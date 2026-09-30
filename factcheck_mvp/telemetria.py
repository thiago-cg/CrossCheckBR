"""Telemetria estruturada por execução (run): trace JSONL + resultado.json.

Feita para um agente (Claude Code) rodar o pipeline, ler o que aconteceu e
ajustar de forma iterativa. Contrato estável (outras fases dependem dele):

    rid = iniciar_run({"entrada": ...})     # ou: with run(meta) as rid: ...
    evento("etapa", nome="afirmacoes", status="ok", detalhe="...")
    with span("deep-crawl", n=3): ...       # também `async with span(...)`
    finalizar_run(relatorio.model_dump(mode="json"))

Arquivos: `<TELEMETRIA_DIR>/<run_id>/trace.jsonl` (1 evento por linha:
`{"ts", "t_rel_ms", "tipo", "dados"}`) e `resultado.json` (relatório final +
métricas agregadas). Estado em contextvars: seguro p/ asyncio e
`asyncio.to_thread` (a thread herda o run). Sem run ativo, `evento` é no-op.

Ambiente: `TELEMETRIA=0` desliga; `TELEMETRIA_DIR` (padrão `runs/` na raiz do
projeto). Sob pytest, desligada salvo `TELEMETRIA` explícita (não suja runs/).

Tipos de evento padronizados (campos em `dados`; docs/TELEMETRIA.md):
  run_inicio  meta (entrada, tipo, config)
  run_meta    meta extra de um iniciar_run aninhado (ex.: eval abriu o run antes)
  run_fim     dur_ms, ok, erro, nivel
  etapa       nome, status (ok|parcial|falha|pulada), detalhe, n_fontes
  llm         motor, modelo, finalidade, latencia_ms, prompt_sha, n_chars_prompt,
              saida (truncada), erro, cache (hit|miss|live), status
  http        metodo, url (redigida), status, bytes, latencia_ms, cache, erro
  fonte       url, estagio, decisao (mantida|descartada), motivo
  fallback    onde, motivo
  sinal       motor, rotulo, valor, confianca, direcao, peso
  decisao     nivel, score, sinais, why
  erro        onde, erro
  span_inicio / span_fim   nome, (dur_ms, ok, erro no fim)

Segredos (api_key=, key=, token, /bot<token>/, Authorization, sk-...) são
redigidos e strings longas truncadas ANTES de gravar. Nunca levanta: falha de
disco vira silêncio (telemetria não pode derrubar o pipeline).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

RAIZ = Path(__file__).resolve().parent.parent
LIMITE_STR = 2000  # truncamento padrão de strings gravadas

TIPOS = ("run_inicio", "run_meta", "run_fim", "etapa", "llm", "http", "fonte", "fallback",
         "sinal", "decisao", "erro", "span_inicio", "span_fim")


# --------------------------------------------------------------------------
# Configuração (lida a cada chamada: monkeypatch/env mudam em runtime)

def habilitada() -> bool:
    v = os.environ.get("TELEMETRIA")
    if v is None:
        # pytest: não grava runs/ a menos que o teste peça explicitamente
        return "PYTEST_CURRENT_TEST" not in os.environ
    return v.strip().lower() not in ("0", "false", "off", "nao", "não", "")


def diretorio() -> Path:
    d = os.environ.get("TELEMETRIA_DIR")
    return Path(d).expanduser().resolve() if d else RAIZ / "runs"


# --------------------------------------------------------------------------
# Redação de segredos + truncamento

_SEGREDO_PARAM = r"(?:api_key|apikey|api-key|key|token|access_token|secret|password|senha)"
_RX_REDACAO = [
    # query string: ?api_key=XYZ / &key=XYZ
    (re.compile(r"(?i)([?&]" + _SEGREDO_PARAM + r"=)[^&\s\"'#]+"), r"\1REDACTED"),
    # texto/JSON: api_key: "XYZ", "token": "XYZ", key=XYZ
    (re.compile(r"(?i)(\b(?:api_key|apikey|access_token|secret|password|senha|token)\b[\"']?\s*[:=]\s*[\"']?)"
                r"[^\s\"'&,}]+"), r"\1REDACTED"),
    (re.compile(r"(?i)(\bkey=)[^&\s\"'#]+"), r"\1REDACTED"),
    # Telegram: /bot<id>:<token>/
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot<REDACTED>"),
    # Authorization: Bearer xxx (header ou JSON)
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(bearer\s+)?[^\s\"',}]+"), r"\1\2REDACTED"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer REDACTED"),
    # chaves no formato sk-... (OpenRouter/OpenAI)
    (re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9_-]{16,}"), "sk-REDACTED"),
]
_ENV_SEGREDOS = ("SERPAPI_KEY", "OPENROUTER_API_KEY", "TELEGRAM_TOKEN", "UNSLOTH_API_KEY")


def redigir(texto: str) -> str:
    """Remove segredos conhecidos de uma string (padrões + valores do .env)."""
    if not texto:
        return texto
    s = str(texto)
    for nome in _ENV_SEGREDOS:
        val = os.environ.get(nome) or ""
        if len(val) >= 8 and val in s:
            s = s.replace(val, "REDACTED")
    for rx, rep in _RX_REDACAO:
        s = rx.sub(rep, s)
    return s


def truncar(texto: str, limite: int = LIMITE_STR) -> str:
    if texto is None or len(texto) <= limite:
        return texto
    return texto[:limite] + f"…[+{len(texto) - limite} chars]"


def limpar(obj: Any, limite: int = LIMITE_STR, _prof: int = 0) -> Any:
    """Serializável + redigido + truncado (recursivo). Nunca levanta."""
    try:
        if _prof > 8:
            return truncar(redigir(str(obj)), 200)
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            return truncar(redigir(obj), limite)
        if isinstance(obj, bytes):
            return f"<{len(obj)} bytes>"
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return {str(k): limpar(v, limite, _prof + 1) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [limpar(v, limite, _prof + 1) for v in list(obj)[:500]]
        return truncar(redigir(str(obj)), limite)
    except Exception:
        return "<nao-serializavel>"


def sha_curto(obj: Any) -> str:
    bruto = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(bruto.encode("utf-8", "ignore")).hexdigest()[:12]


# --------------------------------------------------------------------------
# Estado do run

class _Run:
    def __init__(self, run_id: str, pasta: Path, meta: Dict[str, Any]):
        self.id = run_id
        self.pasta = pasta
        self.meta = meta
        self.t0 = time.perf_counter()
        self.inicio = datetime.now(timezone.utc).isoformat()
        self.lock = threading.Lock()
        self.profundidade = 0  # iniciar_run aninhado reusa o run externo
        self.token: Optional[contextvars.Token] = None
        self.resultado: Optional[Dict[str, Any]] = None
        self.erro: Optional[str] = None


_run_ctx: contextvars.ContextVar[Optional[_Run]] = contextvars.ContextVar("telemetria_run", default=None)
_finalidade_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("telemetria_finalidade",
                                                                               default=None)
_ultimo_run_id: Optional[str] = None


def run_atual() -> Optional[str]:
    r = _run_ctx.get()
    return r.id if r else None


def ultimo_run_id() -> Optional[str]:
    """Último run finalizado neste processo (útil p/ CLI)."""
    return _ultimo_run_id


def _novo_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def iniciar_run(meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Abre um run no contexto atual e devolve o run_id (None se desligada).

    Reentrante: se já há run ativo (ex.: eval abriu antes do pipeline), não
    cria outro — agrega a meta e devolve o mesmo id.
    """
    if not habilitada():
        return None
    atual = _run_ctx.get()
    if atual is not None:
        atual.profundidade += 1
        evento("run_meta", **(meta or {}))
        return atual.id
    try:
        rid = _novo_id()
        pasta = diretorio() / rid
        pasta.mkdir(parents=True, exist_ok=True)
        r = _Run(rid, pasta, dict(meta or {}))
        r.token = _run_ctx.set(r)
        evento("run_inicio", **(meta or {}))
        return rid
    except Exception:
        return None


def finalizar_run(resultado: Optional[Dict[str, Any]] = None, erro: Optional[str] = None) -> Optional[str]:
    """Fecha o run: grava run_fim + resultado.json. Aninhado: só guarda o resultado."""
    global _ultimo_run_id
    r = _run_ctx.get()
    if r is None:
        return None
    if resultado is not None:
        r.resultado = resultado
    if erro:
        r.erro = erro
    if r.profundidade > 0:
        r.profundidade -= 1
        return r.id
    dur = round((time.perf_counter() - r.t0) * 1000, 1)
    nivel = (r.resultado or {}).get("propensao") if isinstance(r.resultado, dict) else None
    evento("run_fim", dur_ms=dur, ok=r.erro is None, erro=r.erro, nivel=nivel)
    try:
        eventos = _ler_trace(r.pasta / "trace.jsonl")
        doc = {"run_id": r.id, "inicio": r.inicio, "fim": datetime.now(timezone.utc).isoformat(),
               "dur_ms": dur, "erro": r.erro, "meta": limpar(r.meta),
               "relatorio": limpar(r.resultado, limite=20000) if r.resultado is not None else None,
               "metricas": metricas_de_eventos(eventos)}
        (r.pasta / "resultado.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str),
                                                 encoding="utf-8")
    except Exception:
        pass
    try:
        if r.token is not None:
            _run_ctx.reset(r.token)
        else:
            _run_ctx.set(None)
    except (ValueError, RuntimeError):
        _run_ctx.set(None)  # finalizado em outro contexto: só desliga
    _ultimo_run_id = r.id
    return r.id


class run:
    """`with run(meta) as rid:` — erro não tratado vai p/ run_fim e resultado.json."""

    def __init__(self, meta: Optional[Dict[str, Any]] = None):
        self.meta = meta or {}
        self.id: Optional[str] = None

    def __enter__(self) -> Optional[str]:
        self.id = iniciar_run(self.meta)
        return self.id

    def __exit__(self, et, ev, tb):
        finalizar_run(None, erro=(f"{et.__name__}: {ev}"[:500] if et else None))
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, et, ev, tb):
        return self.__exit__(et, ev, tb)


def anexar_resultado(resultado: Dict[str, Any]) -> None:
    """Guarda o relatório no run ativo sem fechar (gravado no finalizar_run)."""
    r = _run_ctx.get()
    if r is not None:
        r.resultado = resultado


# --------------------------------------------------------------------------
# Eventos

def evento(tipo: str, /, **dados: Any) -> None:
    """Grava 1 linha no trace do run ativo. Sem run/desligada: no-op."""
    r = _run_ctx.get()
    if r is None:
        return
    try:
        linha = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "t_rel_ms": round((time.perf_counter() - r.t0) * 1000, 1),
                 "tipo": tipo, "dados": limpar(dados)}
        txt = json.dumps(linha, ensure_ascii=False, default=str)
        with r.lock:
            with open(r.pasta / "trace.jsonl", "a", encoding="utf-8") as f:
                f.write(txt + "\n")
    except Exception:
        pass


def fallback(onde: str, motivo: Any = "", /, **extra: Any) -> None:
    """Atalho: todo fallback (silencioso ou não) deixa rastro explícito."""
    evento("fallback", onde=onde, motivo=str(motivo)[:500], **extra)


class span:
    """Mede um trecho: `with span("x"):` ou `async with span("x"):`.

    Grava span_inicio e span_fim (dur_ms, ok, erro). `sp.dados[...] = v` dentro
    do bloco acrescenta campos ao span_fim.
    """

    def __init__(self, nome: str, /, **dados: Any):
        self.nome = nome
        self.dados: Dict[str, Any] = dict(dados)
        self.t0 = 0.0

    def __enter__(self) -> "span":
        self.t0 = time.perf_counter()
        evento("span_inicio", nome=self.nome, **self.dados)
        return self

    def __exit__(self, et, ev, tb):
        evento("span_fim", nome=self.nome, dur_ms=round((time.perf_counter() - self.t0) * 1000, 1),
               ok=et is None, erro=(f"{et.__name__}: {ev}"[:300] if et else None), **self.dados)
        return False

    async def __aenter__(self) -> "span":
        return self.__enter__()

    async def __aexit__(self, et, ev, tb):
        return self.__exit__(et, ev, tb)


aspan = span  # alias explícito p/ uso async


class finalidade:
    """`with finalidade("juiz"):` rotula as chamadas LLM feitas no bloco."""

    def __init__(self, nome: str):
        self.nome = nome
        self._tok = None

    def __enter__(self):
        self._tok = _finalidade_ctx.set(self.nome)
        return self

    def __exit__(self, *a):
        try:
            _finalidade_ctx.reset(self._tok)
        except (ValueError, RuntimeError):
            _finalidade_ctx.set(None)
        return False


def finalidade_atual() -> Optional[str]:
    return _finalidade_ctx.get()


# --------------------------------------------------------------------------
# Leitura / métricas / resumo (para humanos e agentes)

def _ler_trace(caminho: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if linha:
                    try:
                        out.append(json.loads(linha))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


def metricas_de_eventos(eventos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Agregados do run: contagem por tipo, LLM, fallbacks, descartes, duração."""
    por_tipo = Counter(e.get("tipo") for e in eventos)
    llm = [e["dados"] for e in eventos if e.get("tipo") == "llm"]
    http = [e["dados"] for e in eventos if e.get("tipo") == "http"]
    fbs = [e["dados"] for e in eventos if e.get("tipo") == "fallback"]
    desc = [e["dados"] for e in eventos if e.get("tipo") == "fonte"
            and (e.get("dados") or {}).get("decisao") == "descartada"]
    fim = [e["dados"] for e in eventos if e.get("tipo") == "run_fim"]
    dec = [e["dados"] for e in eventos if e.get("tipo") == "decisao"]
    dur = fim[-1].get("dur_ms") if fim else (eventos[-1].get("t_rel_ms") if eventos else 0)
    return {
        "eventos_por_tipo": dict(por_tipo),
        "n_llm": len(llm),
        "n_llm_erro": sum(1 for d in llm if d.get("erro")),
        "llm_latencia_total_ms": round(sum(float(d.get("latencia_ms") or 0) for d in llm), 1),
        "llm_por_finalidade": dict(Counter(str(d.get("finalidade")) for d in llm)),
        "n_http": len(http),
        "http_cache": dict(Counter(str(d.get("cache")) for d in http)),
        "n_fallbacks": len(fbs),
        "fallbacks_por_onde": dict(Counter(str(d.get("onde")) for d in fbs)),
        "descartes_por_motivo": dict(Counter(str(d.get("motivo")) for d in desc)),
        "n_erros": por_tipo.get("erro", 0),
        "dur_ms": dur,
        "nivel": (dec[-1].get("nivel") if dec else (fim[-1].get("nivel") if fim else None)),
    }


def _resolver(run_id: str) -> Optional[Path]:
    base = diretorio()
    if not base.exists():
        return None
    if run_id in ("last", "ultimo", "último", ""):
        pastas = sorted((p for p in base.iterdir() if (p / "trace.jsonl").exists()), key=lambda p: p.name)
        return pastas[-1] if pastas else None
    p = base / run_id
    if p.exists():
        return p
    # prefixo único (ex.: só o timestamp)
    cands = sorted(p for p in base.iterdir() if p.name.startswith(run_id))
    return cands[-1] if cands else None


def ler_run(run_id: str = "last") -> Dict[str, Any]:
    """{run_id, dir, eventos[], resultado|None, metricas}. KeyError se não existir."""
    pasta = _resolver(run_id)
    if pasta is None:
        raise KeyError(f"run não encontrado: {run_id} (em {diretorio()})")
    eventos = _ler_trace(pasta / "trace.jsonl")
    resultado = None
    try:
        resultado = json.loads((pasta / "resultado.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"run_id": pasta.name, "dir": str(pasta), "eventos": eventos, "resultado": resultado,
            "metricas": (resultado or {}).get("metricas") or metricas_de_eventos(eventos)}


def listar_runs(n: int = 10) -> List[Dict[str, Any]]:
    """Runs mais recentes primeiro: id, data, entrada, nível, fallbacks, duração."""
    base = diretorio()
    if not base.exists():
        return []
    pastas = sorted((p for p in base.iterdir() if (p / "trace.jsonl").exists()),
                    key=lambda p: p.name, reverse=True)[: max(1, n)]
    out = []
    for p in pastas:
        try:
            r = ler_run(p.name)
        except KeyError:
            continue
        ini = next((e for e in r["eventos"] if e.get("tipo") == "run_inicio"), {})
        meta = (r["resultado"] or {}).get("meta") or ini.get("dados") or {}
        m = r["metricas"]
        out.append({"run_id": p.name, "data": ini.get("ts", ""), "entrada": meta.get("entrada", ""),
                    "caso": meta.get("caso_id"), "nivel": m.get("nivel"),
                    "n_fallbacks": m.get("n_fallbacks", 0), "dur_ms": m.get("dur_ms"),
                    "completo": r["resultado"] is not None})
    return out


def _fmt_ms(ms: Any) -> str:
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return "?"
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"


def resumir_run(run_id: str = "last", max_itens: int = 12) -> str:
    """Resumo compacto (texto) de um run, pensado p/ um agente LLM ler."""
    r = ler_run(run_id)
    ev = r["eventos"]
    m = r["metricas"]
    ini = next((e["dados"] for e in ev if e.get("tipo") == "run_inicio"), {})
    fim = next((e["dados"] for e in ev if e.get("tipo") == "run_fim"), {})
    linhas: List[str] = []
    linhas.append(f"RUN {r['run_id']} | dur {_fmt_ms(m.get('dur_ms'))} | nivel={m.get('nivel')} "
                  f"| ok={fim.get('ok', '?')}{' | ERRO: ' + str(fim.get('erro')) if fim.get('erro') else ''}")
    extra = {k: v for k, v in ini.items() if k not in ("entrada", "config")}
    linhas.append(f"entrada: {truncar(str(ini.get('entrada', '')), 200)!r} {extra if extra else ''}".rstrip())
    if ini.get("config"):
        linhas.append("config: " + ", ".join(f"{k}={v}" for k, v in (ini.get("config") or {}).items()))

    linhas.append("ETAPAS (dur = desde o evento de etapa anterior)")
    t_ant = 0.0
    for e in ev:
        if e.get("tipo") != "etapa":
            continue
        d = e["dados"]
        t = float(e.get("t_rel_ms") or 0)
        linhas.append(f"  [{d.get('status', '?'):<7}] {d.get('nome', '?'):<18} +{_fmt_ms(t - t_ant):>6}  "
                      f"{truncar(str(d.get('detalhe', '')), 220)}")
        t_ant = t

    llm = [e["dados"] for e in ev if e.get("tipo") == "llm"]
    if llm:
        linhas.append(f"LLM ({len(llm)} chamadas, {_fmt_ms(m.get('llm_latencia_total_ms'))} total, "
                      f"{m.get('n_llm_erro', 0)} com erro)")
        for d in llm[:max_itens]:
            st = f"ERRO {truncar(str(d.get('erro')), 140)}" if d.get("erro") else \
                f"-> {truncar(' '.join(str(d.get('saida') or '').split()), 140)!r}"
            linhas.append(f"  {str(d.get('finalidade')):<16} {d.get('motor', '?')}:{d.get('modelo', '?')} "
                          f"{_fmt_ms(d.get('latencia_ms'))} cache={d.get('cache')} {st}")
        if len(llm) > max_itens:
            linhas.append(f"  … +{len(llm) - max_itens} chamadas")

    http = [e["dados"] for e in ev if e.get("tipo") == "http"]
    if http:
        err = [d for d in http if d.get("erro") or (d.get("status") or 0) >= 400]
        linhas.append(f"HTTP ({len(http)} req, cache={m.get('http_cache')}, {len(err)} com erro)")
        for d in err[:max_itens]:
            linhas.append(f"  {d.get('metodo')} {truncar(str(d.get('url')), 100)} status={d.get('status')} "
                          f"cache={d.get('cache')} erro={truncar(str(d.get('erro')), 120)}")

    fbs = [e["dados"] for e in ev if e.get("tipo") == "fallback"]
    if fbs:
        linhas.append(f"FALLBACKS ({len(fbs)})")
        grupos: Dict[str, List[str]] = {}
        for d in fbs:
            grupos.setdefault(str(d.get("onde")), []).append(str(d.get("motivo", "")))
        for onde, motivos in grupos.items():
            top = Counter(motivos).most_common(2)
            linhas.append(f"  {onde} x{len(motivos)}: " +
                          " | ".join(f"{truncar(mo, 140)}" + (f" (x{c})" if c > 1 else "") for mo, c in top))

    fontes = [e["dados"] for e in ev if e.get("tipo") == "fonte"]
    if fontes:
        mant = [d for d in fontes if d.get("decisao") == "mantida"]
        desc = [d for d in fontes if d.get("decisao") == "descartada"]
        linhas.append(f"FONTES ({len(mant)} mantidas, {len(desc)} descartadas)")
        for d in mant[:max_itens]:
            linhas.append(f"  + [{d.get('estagio')}] {truncar(str(d.get('url')), 110)} {d.get('motivo') or ''}".rstrip())
        por_motivo: Dict[str, List[str]] = {}
        for d in desc:
            por_motivo.setdefault(f"{d.get('estagio')}: {d.get('motivo')}", []).append(str(d.get("url")))
        for mot, urls in por_motivo.items():
            linhas.append(f"  - {truncar(mot, 120)} x{len(urls)}: " + ", ".join(truncar(u, 70) for u in urls[:3]))

    sinais = [e["dados"] for e in ev if e.get("tipo") == "sinal"]
    if sinais:
        linhas.append(f"SINAIS ({len(sinais)}) [direcao +1 = eleva propensão a desinformação]")
        for d in sinais[:max_itens * 2]:
            linhas.append(f"  dir={d.get('direcao')!s:>5} peso={d.get('peso')!s:<5} conf={d.get('confianca')!s:<5} "
                          f"{d.get('motor')}: {truncar(str(d.get('rotulo')), 90)} = {truncar(str(d.get('valor')), 60)}")

    decs = [e["dados"] for e in ev if e.get("tipo") == "decisao"]
    for d in decs[-1:]:
        linhas.append(f"DECISAO nivel={d.get('nivel')} (agregador={d.get('nivel_agregador')}, "
                      f"score={d.get('score')}) travas={d.get('travas')}")
        if d.get("why"):
            linhas.append(f"  why: {truncar(str(d.get('why')), 400)}")

    erros = [e["dados"] for e in ev if e.get("tipo") == "erro"]
    for d in erros[:max_itens]:
        linhas.append(f"ERRO {d.get('onde')}: {truncar(str(d.get('erro')), 300)}")
    linhas.append(f"trace: {Path(r['dir']) / 'trace.jsonl'}")
    return "\n".join(linhas)


def eventos_de(run_id: str, tipos: Optional[List[str]] = None) -> Iterator[Dict[str, Any]]:
    """Eventos crus de um run, filtrados por tipo (None = todos)."""
    for e in ler_run(run_id)["eventos"]:
        if not tipos or e.get("tipo") in tipos:
            yield e
