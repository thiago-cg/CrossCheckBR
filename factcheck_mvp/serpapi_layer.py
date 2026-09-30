"""Camada 0 — descoberta fresca via SerpAPI (Google tradicional ou Google News).

Dois motores mantidos (SERP_ENGINE): `google` (orgânico, padrão) e
`google_news` (legado). Estratégias estáticas no orgânico (SERP_ESTRATEGIA):
`simples` (crua + dirigida por vocabulário) e `avancada` (crua + 1 filtro
`site:` com as agências de checagem do catálogo). `ambas` = união (p/ A/B).
`agente` (padrão) é orquestrado em `agente.py` e usa as mesmas peças daqui.
Toda query tem no máximo 32 palavras (limite do Google; operadores contam).
Agências de checagem: derivadas do catálogo (`agencias_checagem`), nunca lista fixa.
Sem chave: tudo degrada com elegância (retorna None; pipeline segue no índice).
Sem denylist: filtragem só por relevância nas etapas seguintes.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import config, replay, telemetria
from .catalogo import Catalogo

log = logging.getLogger("factcheck.serpapi")

ENGINES = ("google", "google_news", "google_scholar")
RESULT_KEY = {"google_news": "news_results", "google": "organic_results",
              "google_scholar": "organic_results"}

# Limite de termos do Google: a partir da 33ª palavra o resto é ignorado (e um
# filtro `site:` cortado no meio zera a perna). Operadores (`OR`, `site:x`) contam.
MAX_PALAVRAS_QUERY = 32
# Palavras da consulta mantidas na query restrita às agências (o resto é `site:`).
MAX_PALAVRAS_CONSULTA_SITES = 8

# Agência de checagem = portal CURADO do catálogo com `tipo: checagem` cuja editoria
# declara verificação (checagem/verificação/boato/desinformação/fake/farsa). Um campo
# booleano `agencia_checagem` no portal, se existir, vence a heurística. Portais de
# `tipo: checagem` sem editoria de verificação (ex.: cobertura anti-golpes de um site
# de finanças, canal de vídeo institucional) não entram: o review (B1) mostrou que
# contá-los como checagem faz notícia comum passar por checagem.
_EDITORIA_VERIFICACAO = re.compile(r"checag|verifica|boato|desinforma|fake|farsa", re.I)
_SITE_VALIDO = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}(/[a-z0-9._~%/-]*)?$")
_cat_padrao: Optional[Catalogo] = None


def _catalogo_padrao() -> Catalogo:
    """Catálogo do disco, carregado uma vez (agências curadas só mudam por curadoria humana)."""
    global _cat_padrao
    if _cat_padrao is None:
        try:
            _cat_padrao = Catalogo.carregar()
        except Exception as e:  # catálogo ausente/corrompido: sem agências, sem quebrar a busca
            telemetria.fallback("serpapi.agencias", f"catálogo indisponível: {type(e).__name__}: {e}")
            return Catalogo([])
    return _cat_padrao


def eh_agencia_checagem(portal: Optional[Dict[str, Any]], catalogo: Catalogo) -> bool:
    if not portal or portal.get("tipo") != "checagem" or not catalogo.eh_curado(portal):
        return False
    explicito = portal.get("agencia_checagem")
    if isinstance(explicito, bool):
        return explicito
    return any(_EDITORIA_VERIFICACAO.search(str(e or "")) for e in (portal.get("editorias") or []))


def _extras() -> List[str]:
    """Hosts de SERP_SITES_EXTRAS (vírgula). Aceita URL completa: vira host (sem scheme/porta/path)."""
    from urllib.parse import urlparse as _up
    out = []
    for h in (getattr(config, "SERP_SITES_EXTRAS", "") or "").split(","):
        h = (h or "").strip().lower()
        if "://" in h or "/" in h:
            try:
                h = _up(h if "://" in h else f"https://{h}").hostname or ""
            except Exception:
                h = ""
        else:
            h = h.split(":")[0].split("?")[0]  # sem porta/query
        if h.startswith("www."):
            h = h[4:]  # removeprefix manual (lstrip corrompe: tira charset, não prefixo)
        if h:
            out.append(h)
    return out


def agencias_checagem(catalogo: Optional[Catalogo] = None) -> List[Dict[str, str]]:
    """Agências de checagem DERIVADAS do catálogo (sem lista fixa de hosts).

    -> [{id, nome, host, path, site}], `site` = host (agência dedicada) ou host+caminho
    (seção de um portal amplo, ex. g1.globo.com/fato-ou-fake). Ordem: domínio principal de
    cada agência (ordem do catálogo), depois os aliases, depois SERP_SITES_EXTRAS.
    """
    from .catalogo import _host_caminho
    cat = catalogo if catalogo is not None else _catalogo_padrao()
    principais: List[Dict[str, str]] = []
    aliases: List[Dict[str, str]] = []
    for p in cat.portais:
        if not eh_agencia_checagem(p, cat):
            continue
        for k, alvo in enumerate([p.get("homepage", "")] + list(p.get("aliases") or [])):
            host, caminho = _host_caminho(alvo)
            if not host:
                continue
            site = host if caminho == "/" else host + caminho.rstrip("/")
            (principais if k == 0 else aliases).append(
                {"id": p.get("id", ""), "nome": p.get("nome", ""), "host": host, "path": caminho, "site": site})
    extras = [{"id": f"extra:{h}", "nome": h, "host": h, "path": "/", "site": h} for h in _extras()]
    out, vistos = [], set()
    for a in principais + aliases + extras:
        if a["site"] not in vistos:
            vistos.add(a["site"])
            out.append(a)
    return out


def sites_checagem(catalogo: Optional[Catalogo] = None) -> List[Dict[str, str]]:
    """Compat: mesmo que `agencias_checagem` (cada item tem `host` e `site`)."""
    return agencias_checagem(catalogo)


def eh_secao_checagem(url: str, catalogo: Optional[Catalogo] = None) -> bool:
    """URL é de agência de checagem? Agência dedicada: qualquer caminho do host;
    seção de portal amplo (g1/fato-ou-fake, estadão/estadao-verifica…): só sob o caminho
    da seção (por segmento: /fato-ou-fake-x/ não vale)."""
    from .catalogo import _host_caminho
    host, caminho = _host_caminho(url or "")
    if not host or not (url or "").strip():
        return False
    for a in agencias_checagem(catalogo):
        if host != a["host"] and not host.endswith("." + a["host"]):
            continue
        if caminho.startswith(a["path"]):
            return True
    return False


def limitar_palavras(texto: str, n: int = MAX_PALAVRAS_QUERY) -> str:
    return " ".join((texto or "").split()[: max(0, n)])


def n_palavras(q: str) -> int:
    return len((q or "").split())


def _base(q: str, engine: str) -> Dict[str, Any]:
    return {"q": limitar_palavras(q), "hl": "pt-br", "gl": "br", "num": 10, "engine": engine}


def query_agencias(consulta: str, agencias: Optional[List[Dict[str, Any]]] = None,
                   max_palavras: int = MAX_PALAVRAS_QUERY,
                   max_palavras_consulta: int = MAX_PALAVRAS_CONSULTA_SITES) -> Tuple[Dict[str, Any], List[str]]:
    """Consulta + `(site:a OR site:b …)` das agências, com no máximo `max_palavras` palavras.

    Evidência live (23/09/2026): cadeia `site:host inurl:path` em OR retorna zero; `site:` puro
    (inclusive host/caminho) funciona. A consulta fica com até `max_palavras_consulta` palavras
    e entram tantas agências quantas couberem (k agências = 2k-1 palavras), na ordem de
    `agencias_checagem`. -> (query, sites que ficaram de fora).
    """
    agencias = agencias if agencias is not None else agencias_checagem()
    sites = []
    for a in agencias:
        s = str(a.get("site") or a.get("host") or "").strip().lower()
        if _SITE_VALIDO.match(s) and s not in sites:
            sites.append(s)
    palavras = (consulta or "").split()[: max(1, min(max_palavras_consulta, max_palavras - 1))]
    k = max(0, (max_palavras - len(palavras) + 1) // 2)
    usados, fora = sites[:k], sites[k:]
    q = " ".join(palavras)
    if usados:
        q = f"{q} ({' OR '.join('site:' + s for s in usados)})"
    return _base(q, "google"), fora


def construir_queries_news(afirmacao: str) -> List[Dict[str, Any]]:
    """Legado: 2 queries no Google News (neutra + dirigida a checadores)."""
    curta = afirmacao.strip()[:200]
    sufixo = " é verdade OR é falso OR checagem"
    return [
        {"q": limitar_palavras(curta), "hl": "pt-br", "gl": "br", "engine": "google_news"},
        {"q": limitar_palavras(curta, MAX_PALAVRAS_QUERY - n_palavras(sufixo)) + sufixo,
         "hl": "pt-br", "gl": "br", "engine": "google_news"},
    ]


def construir_queries_simples(afirmacao: str) -> List[Dict[str, Any]]:
    """Orgânico sem operadores de site: crua + dirigida leve."""
    curta = afirmacao.strip()[:200]
    sufixo = " é verdade OR é falso OR checagem OR fake"
    return [
        _base(curta, "google"),
        _base(limitar_palavras(curta, MAX_PALAVRAS_QUERY - n_palavras(sufixo)) + sufixo, "google"),
    ]


def construir_queries_avancada(afirmacao: str,
                               sites: Optional[List[Dict[str, Any]]] = None
                               ) -> List[Dict[str, Any]]:
    """Orgânico em 2 buscas: crua (recall) + a mesma consulta restrita às agências de checagem
    do catálogo (`query_agencias`, ≤ 32 palavras). Os paths de seção entram como `site:host/path`."""
    curta = afirmacao.strip()[:200]
    return [_base(curta, "google"), query_agencias(curta, sites)[0]]


def construir_queries(afirmacao: str, engine: Optional[str] = None,
                      estrategia: Optional[str] = None) -> List[Dict[str, Any]]:
    """Roteador: mantém os dois motores; estratégia só vale no orgânico."""
    eng = (engine if engine is not None else
           getattr(config, "SERP_ENGINE", "google_news") or "google_news")
    eng = str(eng).strip().lower()
    if eng not in ENGINES:
        eng = "google"
    if eng == "google_news":
        return construir_queries_news(afirmacao)
    if eng == "google_scholar":
        return [{"q": limitar_palavras(afirmacao.strip()[:200]), "hl": "pt", "num": 5,
                 "engine": "google_scholar"}]
    est = (estrategia if estrategia is not None else
           getattr(config, "SERP_ESTRATEGIA", "avancada") or "avancada")
    est = str(est).strip().lower()
    if est == "agente":
        raise ValueError("agente é orquestração pipeline-level (use agente.onda1/onda_extra), "
                         "não query estática da camada")
    if est == "simples":
        return construir_queries_simples(afirmacao)
    if est == "ambas":
        uniao, vistas = [], set()
        for q in construir_queries_simples(afirmacao) + construir_queries_avancada(afirmacao):
            if q["q"] not in vistas:
                vistas.add(q["q"])
                uniao.append(q)
        return uniao
    return construir_queries_avancada(afirmacao)


@dataclass
class ResultadoBusca:
    """Resultado de UMA chamada (sem estado compartilhado: seguro com requisições concorrentes)."""
    payload: Optional[Dict[str, Any]]
    motivo: str          # ok | vazio | cap | erro | sem_chave
    cache: bool = False  # veio do cache em memória do cliente (não gastou busca)


class SerpAPIClient:
    def __init__(self, api_key: str | None = None, ttl: int | None = None, timeout: int = 25,
                 cache_max: int = 200, engine: str | None = None):
        # None = usa env; "" explícito = força desligado (testes offline)
        self.api_key = config.SERPAPI_KEY if api_key is None else api_key
        self.ttl = config.CACHE_SERPAPI_TTL if ttl is None else ttl
        self.timeout = timeout
        self.engine_default = (engine or getattr(config, "SERP_ENGINE", "google_news")
                               or "google_news")
        if self.engine_default not in ENGINES:
            self.engine_default = "google"
        self._cache_max = cache_max
        self._cache: Dict[str, tuple] = {}  # chave -> (expira_em, payload)
        self._uso_n = 0  # contador escalar diário (sem leak por query distinta)
        self._dia: str = datetime.now(timezone.utc).date().isoformat()
        # Observabilidade do skip (round 9): por que a descoberta rendeu zero?
        self.bloqueios_cap = 0
        self.erros_rede = 0
        self.ultimo_motivo = "ok"  # ok|cap|erro|vazio|sem_chave
        self._trava = threading.Lock()  # buscas de uma onda rodam em paralelo (threads)

    @property
    def uso_hoje(self) -> int:
        hoje = datetime.now(timezone.utc).date().isoformat()
        if hoje != self._dia:
            self._dia, self._uso_n = hoje, 0
        return self._uso_n

    @property
    def ativo(self) -> bool:
        return bool(self.api_key)

    def engine_de(self, params: Dict[str, Any]) -> str:
        """Motor efetivo: override por query > default do cliente."""
        eng = str((params or {}).get("engine") or self.engine_default).strip().lower()
        return eng if eng in ENGINES else "google"

    def result_key(self, params: Dict[str, Any]) -> str:
        """Campo do JSON com os resultados do motor efetivo."""
        return RESULT_KEY[self.engine_de(params)]

    def _chave(self, params: Dict[str, Any]) -> str:
        return "|".join(f"{k}={params.get(k)}" for k in ("engine", "q", "hl", "gl", "num"))

    def buscar(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Retorna o JSON bruto ou None (sem chave, timeout, erro). Nunca levanta."""
        return self.buscar_ex(params).payload

    def buscar_ex(self, params: Dict[str, Any]) -> ResultadoBusca:
        """Como `buscar`, mas devolve o motivo e se veio do cache DESTA chamada.

        `ultimo_motivo` continua sendo atualizado (compat), mas é estado compartilhado:
        quem precisa do motivo por requisição (o agente) usa o retorno daqui."""
        if not self.ativo:
            self.ultimo_motivo = "sem_chave"
            return ResultadoBusca(None, "sem_chave")
        agora = time.time()
        chave = self._chave(params)
        with self._trava:
            hit = self._cache.get(chave)
            if hit and hit[0] > agora:
                return ResultadoBusca(hit[1], "ok", cache=True)
            # Teto diário de custo (check #7): cache-hit não conta, só chamada real
            hoje = datetime.now(timezone.utc).date().isoformat()
            if hoje != self._dia:
                self._dia, self._uso_n = hoje, 0
            cap = getattr(config, "SERPAPI_DAILY_CAP", 100)
            estourou = bool(cap and self._uso_n >= cap)
            if estourou:
                self.bloqueios_cap += 1
                self.ultimo_motivo = "cap"
        if estourou:
            # WARNING de propósito: aparece no bot_err.log (era silêncio total)
            log.warning("SerpAPI teto diário atingido (%s/%s): descoberta pausada", self._uso_n, cap)
            telemetria.fallback("serpapi.cap", f"teto diário {self._uso_n}/{cap}")
            return ResultadoBusca(None, "cap")
        try:
            corpo = dict(params)
            corpo.pop("engine", None)  # motor vai explícito (override por query)
            r = replay.http_get(
                "https://serpapi.com/search.json",
                params={"engine": self.engine_de(params), "api_key": self.api_key, **corpo},
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
            tem = bool((payload or {}).get(self.result_key(params))
                       or (payload or {}).get("top_stories"))
            with self._trava:
                self._uso_n += 1
                if tem:
                    # Vazios NÃO entram no cache: transitório colado por 1h de TTL
                    # custa mais caro agora (mais queries por checagem).
                    if len(self._cache) >= self._cache_max:  # teto: evita crescimento ilimitado
                        mais_antiga = min(self._cache, key=lambda k: self._cache[k][0])
                        del self._cache[mais_antiga]
                    self._cache[chave] = (agora + self.ttl, payload)
                self.ultimo_motivo = "ok" if tem else "vazio"
            return ResultadoBusca(payload, "ok" if tem else "vazio")
        except Exception as e:
            self.erros_rede += 1
            self.ultimo_motivo = "erro"
            if isinstance(e, replay.CotaSerpAPIEsgotada):
                log.warning("SerpAPI cota esgotada/chave inválida, eval deve parar: %s", str(e)[:200])
            else:
                log.warning("SerpAPI falhou: %s", str(e)[:150])
            if not isinstance(e, (replay.OrcamentoSerpAPIEsgotado, replay.CotaSerpAPIEsgotada)):  # já emitiram
                telemetria.fallback("serpapi", f"{type(e).__name__}: {e}")
            return ResultadoBusca(None, "erro")  # SerpAPI é opcional: falha vira etapa, nunca exceção


def normalizar_item(item: Dict[str, Any], engine: str = "google_news",
                    top_story: bool = False) -> Dict[str, Any]:
    """news_results[]/organic_results[]/top_stories[] -> ArtigoNoticia PARCIAL.

    Sem corpo/veredito: exige deep crawl. Snippet guardado em `_snippet`
    (fallback de leitura quando não há corpo — manchete sozinha vale menos).
    """
    fonte = item.get("source", {}) or {}
    if isinstance(fonte, str):
        # Orgânico novo: `source` vem string ("Einstein Hospital…").
        nome_fonte, autores = fonte, []
    else:
        nome_fonte = fonte.get("name", "")
        autores = list(fonte.get("authors", []) or [])
    if engine == "google" and not nome_fonte:
        dl = (item.get("displayed_link") or "").split("›")[0].strip(" /")
        nome_fonte = re.sub(r"^https?://", "", dl)
    # Descrição "sobre a fonte" (grátis no payload): ajuda a classificar
    # domínios novos na descoberta de catálogo.
    sobre = item.get("about_this_result", {}) or {}
    desc_fonte = ((sobre.get("source") or {}).get("description")
                  if isinstance(sobre.get("source"), dict) else None)
    base = {
        "subtitulo": None,
        "autores": autores,
        "data_atualizacao": None,
        "editoria": None,
        "tags": [],
        "corpo_texto": None,
        "corpo_markdown": None,
        "fonte": {"id": "", "nome": nome_fonte, "homepage": "", "tipo": ""},
        "tipo_conteudo": "noticia",
        "veredito": None,
        "selo_original": None,
        "metodo_checagem": None,
        "links_fontes": [],
        "coletado_em": datetime.now(timezone.utc).isoformat(),
        "idioma": "pt-BR",
        "origem": "serpapi",
        "_snippet": item.get("snippet"),
        "_fonte_desc": desc_fonte,
        "_secao_checagem": None,  # preenchido abaixo (URL final conhecida)
        "_engine": engine,
        "_top_story": top_story,
    }
    if engine == "google_scholar":
        # Scholar: snippet + publication_info.summary; cited_by = autoridade.
        # summary ("Autor - J Med, 2024") NÃO é data: extrai só o ano.
        pub = item.get("publication_info", {}) or {}
        resumo_pub = pub.get("summary")
        m_ano = re.search(r"\b(19\d{2}|20\d{2})\b", resumo_pub or "")
        cit = 0
        try:
            cit = int((((item.get("inline_links") or {}).get("cited_by") or {}).get("total")) or 0)
        except (TypeError, ValueError):
            cit = 0
        base.update({
            "url": item.get("link", ""),
            "titulo": item.get("title", ""),
            "data_publicacao": m_ano.group(1) if m_ano else None,
            "imagem_principal": {"src": None, "legenda": None, "alt": None},
            "posicao": item.get("position"),
            "_citacoes": cit,
            "_scholar": True,
            "_scholar_pub": resumo_pub,
        })
    elif engine == "google":
        base.update({
            "url": item.get("link", ""),
            "titulo": item.get("title", ""),
            "data_publicacao": item.get("date"),  # relativa ("há 2 dias"): exibe, não ordena
            "imagem_principal": {"src": item.get("thumbnail"), "legenda": None, "alt": None},
            "posicao": item.get("position"),
        })
    else:
        base.update({
            "url": item.get("link", ""),
            "titulo": item.get("title", ""),
            "data_publicacao": item.get("iso_date"),
            "imagem_principal": {"src": item.get("thumbnail"), "legenda": None, "alt": None},
            "posicao": item.get("position"),
        })
    base["_secao_checagem"] = eh_secao_checagem(base["url"])
    return base


def rotear_fonte(parcial: Dict[str, Any], catalogo: Catalogo) -> Dict[str, Any]:
    """Liga source.name -> portal do catálogo (p/ deep crawl com schema próprio).

    Sem match: mantém como 'não catalogada' (peso menor, candidata a inclusão).
    Sem denylist: nenhum domínio é bloqueado aqui.
    """
    nome = (parcial.get("fonte") or {}).get("nome", "")
    # Fase 2: catálogo decidido pela URL (domínio + aliases), nome só como último recurso;
    # `catalogada` = portal CURADO (inserção automática é candidata, não curada).
    portal = catalogo.por_url(parcial.get("url", "")) or catalogo.por_nome_fonte(nome)
    if portal:
        parcial["fonte"] = {
            "id": portal["id"],
            "nome": portal["nome"],
            "homepage": portal["homepage"],
            "tipo": portal.get("tipo", ""),
        }
        parcial["catalogada"] = bool(catalogo.eh_curado(portal))
    else:
        parcial["catalogada"] = False
    return parcial
