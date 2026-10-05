"""Base de checagens via Google Fact Check Tools API (ClaimReview).

Reúne checagens publicadas por Lupa, Aos Fatos, Estadão Verifica, Comprova,
G1 Fato ou Fake etc., já com o selo da agência (textualRating). Devolve docs
no MESMO formato do índice local, então seguem o fluxo de selos do pipeline
(julgados pelo LLM-juiz antes de virar sinal).

Sem chave: tudo degrada com elegância (lista vazia; pipeline segue no índice).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List

from . import config

log = logging.getLogger("factcheck.factcheck_api")

_URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
_cache: Dict[str, tuple] = {}  # query -> (expira_em, docs)
_CACHE_MAX = 200


def ativo() -> bool:
    return bool(config.FACTCHECK_API_KEY)


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (texto or "").lower()).strip("-")[:40] or "agencia"


def _para_docs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """claims[].claimReview[] -> docs do índice (1 doc por review com URL)."""
    docs: List[Dict[str, Any]] = []
    vistos = set()
    for claim in (payload or {}).get("claims", []) or []:
        alegacao = (claim.get("text") or "").strip()
        autor = (claim.get("claimant") or "").strip()
        for rev in claim.get("claimReview", []) or []:
            url = (rev.get("url") or "").strip()
            if not url or url in vistos:
                continue
            vistos.add(url)
            pub = rev.get("publisher") or {}
            nome = (pub.get("name") or pub.get("site") or "agência de checagem").strip()
            selo = (rev.get("textualRating") or "").strip() or None
            titulo = (rev.get("title") or alegacao or url).strip()
            # Corpo p/ o juiz: o que foi alegado + o que a agência concluiu.
            corpo = (f"Alegação checada: {alegacao}"
                     + (f" (autor: {autor})" if autor else "")
                     + f". Classificação da agência {nome}: {selo or 'sem classificação'}. {titulo}")
            docs.append({
                "url": url, "titulo": titulo, "corpo_texto": corpo,
                "fonte": {"id": _slug(pub.get("site") or nome), "nome": nome},
                "tipo_conteudo": "checagem", "veredito": selo, "selo_original": selo,
                "data_publicacao": (rev.get("reviewDate") or claim.get("claimDate") or None),
            })
    return docs


def buscar(afirmacao: str, max_itens: int = 0) -> List[Dict[str, Any]]:
    """Checagens publicadas sobre a afirmação. Nunca levanta."""
    if not ativo():
        return []
    q = (afirmacao or "").strip()[:200]
    if len(q) < 10:
        return []
    agora = time.time()
    hit = _cache.get(q)
    if hit and hit[0] > agora:
        return hit[1]
    try:
        import httpx

        r = httpx.get(_URL, params={"query": q, "languageCode": "pt-BR",
                                    "pageSize": max_itens or config.FACTCHECK_MAX_ITENS,
                                    "key": config.FACTCHECK_API_KEY},
                      timeout=config.FACTCHECK_TIMEOUT_S)
        r.raise_for_status()
        docs = _para_docs(r.json())
    except Exception as e:
        # Nunca loga a URL (contém a chave).
        status = getattr(getattr(e, "response", None), "status_code", None)
        log.warning("Fact Check API falhou: %s%s", type(e).__name__, f" HTTP {status}" if status else "")
        return []
    log.info("fact check: %r -> %d checagem(ns): %s", q[:100], len(docs),
             ", ".join(f"{d['fonte']['nome']}={d['veredito']}" for d in docs[:5]))
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
    _cache[q] = (agora + (config.CACHE_SERPAPI_TTL or 3600), docs)
    return docs
