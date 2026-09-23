"""Camada 0 — descoberta fresca via SerpAPI Google News.

Sem chave: tudo degrada com elegância (retorna None; pipeline segue no índice).
Sem denylist: filtragem só por relevância nas etapas seguintes.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config
from .catalogo import Catalogo

log = logging.getLogger("factcheck.serpapi")

ENGINE = "google_news"


def construir_queries(afirmacao: str) -> List[Dict[str, Any]]:
    """2 queries por afirmação: neutra + dirigida a checadores (operador site:)."""
    curta = afirmacao.strip()[:200]
    return [
        {"q": curta, "hl": "pt-br", "gl": "br"},
        {"q": f"{curta} é verdade OR é falso OR checagem", "hl": "pt-br", "gl": "br"},
    ]


class SerpAPIClient:
    def __init__(self, api_key: str | None = None, ttl: int | None = None, timeout: int = 25,
                 cache_max: int = 200):
        # None = usa env; "" explícito = força desligado (testes offline)
        self.api_key = config.SERPAPI_KEY if api_key is None else api_key
        self.ttl = config.CACHE_SERPAPI_TTL if ttl is None else ttl
        self.timeout = timeout
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

    def _chave(self, params: Dict[str, Any]) -> str:
        return "|".join(f"{k}={params.get(k)}" for k in ("q", "hl", "gl"))

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

            r = httpx.get(
                "https://serpapi.com/search.json",
                params={"engine": ENGINE, "api_key": self.api_key, **params},
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
            self._uso_n += 1
            if len(self._cache) >= self._cache_max:  # teto: evita crescimento ilimitado
                mais_antiga = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[mais_antiga]
            self._cache[chave] = (agora + self.ttl, payload)
            self.ultimo_motivo = "ok" if (payload or {}).get("news_results") else "vazio"
            return payload
        except Exception as e:
            self.erros_rede += 1
            self.ultimo_motivo = "erro"
            log.warning("SerpAPI falhou: %s", str(e)[:150])
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
