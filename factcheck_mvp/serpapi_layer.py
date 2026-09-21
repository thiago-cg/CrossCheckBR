"""Camada 0 — descoberta fresca via SerpAPI Google News.

Sem chave: tudo degrada com elegância (retorna None; pipeline segue no índice).
Sem denylist: filtragem só por relevância nas etapas seguintes.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config
from .catalogo import Catalogo

ENGINE = "google_news"


def construir_queries(afirmacao: str) -> List[Dict[str, Any]]:
    """2 queries por afirmação: neutra + dirigida a checadores (operador site:)."""
    curta = afirmacao.strip()[:200]
    return [
        {"q": curta, "hl": "pt-br", "gl": "br"},
        {"q": f"{curta} é verdade OR é falso OR checagem", "hl": "pt-br", "gl": "br"},
    ]


class SerpAPIClient:
    def __init__(self, api_key: str = "", ttl: int | None = None, timeout: int = 25,
                 cache_max: int = 200):
        self.api_key = api_key or config.SERPAPI_KEY
        self.ttl = config.CACHE_SERPAPI_TTL if ttl is None else ttl
        self.timeout = timeout
        self._cache_max = cache_max
        self._cache: Dict[str, tuple] = {}  # chave -> (expira_em, payload)

    @property
    def ativo(self) -> bool:
        return bool(self.api_key)

    def _chave(self, params: Dict[str, Any]) -> str:
        return "|".join(f"{k}={params.get(k)}" for k in ("q", "hl", "gl"))

    def buscar(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Retorna o JSON bruto ou None (sem chave, timeout, erro). Nunca levanta."""
        if not self.ativo:
            return None
        agora = time.time()
        chave = self._chave(params)
        hit = self._cache.get(chave)
        if hit and hit[0] > agora:
            return hit[1]
        try:
            import httpx

            r = httpx.get(
                "https://serpapi.com/search.json",
                params={"engine": ENGINE, "api_key": self.api_key, **params},
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
            if len(self._cache) >= self._cache_max:  # teto: evita crescimento ilimitado
                mais_antiga = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[mais_antiga]
            self._cache[chave] = (agora + self.ttl, payload)
            return payload
        except Exception:
            return None  # SerpAPI é opcional: falha vira etapa "pulada/falha", nunca exceção


def normalizar_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """news_results[] -> ArtigoNoticia PARCIAL (sem corpo/veredito: exige deep crawl)."""
    fonte = item.get("source", {}) or {}
    return {
        "url": item.get("link", ""),
        "titulo": item.get("title", ""),
        "subtitulo": None,
        "autores": list(fonte.get("authors", []) or []),
        "data_publicacao": item.get("iso_date"),
        "data_atualizacao": None,
        "editoria": None,
        "tags": [],
        "corpo_texto": None,
        "corpo_markdown": None,
        "imagem_principal": {"src": item.get("thumbnail"), "legenda": None, "alt": None},
        "fonte": {"id": "", "nome": fonte.get("name", ""), "homepage": "", "tipo": ""},
        "tipo_conteudo": "noticia",
        "veredito": None,
        "selo_original": None,
        "metodo_checagem": None,
        "links_fontes": [],
        "coletado_em": datetime.now(timezone.utc).isoformat(),
        "idioma": "pt-BR",
        "origem": "serpapi",
        "posicao": item.get("position"),
    }


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
