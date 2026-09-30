"""Catálogo dos 20 portais + roteamento de fontes. Sem denylist (decisão do MVP):
nada é bloqueado por domínio; o filtro é só piso de relevância por consulta.

Roteamento (achado A3 da revisão): `catalogada` deve ser decidido pela URL,
nunca pelo nome da fonte. `por_url` casa pelo prefixo mais específico
(host + caminho) da `homepage` e dos `aliases` de cada portal; sem prefixo,
cai no domínio registrável (eTLD+1) se só um portal usa aquele domínio.
`por_nome_fonte` agora só aceita igualdade de nome normalizado (ou alias de
nome); substring não roteia mais ("Estadão Mato Grosso" ≠ Estadão, "" ≠ G1).

Curadoria (achado A5): portais com `origem_catalogo: "descoberta-automatica"`
(inseridos pelo pipeline sem revisão) ou `status: "candidato"` são
CANDIDATOS: continuam roteáveis, mas `eh_curado()` é False. Apagar ou
promover é decisão humana.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOGO = BASE_DIR / "data" / "catalogo.json"

# Sufixos públicos de 2 níveis (eTLD) relevantes p/ portais BR e vizinhos. Lista
# curta de propósito: a PSL completa é dependência demais p/ o MVP.
_SUFIXOS_2_NIVEIS = frozenset("""
com.br gov.br org.br net.br edu.br jus.br leg.br mil.br mp.br art.br blog.br jor.br tv.br ind.br inf.br
eco.br emp.br app.br dev.br log.br med.br adv.br eng.br etc.br fot.br def.br rec.br tur.br wiki.br
co.uk org.uk gov.uk ac.uk com.ar gob.ar com.mx gob.mx com.pt gov.pt com.co com.au
""".split())

STATUS_CURADO = "curado"
STATUS_CANDIDATO = "candidato"
_ORIGENS_CANDIDATAS = frozenset({"descoberta-automatica"})


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


def _host_caminho(url_ou_dominio: str) -> Tuple[str, str]:
    """-> (host sem www./porta, caminho normalizado com barra final)."""
    texto = (url_ou_dominio or "").strip()
    if not texto:
        return "", "/"
    if "://" not in texto:
        texto = "https://" + texto.lstrip("/")
    try:
        p = urlparse(texto)
    except Exception:
        return "", "/"
    host = (p.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    caminho = unquote(p.path or "/").lower()
    if not caminho.endswith("/"):
        caminho += "/"
    return host, caminho


def dominio_registravel(url_ou_host: str) -> str:
    """eTLD+1 simples: 'noticias.uol.com.br' -> 'uol.com.br'; 'g1.globo.com' -> 'globo.com'."""
    host = _host_caminho(url_ou_host)[0] if ("/" in (url_ou_host or "") or ":" in (url_ou_host or "")) \
        else (url_ou_host or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    partes = [p for p in host.split(".") if p]
    if len(partes) <= 2:
        return ".".join(partes)
    if ".".join(partes[-2:]) in _SUFIXOS_2_NIVEIS:
        return ".".join(partes[-3:])
    return ".".join(partes[-2:])


def normalizar_nome(nome: Optional[str]) -> str:
    """Nome de exibição -> chave: minúsculas, sem acento, pontuação vira espaço."""
    texto = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode().lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _parece_dominio(texto: str) -> bool:
    return bool(re.fullmatch(r"(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?", (texto or "").strip(), re.I))


class Catalogo:
    def __init__(self, portais: List[Dict[str, Any]]):
        self.portais = portais
        self._por_dominio: Dict[str, Dict[str, Any]] = {}
        self._por_nome: Dict[str, Dict[str, Any]] = {}
        self._por_nome_norm: Dict[str, Dict[str, Any]] = {}
        self._prefixos: List[Tuple[str, str, Dict[str, Any]]] = []  # (host, caminho, portal)
        for p in portais:
            self._indexar(p)

    # ------------------------------------------------------------------ indexação
    def _indexar(self, p: Dict[str, Any]) -> None:
        dom = _dominio(p.get("homepage", ""))
        if dom:
            self._por_dominio.setdefault(dom, p)
        for alias in p.get("aliases") or []:
            dom_a = _dominio(alias)
            if dom_a:
                self._por_dominio.setdefault(dom_a, p)
        nome = (p.get("nome") or "").strip().lower()
        if nome:
            self._por_nome.setdefault(nome, p)
        for n in [p.get("nome"), p.get("id")] + list(p.get("aliases_nome") or []):
            k = normalizar_nome(n)
            if k:
                self._por_nome_norm.setdefault(k, p)
        for alvo in [p.get("homepage", "")] + list(p.get("aliases") or []):
            host, caminho = _host_caminho(alvo)
            if host:
                self._prefixos.append((host, caminho, p))

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

    # ------------------------------------------------------------------ curadoria
    @staticmethod
    def status(portal: Optional[Dict[str, Any]]) -> Optional[str]:
        """'curado' | 'candidato'. Explícito em `status` vence; senão, inserção
        automática (origem_catalogo=descoberta-automatica) = candidato."""
        if not portal:
            return None
        explicito = (portal.get("status") or "").strip().lower()
        if explicito:
            return explicito
        if (portal.get("origem_catalogo") or "").strip().lower() in _ORIGENS_CANDIDATAS:
            return STATUS_CANDIDATO
        return STATUS_CURADO

    def eh_curado(self, portal: Optional[Dict[str, Any]]) -> bool:
        return self.status(portal) == STATUS_CURADO

    def curados(self) -> List[Dict[str, Any]]:
        return [p for p in self.portais if self.eh_curado(p)]

    def candidatos(self) -> List[Dict[str, Any]]:
        return [p for p in self.portais if self.status(p) == STATUS_CANDIDATO]

    # ------------------------------------------------------------------ roteamento
    def por_url(self, url: str, apenas_curados: bool = False) -> Optional[Dict[str, Any]]:
        """URL -> portal pelo prefixo mais específico (host, depois caminho) da homepage
        ou de um alias. Sem prefixo: domínio registrável, se um único portal o usa
        (globo.com/uol.com.br são compartilhados -> None)."""
        host, caminho = _host_caminho(url)
        if not host:
            return None
        melhor, chave_melhor = None, (-1, -1)
        for h, c, portal in self._prefixos:
            if apenas_curados and not self.eh_curado(portal):
                continue
            if host != h and not host.endswith("." + h):
                continue
            if not caminho.startswith(c):
                continue
            chave = (len(h), len(c))
            if chave > chave_melhor:
                melhor, chave_melhor = portal, chave
        if melhor is not None:
            return melhor
        reg = dominio_registravel(host)
        donos = {id(p): p for h, _, p in self._prefixos
                 if dominio_registravel(h) == reg and (not apenas_curados or self.eh_curado(p))}
        return next(iter(donos.values())) if len(donos) == 1 else None

    def por_dominio(self, url_ou_dominio: str) -> Optional[Dict[str, Any]]:
        """Roteia URL/dominio -> portal. Inclui match por sufixo (subdomínios) e aliases."""
        dom = _dominio(url_ou_dominio)
        if not dom:
            return None
        if dom in self._por_dominio:
            return self._por_dominio[dom]
        for conhecido, portal in self._por_dominio.items():
            if dom.endswith("." + conhecido):
                return portal
        return None

    def por_nome_fonte(self, nome: Optional[str]) -> Optional[Dict[str, Any]]:
        """Roteia `source.name` da SerpAPI (domínio ou nome de exibição).

        Só igualdade: nome normalizado (sem acento/caixa/pontuação) contra nome,
        id e `aliases_nome` do portal; se parece domínio, via `por_url`.
        Vazio/None -> None. Substring NÃO roteia (evita "Estadão Mato Grosso" -> Estadão)."""
        texto = (nome or "").strip()
        if not texto:
            return None
        if texto.lower() in self._por_nome:
            return self._por_nome[texto.lower()]
        k = normalizar_nome(texto)
        if k and k in self._por_nome_norm:
            return self._por_nome_norm[k]
        if _parece_dominio(texto):
            return self.por_url(texto)
        return None

    def adicionar(self, portal: Dict[str, Any]) -> bool:
        """Insere portal em memória (pós-promoção imediata). False se dup/inválido."""
        if not portal or not portal.get("id") or not portal.get("homepage"):
            return False
        if any(p.get("id") == portal["id"] for p in self.portais):
            return False
        dom = _dominio(portal.get("homepage", ""))
        if dom and self.por_dominio(dom):
            return False
        self.portais.append(portal)
        self._indexar(portal)
        return True
