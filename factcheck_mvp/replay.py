"""Gravação/reprodução de I/O (HTTP e LLM) para iteração determinística e barata.

Modo (`IO_MODO` no ambiente, padrão `live`; override com `definir_modo` ou
`with modo("replay"):`):
  live    rede de verdade, nada gravado
  record  rede de verdade + grava cada resposta como cassete
  replay  só lê cassetes; se faltar, levanta `ReplayMiss` (subclasse de
          HTTPError — os try/except de rede existentes já tratam)
  (record REUSA cassete existente em vez de chamar de novo: apague o cassete
  p/ regravar. Assim record nunca gasta SerpAPI com query já gravada.)

SerpAPI: toda busca live é contada em eval/serpapi_uso.json; teto opcional
(SERPAPI_ORCAMENTO_RESTANTE / definir_teto_serpapi) -> `OrcamentoSerpAPIEsgotado`;
cota da conta esgotada (HTTP 401/403/429) -> `CotaSerpAPIEsgotada` (troque a chave).

Cassetes: `<CASSETES_DIR>/<sha256>.json` (padrão `eval/cassettes/`). Chave =
sha256(método + URL canônica SEM segredos (api_key/key/token… removidos, query
ordenada) + corpo JSON canônico). Para LLM o corpo inclui modelo, mensagens,
temperatura e max_tokens: mudar o prompt = cassete novo (miss em replay).

API (todas emitem evento `http` de telemetria; `llm_post` também `llm`):
  http_get(url, params=None, **kw) -> Resposta          (sync, ~curl_cffi get)
  http_post(url, json=None, **kw) -> Resposta           (sync, ~curl_cffi post)
  await ahttp_get(url, max_bytes=None, headers=None, timeout=10.0)
        -> Resposta   (async, streaming com teto de bytes, SEM seguir redirect:
                       o chamador vê 3xx + headers["location"] e decide)
  llm_post(url, payload, headers=None, timeout=60, motor="", finalidade=None)
        -> Resposta   (POST de chat/completions ou decisions + evento `llm`)

`Resposta`: status_code, headers (case-insensitive), content (bytes), text,
url (final), json(), raise_for_status(), cache ("live"|"hit"), truncado.
Em live as funções chamam `curl_cffi.requests.get/post/AsyncSession` pelo
atributo do módulo em tempo de chamada, então monkeypatch em
`curl_cffi.requests.get` nos testes continua valendo.
Transporte: curl_cffi (fingerprint TLS de navegador via `impersonate`).
"""
from __future__ import annotations

import base64
import contextvars
import hashlib
import json as _json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import curl_cffi.requests
from curl_cffi.requests.exceptions import RequestException as _CurlRequestException

from . import telemetria

MODOS = ("live", "record", "replay")
_SEGREDOS_QUERY = {"api_key", "apikey", "api-key", "key", "token", "access_token",
                   "secret", "password", "senha", "auth"}
_REDIRECTS = (301, 302, 303, 307, 308)
# Fingerprint TLS de navegador (curl_cffi): dribla bloqueios anti-bot simples.
IMPERSONATE_PADRAO = "chrome"


class HTTPError(_CurlRequestException):
    """Erro de rede/HTTP do transporte (curl_cffi)."""


class ReplayMiss(HTTPError):
    """Modo replay sem cassete para a requisição (rode em record para gravar)."""


class ErroStatusHTTP(HTTPError):
    """raise_for_status() de uma Resposta com status >= 400."""

    def __init__(self, mensagem: str, status_code: int = 0):
        super().__init__(mensagem)
        self.status_code = status_code


# --------------------------------------------------------------------------
# Modo

_modo_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("io_modo", default=None)
_modo_global: Optional[str] = None


def _valida(m: Optional[str]) -> Optional[str]:
    if m is None:
        return None
    m = str(m).strip().lower()
    if m not in MODOS:
        raise ValueError(f"IO_MODO inválido: {m!r} (use {'|'.join(MODOS)})")
    return m


def modo_atual() -> str:
    """Contexto (with modo) > definir_modo() > env IO_MODO > live."""
    return _modo_ctx.get() or _modo_global or _valida(os.environ.get("IO_MODO") or "live") or "live"


def definir_modo(m: Optional[str]) -> None:
    """Override global do processo (None volta a ler IO_MODO)."""
    global _modo_global
    _modo_global = _valida(m)


class modo:
    """`with modo("replay"):` — override só neste contexto (async-safe)."""

    def __init__(self, m: str):
        self.m = _valida(m)
        self._tok = None

    def __enter__(self):
        self._tok = _modo_ctx.set(self.m)
        return self

    def __exit__(self, *a):
        try:
            _modo_ctx.reset(self._tok)
        except (ValueError, RuntimeError):
            _modo_ctx.set(None)
        return False


def dir_cassetes() -> Path:
    d = os.environ.get("CASSETES_DIR")
    return Path(d).expanduser().resolve() if d else telemetria.RAIZ / "eval" / "cassettes"


# --------------------------------------------------------------------------
# Resposta simples (mesma superfície que o código chamador usava do httpx)

class _Cabecalhos(dict):
    """dict com chaves minúsculas; get/[]/in ignoram caixa."""

    def __init__(self, dados: Optional[Dict[str, Any]] = None):
        super().__init__({str(k).lower(): str(v) for k, v in (dados or {}).items()})

    def get(self, k, default=None):
        return super().get(str(k).lower(), default)

    def __getitem__(self, k):
        return super().__getitem__(str(k).lower())

    def __contains__(self, k):
        return super().__contains__(str(k).lower())


_SEM = object()


class Resposta:
    def __init__(self, status_code: int = 200, headers: Optional[Dict[str, Any]] = None,
                 content: bytes = b"", url: str = "", cache: str = "live",
                 truncado: bool = False, _json_pronto: Any = _SEM):
        self.status_code = int(status_code or 0)
        self.headers = _Cabecalhos(headers)
        self.content = content or b""
        self.url = url
        self.cache = cache
        self.truncado = truncado
        self._json_pronto = _json_pronto

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if self._json_pronto is not _SEM:
            return self._json_pronto
        return _json.loads(self.text)

    def raise_for_status(self) -> "Resposta":
        if self.status_code >= 400:
            raise ErroStatusHTTP(f"HTTP {self.status_code} para {url_redigida(self.url)}", self.status_code)
        return self


def _args_curl(kw: Dict[str, Any]) -> Dict[str, Any]:
    """Traduz kwargs estilo-httpx p/ curl_cffi + impersonate padrão."""
    args = dict(kw)
    if "follow_redirects" in args:
        args["allow_redirects"] = args.pop("follow_redirects")
    args.setdefault("impersonate", IMPERSONATE_PADRAO)
    return args


def _de_resposta(r: Any, url: str) -> Resposta:
    """Converte curl_cffi.Response (ou fake de teste) em Resposta."""
    status = getattr(r, "status_code", 200)
    hdrs = getattr(r, "headers", None) or {}
    try:
        hdrs = dict(hdrs)
    except Exception:
        hdrs = {}
    conteudo = getattr(r, "content", None)
    pronto: Any = _SEM
    if not isinstance(conteudo, (bytes, bytearray)):
        # fake sem .content (testes): usa .json() como fonte da verdade
        try:
            pronto = r.json()
            conteudo = _json.dumps(pronto, ensure_ascii=False).encode("utf-8")
        except Exception:
            conteudo = str(getattr(r, "text", "") or "").encode("utf-8")
    final = str(getattr(r, "url", "") or url)
    return Resposta(status if isinstance(status, int) else 200, hdrs, bytes(conteudo), final,
                    cache="live", _json_pronto=pronto)


def _de_httpx(r: Any, url: str) -> Resposta:  # alias de compat (testes antigos)
    return _de_resposta(r, url)


# --------------------------------------------------------------------------
# Chave e cassetes

def _url_com_params(url: str, params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return url
    p = urlsplit(url)
    q = parse_qsl(p.query, keep_blank_values=True) + [(str(k), str(v)) for k, v in params.items()
                                                      if v is not None]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def url_canonica(url: str) -> str:
    """URL sem parâmetros secretos, query ordenada, sem fragmento."""
    try:
        p = urlsplit(url)
        q = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if k.lower() not in _SEGREDOS_QUERY)
        return telemetria.redigir(urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(q), "")))
    except Exception:
        return telemetria.redigir(url)


def url_redigida(url: str) -> str:
    return telemetria.redigir(url or "")


def chave(metodo: str, url: str, corpo: Any = None) -> str:
    base = {"m": metodo.upper(), "u": url_canonica(url),
            "c": corpo if corpo is not None else None}
    bruto = _json.dumps(base, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def _caminho(k: str) -> Path:
    return dir_cassetes() / f"{k}.json"


def _gravar(k: str, metodo: str, url: str, corpo: Any, resp: Resposta, lat_ms: float,
            max_bytes: Optional[int] = None) -> None:
    try:
        try:
            texto = resp.content.decode("utf-8")
            conteudo = {"texto": texto}
        except UnicodeDecodeError:
            conteudo = {"b64": base64.b64encode(resp.content).decode("ascii")}
        hdrs = {k2: v for k2, v in resp.headers.items() if k2 not in ("set-cookie", "authorization")}
        doc = {"versao": 1, "chave": k,
               "req": {"metodo": metodo.upper(), "url": url_canonica(url),
                       "corpo": telemetria.limpar(corpo, limite=100000) if corpo is not None else None},
               "resp": {"status": resp.status_code, "headers": hdrs, "url": url_redigida(resp.url),
                        "truncado": resp.truncado, "max_bytes": max_bytes, **conteudo},
               "latencia_ms": lat_ms, "gravado_em": time.strftime("%Y-%m-%dT%H:%M:%S")}
        pasta = dir_cassetes()
        pasta.mkdir(parents=True, exist_ok=True)
        tmp = pasta / f".{k}.{os.getpid()}.{time.time_ns()}.tmp"
        tmp.write_text(_json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _caminho(k))
    except Exception as e:  # gravar cassete nunca derruba a chamada
        telemetria.evento("erro", onde="replay.gravar", erro=str(e)[:300])


def _ler(k: str, url: str, max_bytes: Optional[int] = None) -> Resposta:
    arq = _caminho(k)
    try:
        doc = _json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReplayMiss(f"replay sem cassete p/ {url_canonica(url)} (chave {k[:12]}; grave com IO_MODO=record)")
    r = doc.get("resp") or {}
    if "b64" in r:
        conteudo = base64.b64decode(r["b64"])
    else:
        conteudo = (r.get("texto") or "").encode("utf-8")
    truncado = bool(r.get("truncado"))
    gravado_max = r.get("max_bytes")
    if max_bytes is not None:
        if truncado and gravado_max and max_bytes > gravado_max:
            raise ReplayMiss(f"cassete truncado em {gravado_max} bytes < max_bytes={max_bytes}; regrave")
        if len(conteudo) > max_bytes:
            conteudo, truncado = conteudo[:max_bytes], True
    return Resposta(r.get("status", 200), r.get("headers") or {}, conteudo,
                    r.get("url") or url, cache="hit", truncado=truncado)


# --------------------------------------------------------------------------
# Orçamento SerpAPI (Free Plan: cota mensal pequena e compartilhada)
#
# Toda chamada LIVE a serpapi.com passa por `_reservar_serpapi()`: contador
# persistido em eval/serpapi_uso.json (SERPAPI_USO_ARQ). Teto OPCIONAL:
# SERPAPI_ORCAMENTO_RESTANTE (desligado se ausente) limita o total do arquivo;
# `definir_teto_serpapi(n)` limita só este processo (eval --max-buscas-serpapi).
# Cache hit (cassete) não conta. Excedeu -> OrcamentoSerpAPIEsgotado + evento
# fallback(onde="serpapi", motivo="orcamento").
# Cota da CONTA esgotada/chave inválida (HTTP 401/403/429 da SerpAPI) ->
# CotaSerpAPIEsgotada + fallback com pedido explícito de nova SERPAPI_KEY.

class OrcamentoSerpAPIEsgotado(HTTPError):
    """Chamada live à SerpAPI recusada: orçamento (arquivo ou teto do processo) esgotado."""


class CotaSerpAPIEsgotada(HTTPError):
    """A SerpAPI recusou a chave (sem buscas na conta / chave inválida): troque SERPAPI_KEY no .env."""


_lock_uso = threading.Lock()
_teto_processo: Optional[int] = None
_usadas_processo = 0
_cota_esgotada = False


def cota_esgotada() -> bool:
    """True se alguma chamada live neste processo viu 401/403/429 da SerpAPI."""
    return _cota_esgotada


def resetar_cota() -> None:
    """Limpa a flag (eval.rodar() chama na entrada; testes usam no fixture)."""
    global _cota_esgotada
    _cota_esgotada = False


def arq_uso_serpapi() -> Optional[Path]:
    """Arquivo do contador. Sob pytest só com SERPAPI_USO_ARQ explícito (não suja o real)."""
    d = os.environ.get("SERPAPI_USO_ARQ")
    if d:
        return Path(d).expanduser().resolve()
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    return telemetria.RAIZ / "eval" / "serpapi_uso.json"


def orcamento_serpapi() -> Optional[int]:
    """Teto total de buscas live no arquivo de uso; None (padrão) = sem teto."""
    v = (os.environ.get("SERPAPI_ORCAMENTO_RESTANTE") or "").strip()
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _ler_uso(arq: Path) -> Dict[str, Any]:
    try:
        d = _json.loads(arq.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {"total_live": 0, "por_dia": {}, "por_run": {}, "chamadas": []}


def uso_serpapi() -> Dict[str, Any]:
    """{usadas, orcamento, restantes, usadas_processo, teto_processo, arquivo}."""
    arq = arq_uso_serpapi()
    usadas = int(_ler_uso(arq).get("total_live", 0)) if arq else 0
    orc = orcamento_serpapi()
    return {"usadas": usadas, "orcamento": orc, "restantes": (max(0, orc - usadas) if orc is not None else None),
            "usadas_processo": _usadas_processo, "teto_processo": _teto_processo,
            "arquivo": str(arq) if arq else None}


def definir_teto_serpapi(n: Optional[int]) -> None:
    """Teto de buscas SerpAPI live neste processo (None = sem teto extra). Zera o contador."""
    global _teto_processo, _usadas_processo
    _teto_processo = n
    _usadas_processo = 0


def _eh_serpapi(url: str) -> bool:
    try:
        h = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    return h == "serpapi.com" or h.endswith(".serpapi.com")


def _eh_llm(url: str) -> bool:
    """URL de LLM (OpenRouter ou local): as únicas que podem ir live com LLM_ONLY_RECORD=1."""
    try:
        h = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    return h in ("localhost", "127.0.0.1", "openrouter.ai") or h.endswith(".openrouter.ai")


def _modo_efetivo(url: str) -> str:
    """Modo vigente p/ esta URL. Com LLM_ONLY_RECORD=1 e modo record, só LLM vai live;
    SerpAPI/páginas ficam em replay (miss em vez de live). Nível 2 do protocolo."""
    m = modo_atual()
    if m == "record" and os.environ.get("LLM_ONLY_RECORD") == "1" and not _eh_llm(url):
        return "replay"
    return m


def _reservar_serpapi(url: str) -> None:
    """Conta 1 busca live ANTES de chamar (conservador: falha de rede também conta)."""
    global _usadas_processo
    arq = arq_uso_serpapi()
    with _lock_uso:
        if _teto_processo is not None and _usadas_processo >= _teto_processo:
            telemetria.fallback("serpapi", "orcamento", escopo="processo", teto=_teto_processo)
            raise OrcamentoSerpAPIEsgotado(
                f"orcamento SerpAPI do processo esgotado ({_usadas_processo}/{_teto_processo})")
        if arq is not None:
            arq.parent.mkdir(parents=True, exist_ok=True)
            with open(str(arq) + ".lock", "w") as lk:
                try:
                    import fcntl
                    fcntl.flock(lk, fcntl.LOCK_EX)  # evals em paralelo (processos) não estouram
                except Exception:
                    pass
                uso = _ler_uso(arq)
                orc = orcamento_serpapi()
                if orc is not None and int(uso.get("total_live", 0)) >= orc:
                    telemetria.fallback("serpapi", "orcamento", escopo="arquivo",
                                        usadas=uso.get("total_live"), orcamento=orc)
                    raise OrcamentoSerpAPIEsgotado(
                        f"orcamento SerpAPI esgotado ({uso.get('total_live')}/{orc}; {arq.name})")
                dia = time.strftime("%Y-%m-%d")
                rid = telemetria.run_atual() or "sem-run"
                q = dict(parse_qsl(urlsplit(url).query)).get("q", "")
                uso["total_live"] = int(uso.get("total_live", 0)) + 1
                uso.setdefault("por_dia", {})[dia] = uso.get("por_dia", {}).get(dia, 0) + 1
                uso.setdefault("por_run", {})[rid] = uso.get("por_run", {}).get(rid, 0) + 1
                uso.setdefault("chamadas", []).append(
                    {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": rid,
                     "engine": dict(parse_qsl(urlsplit(url).query)).get("engine"), "q": q[:120]})
                tmp = arq.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(_json.dumps(uso, ensure_ascii=False, indent=1), encoding="utf-8")
                os.replace(tmp, arq)
        _usadas_processo += 1


def _checar_cota_serpapi(metodo: str, url: str, resp: Resposta, lat: float) -> None:
    """HTTP 401/403/429 da SerpAPI = cota da conta esgotada ou chave inválida: erro explícito
    (não grava cassete, p/ que um record com chave nova busque de novo)."""
    if resp.status_code not in (401, 403, 429):
        return
    global _cota_esgotada
    try:
        detalhe = str((resp.json() or {}).get("error") or "")[:200]
    except Exception:
        detalhe = resp.text[:200]
    msg = (f"SerpAPI recusou a chave (HTTP {resp.status_code}: {detalhe or 'sem detalhe'}). "
           "Cota da conta esgotada ou chave inválida: coloque uma nova SERPAPI_KEY no .env.")
    telemetria.fallback("serpapi", msg, status=resp.status_code)
    _evento_http(metodo, url, resp, lat, "live", msg)
    _cota_esgotada = True
    raise CotaSerpAPIEsgotada(msg)


# --------------------------------------------------------------------------
# Execução comum

def _evento_http(metodo: str, url: str, resp: Optional[Resposta], lat_ms: float, cache: str,
                 erro: Optional[str] = None) -> None:
    telemetria.evento("http", metodo=metodo, url=url_redigida(url),
                      status=(resp.status_code if resp else None),
                      bytes=(len(resp.content) if resp else 0), latencia_ms=round(lat_ms, 1),
                      cache=cache, erro=erro, truncado=(resp.truncado if resp else None),
                      gravado=(modo_atual() == "record" and resp is not None and cache == "live"))


def _do_cassete(m: str, metodo: str, k: str, url: str, max_bytes: Optional[int]) -> Optional[Resposta]:
    """replay: cassete ou ReplayMiss. record: reusa cassete existente (não chama de novo)."""
    if m == "live":
        return None
    t0 = time.perf_counter()
    try:
        resp = _ler(k, url, max_bytes)
    except ReplayMiss as e:
        if m == "replay":
            _evento_http(metodo, url, None, 0.0, "miss", str(e))
            raise
        return None  # record: segue live e grava
    _evento_http(metodo, url, resp, (time.perf_counter() - t0) * 1000, "hit")
    return resp


def _executar(metodo: str, url_chave: str, corpo_chave: Any, ao_vivo: Callable[[], Resposta],
              max_bytes: Optional[int] = None) -> Resposta:
    m = _modo_efetivo(url_chave)
    k = chave(metodo, url_chave, corpo_chave)
    resp = _do_cassete(m, metodo, k, url_chave, max_bytes)
    if resp is not None:
        return resp
    t0 = time.perf_counter()
    try:
        if _eh_serpapi(url_chave):
            _reservar_serpapi(url_chave)
        resp = ao_vivo()
    except Exception as e:
        _evento_http(metodo, url_chave, None, (time.perf_counter() - t0) * 1000, "live",
                     f"{type(e).__name__}: {e}"[:300])
        raise
    lat = (time.perf_counter() - t0) * 1000
    if _eh_serpapi(url_chave):
        _checar_cota_serpapi(metodo, url_chave, resp, lat)
    # 429/5xx são transitórios: gravar congelaria a falha no replay (403/404 seguem gravados).
    if m == "record" and not (resp.status_code == 429 or resp.status_code >= 500):
        _gravar(k, metodo, url_chave, corpo_chave, resp, round(lat, 1), max_bytes)
    _evento_http(metodo, url_chave, resp, lat, "live")
    return resp


def http_get(url: str, params: Optional[Dict[str, Any]] = None, **kw: Any) -> Resposta:
    """GET síncrono (curl_cffi). `params` entram na chave (sem segredos)."""
    def _vivo() -> Resposta:
        args = _args_curl(kw)
        if params is not None:
            args["params"] = params
        return _de_resposta(curl_cffi.requests.get(url, **args), _url_com_params(url, params))
    return _executar("GET", _url_com_params(url, params), None, _vivo)


_limite_lock = threading.Lock()
_ultima_chamada_or = 0.0


def _esperar_limite_openrouter() -> None:
    """Espaça as chamadas VIVAS ao OpenRouter (modelos :free têm ~3 req/min).

    `OPENROUTER_RPM` (padrão 3; 0 desliga). Processo inteiro, seguro entre threads: cada
    chamada reserva o próximo slot sob o lock e dorme fora dele. Cassete não passa por aqui.
    """
    global _ultima_chamada_or
    try:
        rpm = float(os.getenv("OPENROUTER_RPM", "3"))
    except ValueError:
        rpm = 3.0
    if rpm <= 0:
        return
    intervalo = 60.0 / rpm + 1.0  # 1 s de folga contra jitter do relógio do provedor
    with _limite_lock:
        agora = time.monotonic()
        slot = max(agora, _ultima_chamada_or + intervalo)
        _ultima_chamada_or = slot
    espera = slot - time.monotonic()
    if espera > 0:
        telemetria.evento("rate_limit", alvo="openrouter", espera_s=round(espera, 1))
        time.sleep(espera)


def http_post(url: str, json: Any = None, **kw: Any) -> Resposta:  # noqa: A002 (espelha requests)
    """POST síncrono (curl_cffi). Corpo JSON entra na chave (headers não)."""
    def _vivo() -> Resposta:
        args = _args_curl(kw)
        if json is not None:
            args["json"] = json
        if "openrouter.ai" not in url:
            return _de_resposta(curl_cffi.requests.post(url, **args), url)
        # OpenRouter: ritmo limitado e 429 tratado AQUI, para que só a resposta final
        # (nunca um 429) chegue ao cassete.
        for tentativa in range(3):
            _esperar_limite_openrouter()
            r = curl_cffi.requests.post(url, **args)
            if r.status_code != 429 or tentativa == 2:
                return _de_resposta(r, url)
            try:
                espera = float(r.headers.get("retry-after", "") or 0)
            except ValueError:
                espera = 0.0
            espera = min(max(espera, 30.0), 120.0)
            telemetria.evento("rate_limit", alvo="openrouter", http=429, espera_s=espera)
            time.sleep(espera)
        return _de_resposta(r, url)
    return _executar("POST", url, json, _vivo)


async def ahttp_get(url: str, max_bytes: Optional[int] = None, headers: Optional[Dict[str, str]] = None,
                    timeout: float = 10.0, follow_redirects: bool = False, **kw: Any) -> Resposta:
    """GET assíncrono com streaming e teto de bytes; NÃO segue redirect por padrão.

    Semântica p/ os crawlers (aprofundar/descoberta_site): devolve 3xx com
    headers["location"] (o chamador revalida o próximo hop), e o corpo lido até
    `max_bytes` (resp.truncado=True se cortou). Não levanta por status: use
    raise_for_status(). Erros de rede levantam como RequestException (curl_cffi).
    """
    m = _modo_efetivo(url)
    k = chave("GET", url, None)
    resp = _do_cassete(m, "GET", k, url, max_bytes)
    if resp is not None:
        return resp
    t0 = time.perf_counter()
    try:
        if _eh_serpapi(url):
            _reservar_serpapi(url)
        args = _args_curl(kw)
        async with curl_cffi.requests.AsyncSession(timeout=timeout,
                                                  allow_redirects=follow_redirects,
                                                  impersonate=args.pop("impersonate",
                                                                       IMPERSONATE_PADRAO)) as sess:
            async with sess.stream("GET", url, headers=headers, **args) as r:
                buf = b""
                truncado = False
                if r.status_code not in _REDIRECTS:
                    async for chunk in r.aiter_content():
                        if not chunk:
                            continue
                        buf += chunk
                        if max_bytes is not None and len(buf) > max_bytes:
                            truncado = True
                            break
                if max_bytes is not None and len(buf) > max_bytes:
                    buf = buf[:max_bytes]
                try:
                    hdrs = dict(r.headers)
                except Exception:
                    hdrs = {}
                resp = Resposta(r.status_code, hdrs, buf, str(getattr(r, "url", url)),
                                cache="live", truncado=truncado)
    except Exception as e:
        _evento_http("GET", url, None, (time.perf_counter() - t0) * 1000, "live",
                     f"{type(e).__name__}: {e}"[:300])
        raise
    lat = (time.perf_counter() - t0) * 1000
    if _eh_serpapi(url):
        _checar_cota_serpapi("GET", url, resp, lat)
    if m == "record":
        _gravar(k, "GET", url, None, resp, round(lat, 1), max_bytes)
    _evento_http("GET", url, resp, lat, "live")
    return resp


# --------------------------------------------------------------------------
# LLM (chat/completions OpenAI-compatível e Decisions)

# módulo chamador -> finalidade legível (fallback: "modulo.funcao")
_FINALIDADES = {"afirmacoes": "afirmacoes", "padroes_llm": "padroes",
                "juiz_llm.resumir": "juiz-resumo", "juiz_llm.termometro": "juiz",
                "descoberta_site": "decisions-tipo-site", "implicacao": "implicacao"}
_INTERNOS = ("replay", "telemetria", "llm_openrouter")


def _inferir_finalidade() -> str:
    try:
        f = sys._getframe(2)
        while f is not None:
            mod = (f.f_globals.get("__name__") or "").rsplit(".", 1)[-1]
            if mod and mod not in _INTERNOS:
                func = f.f_code.co_name
                return _FINALIDADES.get(f"{mod}.{func}") or _FINALIDADES.get(mod) or f"{mod}.{func}"
            f = f.f_back
    except Exception:
        pass
    return "desconhecida"


def _saida_chat(d: Any) -> str:
    try:
        return ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    except Exception:
        return ""


def llm_post(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None,
             timeout: float = 60, motor: str = "", finalidade: Optional[str] = None,
             extrair: Optional[Callable[[Any], str]] = None) -> Resposta:
    """POST de LLM via http_post + evento `llm` (motor, modelo, finalidade…)."""
    fin = finalidade or telemetria.finalidade_atual() or _inferir_finalidade()
    prompt = payload.get("messages") if isinstance(payload, dict) else None
    if prompt is None and isinstance(payload, dict):
        prompt = {k: v for k, v in payload.items() if k != "model"}
    prompt_txt = _json.dumps(prompt, ensure_ascii=False, sort_keys=True, default=str)
    base = {"motor": motor, "modelo": (payload or {}).get("model"), "finalidade": fin,
            "prompt_sha": telemetria.sha_curto(prompt_txt), "n_chars_prompt": len(prompt_txt)}
    kw: Dict[str, Any] = {"timeout": timeout}
    if headers is not None:
        kw["headers"] = headers
    t0 = time.perf_counter()
    try:
        r = http_post(url, json=payload, **kw)
    except Exception as e:
        telemetria.evento("llm", **base, latencia_ms=round((time.perf_counter() - t0) * 1000, 1),
                          saida=None, erro=f"{type(e).__name__}: {e}"[:300],
                          cache=("miss" if isinstance(e, ReplayMiss) else "live"), status=None)
        raise
    lat = round((time.perf_counter() - t0) * 1000, 1)
    saida, erro = "", None
    if r.status_code >= 400:
        erro = f"HTTP {r.status_code}: {r.text[:300]}"
    else:
        try:
            d = r.json()
            saida = (extrair or _saida_chat)(d)
        except Exception as e:
            erro = f"resposta não-JSON: {e}"[:300]
    telemetria.evento("llm", **base, latencia_ms=lat, saida=saida, erro=erro, cache=r.cache,
                      status=r.status_code)
    return r


# --------------------------------------------------------------------------
# Relógio determinístico (E4 — data de referência do texto)

RELOGIO_URL = "relogio://hoje"
_DATA_ISO_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _hoje_brt_iso() -> str:
    """Data atual em UTC−3 (BRT, sem zoneinfo: UTC menos 3 h) como YYYY-MM-DD."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date().isoformat()


def _data_do_cassete(k: str) -> tuple[Optional[str], bool]:
    """(YYYY-MM-DD gravado ou None, cassete existe). Chave ausente ou data ilegível -> None."""
    try:
        corpo = _ler(k, RELOGIO_URL).json()
    except ReplayMiss:
        return None, False  # sem arquivo (ou arquivo que o replay não consegue ler)
    except ValueError:
        return None, True   # arquivo existe mas o corpo não é JSON
    dado = corpo.get("data") if isinstance(corpo, dict) else None
    if isinstance(dado, str) and _DATA_ISO_RE.fullmatch(dado):
        try:
            datetime.strptime(dado, "%Y-%m-%d")  # rejeita 2026-02-30
            return dado, True
        except ValueError:
            pass
    return None, True


def hoje(contexto: str) -> Optional[str]:
    """Data de referência ("hoje" do texto) como YYYY-MM-DD.

    live → data atual UTC−3. record → cassete válido é reusado; sem cassete válido mede
    ao vivo e grava chave("GET", "relogio://hoje", contexto) = {"data": ...} (um cassete
    ruim é refeito: record grava o que falta). replay → lê o cassete; ausente →
    None + fallback("relogio", "data de referência não gravada"); presente sem data
    válida → None + fallback("relogio", "cassete sem data"). Nunca levanta ReplayMiss
    (o caso roda, só sem E4) e nunca devolve a string "None".
    """
    m = modo_atual()
    if m == "live":
        return _hoje_brt_iso()
    k = chave("GET", RELOGIO_URL, contexto)
    data, existe = _data_do_cassete(k)
    if data is not None:
        return data
    if m == "replay":
        telemetria.fallback("relogio", "cassete sem data" if existe else "data de referência não gravada")
        return None
    data = _hoje_brt_iso()  # record sem cassete válido: mede e grava
    resp = Resposta(200, {"content-type": "application/json"},
                    _json.dumps({"data": data}, ensure_ascii=False).encode("utf-8"),
                    RELOGIO_URL, cache="live")
    _gravar(k, "GET", RELOGIO_URL, contexto, resp, 0.0)
    return data
