"""Extração de JSON-LD (ClaimReview / NewsArticle) de páginas HTML — só stdlib.

Por que JSON-LD primeiro: Aos Fatos, Estadão Verifica e Comprova publicam
`ClaimReview` com `claimReviewed` e `reviewRating.alternateName` (veredito
oficial do editor, custo zero, sem LLM). A Lupa expõe `NewsArticle.articleBody`.

Contrato (consumido pelo ingestor e pela fase de extração — não renomear):
    extrair(html) -> {"claim_reviews": [{"afirmacao_checada", "selo_original",
                      "url", "autor", "data_pub", "agencia"}],
                      "article_body", "headline", "data_pub", "autor"}
Robusto a @graph, listas, @type como lista (Aos Fatos: ["Review","ClaimReview"]),
blocos com JSON inválido (ignorados) e entidades HTML.
"""
from __future__ import annotations

import html as _html
import json
import re
from typing import Any, Dict, Iterator, List, Optional

_RE_BLOCO = re.compile(
    r"<script\b[^>]*\btype\s*=\s*[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script\s*>",
    re.S | re.I,
)
_TIPOS_ARTIGO = {"NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting",
                 "AnalysisNewsArticle", "BackgroundNewsArticle"}


def _carregar(bruto: str) -> Optional[Any]:
    """json.loads tolerante: CDATA/comentários, controle em strings, entidades HTML."""
    texto = bruto.strip()
    texto = re.sub(r"^\s*(<!--|//\s*<!\[CDATA\[|<!\[CDATA\[)", "", texto)
    texto = re.sub(r"(-->|//\s*\]\]>|\]\]>)\s*$", "", texto).strip()
    if not texto:
        return None
    for tentativa in (texto, _html.unescape(texto)):
        try:
            return json.loads(tentativa, strict=False)
        except ValueError:
            continue
    return None


def _tipos(no: Dict[str, Any]) -> set:
    t = no.get("@type", [])
    lista = t if isinstance(t, list) else [t]
    # "http://schema.org/ClaimReview" / "schema:ClaimReview" -> "ClaimReview"
    return {re.split(r"[/:#]", str(x))[-1] for x in lista if x}


def _nos(dados: Any) -> Iterator[Dict[str, Any]]:
    """Percorre todos os dicts (inclui @graph, listas e aninhados)."""
    pilha = [dados]
    while pilha:
        atual = pilha.pop()
        if isinstance(atual, list):
            pilha.extend(reversed(atual))
        elif isinstance(atual, dict):
            yield atual
            for v in reversed(list(atual.values())):
                if isinstance(v, (dict, list)):
                    pilha.append(v)


def _limpo(valor: Any) -> Optional[str]:
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        valor = str(valor)
    if not isinstance(valor, str):
        return None
    texto = _html.unescape(_html.unescape(valor))  # há sites com entidade dupla (&amp;#8211;)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


def _nome(valor: Any, por_id: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """author/publisher pode ser str, dict, lista ou referência {"@id": …}."""
    if isinstance(valor, list):
        nomes = [n for n in (_nome(v, por_id) for v in valor) if n]
        return ", ".join(dict.fromkeys(nomes)) or None
    if isinstance(valor, dict):
        if "name" not in valor and valor.get("@id") in por_id:
            valor = por_id[valor["@id"]]
        return _limpo(valor.get("name"))
    return _limpo(valor)


def _agencia(no: Dict[str, Any], por_id: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Organização que assina a checagem: publisher, senão author do tipo Organization."""
    pub = no.get("publisher")
    if pub:
        return _nome(pub, por_id)
    autor = no.get("author")
    autores = autor if isinstance(autor, list) else [autor]
    for a in autores:
        if isinstance(a, dict):
            a = por_id.get(a.get("@id"), a) if "name" not in a else a
            if _tipos(a) & {"Organization", "NewsMediaOrganization"}:
                return _limpo(a.get("name"))
    return None


def _selo(rating: Any) -> Optional[str]:
    """reviewRating.alternateName (texto do selo). Escalas numéricas variam por
    agência (1–6, 1–4, ausente): sem nome, não inventa selo a partir do número."""
    if isinstance(rating, list):
        rating = rating[0] if rating else None
    if not isinstance(rating, dict):
        return _limpo(rating) if isinstance(rating, str) else None
    return _limpo(rating.get("alternateName")) or _limpo(rating.get("name"))


def blocos(html: str) -> List[Any]:
    """Todos os blocos JSON-LD válidos da página (inválidos são ignorados)."""
    saida = []
    for bruto in _RE_BLOCO.findall(html or ""):
        dados = _carregar(bruto)
        if dados is not None:
            saida.append(dados)
    return saida


def extrair(html: str) -> Dict[str, Any]:
    resultado: Dict[str, Any] = {"claim_reviews": [], "article_body": None, "headline": None,
                                 "data_pub": None, "autor": None}
    todos = [no for dados in blocos(html) for no in _nos(dados)]
    por_id = {no["@id"]: no for no in todos if isinstance(no.get("@id"), str) and len(no) > 1}

    artigo: Optional[Dict[str, Any]] = None
    for no in todos:
        tipos = _tipos(no)
        if "ClaimReview" in tipos:
            item = no.get("itemReviewed") if isinstance(no.get("itemReviewed"), dict) else {}
            resultado["claim_reviews"].append({
                "afirmacao_checada": _limpo(no.get("claimReviewed")) or _limpo(item.get("name")),
                "selo_original": _selo(no.get("reviewRating")),
                "url": _limpo(no.get("url")) or _limpo(no.get("mainEntityOfPage")
                                                       if isinstance(no.get("mainEntityOfPage"), str) else None),
                "autor": _nome(no.get("author"), por_id),
                "data_pub": _limpo(no.get("datePublished")) or _limpo(item.get("datePublished")),
                "agencia": _agencia(no, por_id),
                "_headline": _limpo(no.get("headline")) or _limpo(no.get("name")),
                "_review_body": _limpo(no.get("reviewBody")),
            })
        elif artigo is None and tipos & _TIPOS_ARTIGO:
            artigo = no
        elif artigo is not None and tipos & _TIPOS_ARTIGO and not artigo.get("articleBody") and no.get("articleBody"):
            artigo = no  # prefere o nó que tem corpo

    if artigo is not None:
        resultado["article_body"] = _limpo(artigo.get("articleBody"))
        resultado["headline"] = _limpo(artigo.get("headline")) or _limpo(artigo.get("name"))
        resultado["data_pub"] = _limpo(artigo.get("datePublished"))
        resultado["autor"] = _nome(artigo.get("author"), por_id)
    crs = resultado["claim_reviews"]
    if crs:
        resultado["headline"] = resultado["headline"] or crs[0]["_headline"]
        resultado["data_pub"] = resultado["data_pub"] or crs[0]["data_pub"]
        resultado["autor"] = resultado["autor"] or crs[0]["autor"]
    for cr in crs:
        cr["trecho"] = cr.pop("_review_body")
        cr.pop("_headline")
    return resultado


# ----------------------------------------------------------------------------- uso direto
_CATALOGO_CACHE: Dict[str, Any] = {}


def _agencia_do_catalogo(url: Optional[str]) -> Optional[str]:
    """id do portal no catálogo (chave de override da tabela de selos), se a URL for conhecida."""
    if not url:
        return None
    try:
        from .catalogo import Catalogo

        if "cat" not in _CATALOGO_CACHE:
            _CATALOGO_CACHE["cat"] = Catalogo.carregar()
        portal = _CATALOGO_CACHE["cat"].por_url(url)
        return portal.get("id") if portal else None
    except Exception:
        return None


def veredito_da_pagina(html: str, url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Veredito oficial de uma página qualquer (busca aberta, deep crawl) via ClaimReview.

    -> {"selo_original", "veredito" (enum de selos.VEREDITOS ou None se o selo não
    está na tabela), "afirmacao_checada", "agencia", "origem": "claimreview",
    "n_claim_reviews", "claims": [ {afirmacao_checada, selo_original, veredito}, … ]}
    ou None se a página não publica ClaimReview. `agencia` = id do portal no
    catálogo quando a URL é conhecida, senão o nome do publisher do JSON-LD.
    Página com várias checagens (debate/discurso): o principal é o primeiro; o
    chamador deve casar a afirmação certa em `claims`."""
    from . import selos

    try:
        info = extrair(html or "")
    except Exception:
        return None
    crs = [c for c in info["claim_reviews"] if c.get("selo_original") or c.get("afirmacao_checada")]
    if not crs:
        return None
    agencia = _agencia_do_catalogo(url) or next((c["agencia"] for c in crs if c.get("agencia")), None)
    claims = [{"afirmacao_checada": c.get("afirmacao_checada"), "selo_original": c.get("selo_original"),
               "veredito": selos.normalizar(c.get("selo_original"), agencia)} for c in crs]
    principal = next((c for c in claims if c["veredito"]), claims[0])
    return {
        "selo_original": principal["selo_original"],
        "veredito": principal["veredito"],
        "afirmacao_checada": principal["afirmacao_checada"],
        "agencia": agencia,
        "origem": "claimreview",
        "n_claim_reviews": len(claims),
        "claims": claims,
    }
