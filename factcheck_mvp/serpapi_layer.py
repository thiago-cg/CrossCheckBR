"""Camada 0 — descoberta fresca via SerpAPI (Google tradicional ou Google News).

Dois motores mantidos (SERP_ENGINE): `google` (orgânico, padrão) e
`google_news` (legado). Duas estratégias no orgânico (SERP_ESTRATEGIA):
`simples` (crua + dirigida por vocabulário) e `avancada` (crua + 1 filtro
com todos os hosts de checagem). `ambas` = união (p/ A/B).
Sem chave: tudo degrada com elegância (retorna None; pipeline segue no índice).
Sem denylist: filtragem só por relevância nas etapas seguintes.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config
from .catalogo import Catalogo

log = logging.getLogger("factcheck.serpapi")

ENGINES = ("google", "google_news", "google_scholar")
RESULT_KEY = {"google_news": "news_results", "google": "organic_results",
              "google_scholar": "organic_results"}

# Portais de checagem priorizados na estratégia avançada.
# `secao_apenas`: a checagem mora numa seção do portal amplo (vale só o
# path); sem a flag, o host é dedicado e qualquer URL dele é checagem.
SITES_CHECAGEM = [
    {"host": "justicaeleitoral.jus.br", "paths": ["fato-ou-boato"],  # Fato ou Boato (TSE)
     "secao_apenas": True},
    {"host": "projetocomprova.com.br", "paths": []},                  # Projeto Comprova
    {"host": "lupa.uol.com.br", "paths": []},                         # Agência Lupa
    {"host": "aosfatos.org", "paths": ["radar"]},                     # Aos Fatos + Radar
    {"host": "gov.br", "paths": ["saude-com-ciencia"],                # Saúde com Ciência (MS)
     "secao_apenas": True},
    {"host": "canalsaude.fiocruz.br", "paths": []},                   # Canal Saúde (Fiocruz)
    {"host": "e-farsas.com", "paths": []},                            # E-farsas
    {"host": "valorinveste.globo.com", "paths": []},                  # Valor Investe
    {"host": "noticias.uol.com.br", "paths": ["confere"],             # UOL Confere
     "secao_apenas": True},
    {"host": "boatos.org", "paths": []},                              # Boatos.org
    {"host": "agenciatatu.com.br", "paths": []},                      # Agência Tatu
    {"host": "estadao.com.br", "paths": ["estadao-verifica"],         # Estadão Verifica
     "secao_apenas": True},
    {"host": "g1.globo.com", "paths": ["fato-ou-fake"],               # Fato ou Fake (G1)
     "secao_apenas": True},
]


def sites_checagem() -> List[Dict[str, Any]]:
    """Padrão + extras de SERP_SITES_EXTRAS (hosts separados por vírgula).

    Extras aceitam URL completa: normaliza p/ host (sem scheme, path, porta).
    """
    from urllib.parse import urlparse as _up
    sites = [dict(s) for s in SITES_CHECAGEM]
    vistos = {s["host"] for s in sites}
    for h in (getattr(config, "SERP_SITES_EXTRAS", "") or "").split(","):
        h = (h or "").strip().lower()
        if "://" in h or "/" in h:
            try:
                u = _up(h if "://" in h else f"https://{h}")
                h = u.hostname or ""
            except Exception:
                h = ""
        else:
            h = h.split(":")[0].split("?")[0]  # sem porta/query
        if h.startswith("www."):
            h = h[4:]  # removeprefix manual (lstrip corrompe: tira charset, não prefixo)
        if h and h not in vistos:
            vistos.add(h)
            sites.append({"host": h, "paths": []})
    return sites


def eh_secao_checagem(url: str) -> bool:
    """URL é de seção de checagem? Host dedicado vale; host amplo exige path.

    Compensa o que o Google não faz: site: em host amplo (g1, estadão, UOL…)
    retorna notícia geral — o path local separa checagem de geral sem
    custar busca extra (inurl: em OR retorna zero no Google).
    """
    from urllib.parse import urlparse as _up
    try:
        u = _up((url or "").lower())
        h = u.hostname or ""
        if h.startswith("www."):
            h = h[4:]
        path = u.path or ""
    except Exception:
        return False
    for s in sites_checagem():
        host = s["host"]
        if h == host or h.endswith("." + host):
            if not s.get("secao_apenas"):
                return True  # outlet dedicado: tudo é checagem
            # Match por segmento exato: /radar/ vale, /radar-meteorologico não.
            segs = [p for p in path.split("/") if p]
            if any(p in segs for p in s.get("paths", [])):
                return True
    return False


def _base(q: str, engine: str) -> Dict[str, Any]:
    return {"q": q, "hl": "pt-br", "gl": "br", "num": 10, "engine": engine}


def construir_queries_news(afirmacao: str) -> List[Dict[str, Any]]:
    """Legado: 2 queries no Google News (neutra + dirigida a checadores)."""
    curta = afirmacao.strip()[:200]
    return [
        {"q": curta, "hl": "pt-br", "gl": "br", "engine": "google_news"},
        {"q": f"{curta} é verdade OR é falso OR checagem",
         "hl": "pt-br", "gl": "br", "engine": "google_news"},
    ]


def construir_queries_simples(afirmacao: str) -> List[Dict[str, Any]]:
    """Orgânico sem operadores de site: crua + dirigida leve."""
    curta = afirmacao.strip()[:200]
    return [
        _base(curta, "google"),
        _base(f"{curta} é verdade OR é falso OR checagem OR fake", "google"),
    ]


def construir_queries_avancada(afirmacao: str,
                               sites: Optional[List[Dict[str, Any]]] = None
                               ) -> List[Dict[str, Any]]:
    """Orgânico em 2 buscas: crua (recall) + 1 filtro com TODOS os hosts.

    Evidência live (23/09/2026, "ibuprofeno cura dengue"): mega-query com
    pares (site:host inurl:path) em OR retorna ZERO — aninhado ou flat,
    o Google inviabiliza a cadeia. Só o bloco `site:` puro nos hosts
    dedicados funciona (9 hosts, 1 resultado). Por isso os paths (TSE,
    Radar, Saúde com Ciência, Confere, Verifica, Fato ou Fake) ficam
    cobertos pelo índice local + query crua, não por inurl:.
    """
    curta = afirmacao.strip()[:200]
    sites = sites if sites is not None else sites_checagem()
    # Hosts válidos + teto: filtro gigante estoura o limite de termos do
    # Google e a perna do filtro zera (evidência live 23/09/2026).
    hosts = [s["host"] for s in sites
             if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", s.get("host", "") or "")][:20]
    ors = " OR ".join(f"site:{h}" for h in hosts)
    return [_base(curta, "google"), _base(f"{curta} ({ors})", "google")]


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
        curta = afirmacao.strip()[:200]
        return [{"q": curta, "hl": "pt", "num": 5, "engine": "google_scholar"}]
    est = (estrategia if estrategia is not None else
           getattr(config, "SERP_ESTRATEGIA", "avancada") or "avancada")
    est = str(est).strip().lower()
    if est == "agente":
        raise ValueError("agente é orquestração pipeline-level (use agente.descobrir), "
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
        if not self.ativo:
            self.ultimo_motivo = "sem_chave"
            return None
        agora = time.time()
        chave = self._chave(params)
        hit = self._cache.get(chave)
        if hit and hit[0] > agora:
            return hit[1]
        # Teto diário de custo (check #7): cache-hit não conta, só chamada real
        hoje = datetime.now(timezone.utc).date().isoformat()
        if hoje != self._dia:
            self._dia, self._uso_n = hoje, 0
        cap = getattr(config, "SERPAPI_DAILY_CAP", 100)
        if cap and self._uso_n >= cap:
            self.bloqueios_cap += 1
            self.ultimo_motivo = "cap"
            # WARNING de propósito: aparece no bot_err.log (era silêncio total)
            log.warning("SerpAPI teto diário atingido (%s/%s): descoberta pausada",
                        self._uso_n, cap)
            return None
        try:
            import httpx

            corpo = dict(params)
            corpo.pop("engine", None)  # motor vai explícito (override por query)
            r = httpx.get(
                "https://serpapi.com/search.json",
                params={"engine": self.engine_de(params), "api_key": self.api_key, **corpo},
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
            self._uso_n += 1
            tem = bool((payload or {}).get(self.result_key(params))
                       or (payload or {}).get("top_stories"))
            if tem:
                # Vazios NÃO entram no cache: transitório colado por 1h de TTL
                # custa mais caro agora (mais queries por checagem).
                if len(self._cache) >= self._cache_max:  # teto: evita crescimento ilimitado
                    mais_antiga = min(self._cache, key=lambda k: self._cache[k][0])
                    del self._cache[mais_antiga]
                self._cache[chave] = (agora + self.ttl, payload)
            self.ultimo_motivo = "ok" if tem else "vazio"
            return payload
        except Exception as e:
            self.erros_rede += 1
            self.ultimo_motivo = "erro"
            log.warning("SerpAPI falhou: %s", str(e)[:150])
            return None  # SerpAPI é opcional: falha vira etapa "pulada/falha", nunca exceção


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
    portal = catalogo.por_nome_fonte(nome) or catalogo.por_dominio(parcial.get("url", ""))
    if portal:
        parcial["fonte"] = {
            "id": portal["id"],
            "nome": portal["nome"],
            "homepage": portal["homepage"],
            "tipo": portal.get("tipo", ""),
        }
        parcial["catalogada"] = True
    else:
        parcial["catalogada"] = False
    return parcial
