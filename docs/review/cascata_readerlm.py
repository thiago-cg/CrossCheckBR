"""Cascata de extracao de checagens: JSON-LD ClaimReview -> trafilatura -> ReaderLM-v2 -> falha explicita.

Experimento (nao producao). Dependencias: curl_cffi, pydantic>=2, beautifulsoup4 (ja vem com crawl4ai), trafilatura (opcional).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Optional

import curl_cffi.requests as _curl
from curl_cffi.requests.exceptions import RequestException as _HTTPError
from pydantic import BaseModel, ValidationError

BASE_URL = os.getenv("READERLM_BASE_URL", os.getenv("UNSLOTH_BASE_URL", "http://127.0.0.1:8888/v1"))
MODEL = os.getenv("READERLM_MODEL", "mradermacher/ReaderLM-v2-GGUF")  # id exato: GET /v1/models
API_KEY = os.getenv("READERLM_API_KEY", os.getenv("UNSLOTH_API_KEY", "not-needed"))
MAX_INPUT_CHARS = 120_000  # ~30-35k tokens de HTML; acima disso, recorte antes (seletor do portal)

# ---------------------------------------------------------------- contrato
class Checagem(BaseModel):
    url: str
    titulo: Optional[str] = None
    data_publicacao: Optional[str] = None
    autor: Optional[str] = None
    afirmacao_checada: Optional[str] = None
    veredito: Optional[str] = None          # selo literal do portal ("Falso", "É #FAKE", ...)
    trecho_evidencia: Optional[str] = None
    metodo: str = "falha"                    # jsonld | trafilatura | readerlm | falha
    avisos: list[str] = []

# Schema enviado ao ReaderLM (JSON Schema "cru", como no exemplo oficial da Jina).
# Descricoes curtas em ingles tendem a casar melhor com o treino (nao verificado).
SCHEMA_READERLM = json.dumps({
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "description": "Headline (h1) of the fact-check article"},
        "data_publicacao": {"type": "string", "description": "Publication date as written on the page"},
        "autor": {"type": "string", "description": "Author / byline"},
        "afirmacao_checada": {"type": "string", "description": "The claim being fact-checked, verbatim"},
        "veredito": {"type": "string", "description": "Verdict label/seal as written, e.g. Falso, Enganoso, #FAKE"},
        "trecho_evidencia": {"type": "string", "description": "Verbatim sentence from the article that justifies the verdict"},
    },
    "required": ["titulo", "afirmacao_checada", "veredito"],
}, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- 1) JSON-LD
def _iter_ld(html: str):
    for raw in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                yield node
                stack.extend(v for k, v in node.items() if k in ("@graph", "itemReviewed", "mainEntity") and isinstance(v, (list, dict)))
                if isinstance(node.get("@graph"), list):
                    stack.extend(node["@graph"])
            elif isinstance(node, list):
                stack.extend(node)

def _types(node: dict) -> set[str]:
    t = node.get("@type", [])
    return set(t if isinstance(t, list) else [t])   # Aos Fatos usa ["Review","ClaimReview"]

def _name(x: Any) -> Optional[str]:
    if isinstance(x, list):
        return ", ".join(filter(None, (_name(i) for i in x))) or None
    if isinstance(x, dict):
        return x.get("name")
    return x or None

def via_jsonld(url: str, html: str) -> Optional[Checagem]:
    article = next((n for n in _iter_ld(html) if _types(n) & {"NewsArticle", "Article", "ReportageNewsArticle"}), {})
    for n in _iter_ld(html):
        if "ClaimReview" in _types(n):
            rating = n.get("reviewRating") or {}
            return Checagem(
                url=url,
                titulo=article.get("headline") or n.get("name") or n.get("headline"),
                data_publicacao=n.get("datePublished") or article.get("datePublished"),
                autor=_name(n.get("author")) or _name(article.get("author")),
                afirmacao_checada=n.get("claimReviewed"),
                veredito=rating.get("alternateName") or rating.get("ratingValue"),
                metodo="jsonld",
            )
    return None

# ---------------------------------------------------------------- 2) trafilatura
def via_trafilatura(url: str, html: str) -> tuple[Optional[dict], Optional[str]]:
    """Retorna (metadados, texto). Nao preenche veredito: isso fica p/ ReaderLM ou regex por portal."""
    try:
        import trafilatura
    except ImportError:
        return None, None
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True, include_comments=False,
                                      favor_precision=True)
    if doc is not None and not isinstance(doc, dict):   # trafilatura 2.x devolve Document
        doc = doc.as_dict()
    if not doc or not doc.get("text"):
        return None, None
    meta = {"titulo": doc.get("title"), "data_publicacao": doc.get("date"), "autor": doc.get("author")}
    return meta, doc["text"]

# ---------------------------------------------------------------- 3) ReaderLM-v2
_F = re.I | re.M | re.S
def limpar_html(html: str, seletor_corpo: Optional[str] = None) -> str:
    """Pre-limpeza oficial da Jina + extras (nav/footer/atributos). Se houver seletor do portal
    (catalogo.json -> css_selectors.corpo), recorta o container antes (reduz tokens 10-50x)."""
    if seletor_corpo:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.select_one("h1")
        corpo = soup.select_one(seletor_corpo)
        if corpo is not None:
            html = f"<html><body>{h1 or ''}{corpo}</body></html>"
    for pat in (r"<[ ]*script.*?\/[ ]*script[ ]*>", r"<[ ]*style.*?\/[ ]*style[ ]*>", r"<[ ]*meta.*?>",
                r"<[ ]*!--.*?--[ ]*>", r"<[ ]*link.*?>"):
        html = re.sub(pat, "", html, flags=_F)
    html = re.sub(r"(<svg[^>]*>)(.*?)(<\/svg>)", r"\1\3", html, flags=re.S)
    html = re.sub(r'<img[^>]+src="data:image/[^;]+;base64,[^"]+"[^>]*>', '<img src="#"/>', html)
    html = re.sub(r"<(nav|footer|aside|form|iframe|noscript|button|select)\b.*?</\1>", "", html, flags=_F)
    html = re.sub(r'\s(class|style|id|data-[\w-]+|aria-[\w-]+|role|srcset|sizes|loading|decoding|width|height)="[^"]*"', "", html)
    return re.sub(r"\s+", " ", html).strip()

def prompt_readerlm(html_limpo: str, schema: Optional[str] = None) -> str:
    """Formato EXATO do model card (create_prompt). O chat template (ChatML + system da Jina)
    e aplicado pelo servidor a partir do GGUF; aqui so montamos o conteudo da mensagem 'user'."""
    if schema:
        instr = "Extract the specified information from a list of news threads and present it in a structured JSON format."
        return f"{instr}\n```html\n{html_limpo}\n```\nThe JSON schema is as follows:```json\n{schema}\n```"
    instr = "Extract the main content from the given HTML and convert it to Markdown format."
    return f"{instr}\n```html\n{html_limpo}\n```"

def chamar_readerlm(conteudo: str, max_tokens: int = 1024, timeout: float = 300) -> str:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": conteudo}],   # sem system: o template injeta o da Jina
        "temperature": 0,             # model card: temperature=0 / do_sample=False
        "max_tokens": max_tokens,
        "repeat_penalty": 1.08,       # nome llama.cpp
        "repetition_penalty": 1.08,   # nome HF/vLLM (um dos dois sera ignorado; passthrough no Unsloth nao verificado)
        "top_k": 1,
        "enable_tools": False,        # Unsloth Studio: desliga web-search/code-exec server-side
    }
    r = _curl.post(f"{BASE_URL}/chat/completions", json=body, timeout=timeout,
                   headers={"Authorization": f"Bearer {API_KEY}"}, impersonate="chrome")
    r.raise_for_status()
    choice = r.json()["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError("ReaderLM truncou (finish_reason=length): provavel loop/degeneracao ou max_tokens baixo")
    return choice["message"]["content"]

def _json_da_resposta(txt: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", txt, re.S) or re.search(r"(\{.*\})", txt, re.S)
    if not m:
        raise ValueError("sem objeto JSON na resposta")
    return json.loads(m.group(1))

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", re.sub(r"\s+", " ", s)).strip()

def _ancorado(valor: Optional[str], texto_fonte: str, min_ratio: float = 0.8) -> bool:
    """Anti-alucinacao: >=80% dos tokens do valor precisam aparecer no texto da pagina."""
    if not valor:
        return True
    toks = _norm(valor).split()
    fonte = set(_norm(texto_fonte).split())
    return bool(toks) and sum(t in fonte for t in toks) / len(toks) >= min_ratio

VAZIOS = {"", "unknown", "n/a", "none", "null", "desconhecido"}  # o exemplo oficial devolve "Unknown"

def via_readerlm(url: str, html: str, seletor_corpo: Optional[str] = None) -> Checagem:
    limpo = limpar_html(html, seletor_corpo)
    if len(limpo) > MAX_INPUT_CHARS:
        return Checagem(url=url, metodo="falha", avisos=[f"HTML limpo grande demais ({len(limpo)} chars); recorte com seletor"])
    bruto = chamar_readerlm(prompt_readerlm(limpo, SCHEMA_READERLM))
    dados = {k: (None if isinstance(v, str) and v.strip().lower() in VAZIOS else v)
             for k, v in _json_da_resposta(bruto).items()}
    ck = Checagem(url=url, metodo="readerlm", **{k: dados.get(k) for k in
                  ("titulo", "data_publicacao", "autor", "afirmacao_checada", "veredito", "trecho_evidencia")})
    texto_fonte = re.sub(r"<[^>]+>", " ", limpo)
    for campo in ("afirmacao_checada", "trecho_evidencia", "veredito", "titulo"):
        if not _ancorado(getattr(ck, campo), texto_fonte):
            ck.avisos.append(f"{campo} nao ancorado no HTML (possivel alucinacao) -> descartado")
            setattr(ck, campo, None)
    return ck

# ---------------------------------------------------------------- orquestracao
def extrair_checagem(url: str, html: str, seletor_corpo: Optional[str] = None) -> Checagem:
    meta, _texto = via_trafilatura(url, html)
    if (ck := via_jsonld(url, html)) and ck.afirmacao_checada and ck.veredito:
        for k, v in (meta or {}).items():                            # completa buracos (ex.: Aos Fatos sem headline)
            if v and not getattr(ck, k):
                setattr(ck, k, v)
        return ck                                                    # deterministico, fonte "oficial"
    try:
        ck = via_readerlm(url, html, seletor_corpo)
    except (_HTTPError, RuntimeError, ValueError, ValidationError) as e:
        return Checagem(url=url, metodo="falha", avisos=[f"readerlm: {type(e).__name__}: {e}"], **(meta or {}))
    if meta:  # metadados do trafilatura (deterministicos) tem prioridade sobre os do LLM
        for k, v in meta.items():
            if v:
                setattr(ck, k, v)
    if not (ck.afirmacao_checada and ck.veredito):
        ck.metodo = "falha"
        ck.avisos.append("sem afirmacao/veredito ancorados")
    return ck

if __name__ == "__main__":
    import sys
    u = sys.argv[1]
    h = _curl.get(u, allow_redirects=True, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, impersonate="chrome").text
    print(extrair_checagem(u, h, sys.argv[2] if len(sys.argv) > 2 else None).model_dump_json(indent=2))
