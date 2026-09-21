"""Catálogo dos 20 portais + roteamento de fontes. Sem denylist (decisão do MVP):
nada é bloqueado por domínio; o filtro é só piso de relevância por consulta."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOGO = BASE_DIR / "data" / "catalogo.json"


def _dominio(url_ou_nome: str) -> str:
    texto = (url_ou_nome or "").strip().lower()
    if "://" not in texto:
        texto = "https://" + texto
    try:
        netloc = urlparse(texto).netloc
    except Exception:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


class Catalogo:
    def __init__(self, portais: List[Dict[str, Any]]):
        self.portais = portais
        self._por_dominio: Dict[str, Dict[str, Any]] = {}
        self._por_nome: Dict[str, Dict[str, Any]] = {}
        for p in portais:
            dom = _dominio(p.get("homepage", ""))
            if dom:
                self._por_dominio.setdefault(dom, p)
            nome = (p.get("nome") or "").strip().lower()
            if nome:
                self._por_nome.setdefault(nome, p)

    @classmethod
    def carregar(cls, caminho: Optional[str] = None) -> "Catalogo":
        try:
            dados = json.loads(Path(caminho or DEFAULT_CATALOGO).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise RuntimeError(f"catálogo indisponível ({caminho or DEFAULT_CATALOGO}): {e}")
        portais = dados.get("portais", dados if isinstance(dados, list) else [])
        return cls(portais)

    def __len__(self) -> int:
        return len(self.portais)

    def checagem(self) -> List[Dict[str, Any]]:
        return [p for p in self.portais if p.get("tipo") == "checagem"]

    def gerais(self) -> List[Dict[str, Any]]:
        return [p for p in self.portais if p.get("tipo") == "geral"]

    def por_dominio(self, url_ou_dominio: str) -> Optional[Dict[str, Any]]:
        """Roteia URL/dominio -> portal. Inclui match por sufixo (subdomínios)."""
        dom = _dominio(url_ou_dominio)
        if not dom:
            return None
        if dom in self._por_dominio:
            return self._por_dominio[dom]
        for conhecido, portal in self._por_dominio.items():
            if dom.endswith("." + conhecido):
                return portal
        return None

    def por_nome_fonte(self, nome: str) -> Optional[Dict[str, Any]]:
        """Roteia `source.name` da SerpAPI (domínio ou nome de exibição).

        Ordem (evita over-match): igualdade exata -> domínio -> substring.
        """
        texto = (nome or "").strip().lower()
        if texto in self._por_nome:
            return self._por_nome[texto]
        if "." in texto:  # parece domínio: tenta via domínio primeiro
            achado = self.por_dominio(texto)
            if achado:
                return achado
        for conhecido, portal in self._por_nome.items():
            if conhecido in texto or texto in conhecido:
                return portal
        return None
