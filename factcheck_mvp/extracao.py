"""Extração de conteúdo em cascata: HTML de uma página -> corpo principal + metadados.

Por que cascata (review A2/A4): nenhum extrator isolado serve para a busca
aberta. O regex antigo (`aprofundar.extrair_corpo`) funciona em WordPress/Globo
mas devolve menu em layouts diferentes, e seletores CSS por portal quebram em
silêncio. Aqui cada nível tem uma condição de VITÓRIA explícita (texto ≥ 500
chars que não parece navegação); quem não passa cede a vez ao próximo e o
motivo vira aviso. Ordem:

  (a) JSON-LD `NewsArticle.articleBody` (+ ClaimReview -> `veredito_pagina`),
      via `factcheck_mvp.jsonld` — texto do próprio editor, custo zero;
  (b) trafilatura (favor_precision, sem comentários) — determinístico, ms;
  (c) seletor `corpo` do catálogo (hint por portal, pode estar velho);
  (d) regex de <p> com filtro de densidade de links (o extrator antigo);
  (e) ReaderLM-v2 — SÓ com `EXTRACAO_READERLM=1` (default desligado: carregar
      o ReaderLM no Unsloth Studio troca o modelo que o juiz usa). Endpoint
      OpenAI-compatível em `READERLM_BASE_URL` (default 127.0.0.1:8889/v1,
      porta dedicada; ver docs/review/r4_readerlm.md);
  (f) falha explícita: `metodo="falha"` (o melhor texto parcial fica em
      `texto`, mas quem consome deve tratar como corpo NÃO lido).

`veredito_pagina` (ClaimReview da própria página) é independente do nível que
venceu o corpo: uma checagem com articleBody curto ainda traz o selo oficial.
Função pura (sem rede, exceto o nível ReaderLM, que passa por `replay.llm_post`).
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import telemetria

log = logging.getLogger("factcheck.extracao")
logging.getLogger("trafilatura").setLevel(logging.ERROR)  # avisos de parsing poluem o log

MIN_CHARS = 500  # abaixo disso não é "corpo lido" (review A4: BBC truncada dava 355)
METODOS = ("jsonld", "trafilatura", "seletor", "regex", "readerlm", "falha")


@dataclass
class Extracao:
    texto: str = ""                 # corpo principal limpo (entidades decodificadas)
    titulo: str = ""
    data_pub: Optional[str] = None
    autor: Optional[str] = None
    metodo: str = "falha"           # um de METODOS
    veredito_pagina: Optional[dict] = None  # ClaimReview da página (ou ReaderLM ancorado)
    avisos: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.metodo != "falha" and len(self.texto) >= MIN_CHARS


# ----------------------------------------------------------------------------- limpeza
def limpar_texto(texto: Optional[str]) -> str:
    """Decodifica entidades (inclusive duplas: &amp;#8211;), tira tags soltas,
    normaliza espaços por linha e colapsa linhas em branco repetidas."""
    if not texto:
        return ""
    t = _html.unescape(_html.unescape(texto))
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("\xa0", " ").replace("​", "")
    linhas = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in t.split("\n")]
    t = "\n".join(linhas)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def parece_navegacao(texto: str) -> Optional[str]:
    """Motivo se o texto parece menu/listagem em vez de matéria; None se ok.

    Heurísticas baratas (mesma ideia do filtro de densidade de links do regex):
    markdown de links, maioria de linhas curtas (itens de menu) e ausência de
    frases (texto corrido tem pontuação de fim de frase).
    """
    linhas = [ln for ln in (texto or "").splitlines() if ln.strip()]
    if not linhas:
        return "vazio"
    n_links = texto.count("](") + len(re.findall(r"https?://", texto))
    if n_links >= 10 and n_links / len(linhas) > 0.3:
        return f"parece navegação ({n_links} links em {len(linhas)} linhas)"
    if len(linhas) >= 8:
        curtas = sum(1 for ln in linhas if len(ln.strip()) < 40)
        if curtas / len(linhas) > 0.6:
            return f"parece navegação ({curtas}/{len(linhas)} linhas curtas)"
    palavras = texto.split()
    frases = len(re.findall(r"[.!?…](?:[\"'”»)]*)(?:\s|$)", texto))
    if len(palavras) >= 80 and frases / len(palavras) < 0.01:
        return f"parece navegação (sem frases: {frases} fins de frase em {len(palavras)} palavras)"
    return None


def _avaliar(texto: str) -> Optional[str]:
    """None se o texto pode vencer o nível; senão o motivo da recusa."""
    if len(texto) < MIN_CHARS:
        return f"curto ({len(texto)} < {MIN_CHARS} chars)"
    return parece_navegacao(texto)


# ----------------------------------------------------------------------------- (d) regex
def paragrafos_regex(html: str, max_paragrafos: int = 60) -> List[str]:
    """<p> com texto real: remove blocos estruturais e descarta parágrafo-menu
    (densidade de links > 0.5) ou curto (< 40 chars). Base do extrator antigo."""
    txt = html or ""
    txt = re.sub(r"<(header|nav|footer|aside|form|noscript)[^>]*>.*?</\1>",
                 " ", txt, flags=re.I | re.S)
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", txt, flags=re.I | re.S)
    uteis: List[str] = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", txt, re.S | re.I)[: max_paragrafos * 3]:
        links = re.findall(r"<a[^>]*>.*?</a>", p, flags=re.S | re.I)
        limpo = re.sub(r"\s+", " ", limpar_texto(p)).strip()
        if not limpo or len(limpo) < 40:
            continue
        dens = (sum(len(limpar_texto(a).strip()) for a in links) / max(1, len(limpo)))
        if dens > 0.5:  # parágrafo-menu: texto quase todo dentro de links
            continue
        uteis.append(limpo)
        if len(uteis) >= max_paragrafos:
            break
    return uteis


def titulo_html(html: str) -> str:
    m = (re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html or "", re.I)
         or re.search(r"<h1[^>]*>(.*?)</h1>", html or "", re.I | re.S)
         or re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S))
    return re.sub(r"\s+", " ", limpar_texto(m.group(1))).strip()[:300] if m else ""


# ----------------------------------------------------------------------------- (c) seletor
_TAGS_RUIDO = ("script", "style", "noscript", "nav", "aside", "footer", "header", "form",
               "figure", "iframe", "button", "svg")


def texto_por_seletor(html: str, seletor: str) -> str:
    """Texto do maior elemento que casa o seletor CSS (lista "a, b" aceita)."""
    from bs4 import BeautifulSoup  # dependência do crawl4ai; import tardio (nível raro)
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    els = soup.select(seletor)
    if not els:
        return ""
    melhor = ""
    for el in els[:20]:
        for ruido in el.find_all(_TAGS_RUIDO):
            ruido.decompose()
        ps = [p.get_text(" ", strip=True) for p in el.find_all("p")]
        ps = [p for p in ps if len(p) >= 40]
        texto = "\n\n".join(ps) if ps else el.get_text("\n", strip=True)
        texto = limpar_texto(texto)
        if len(texto) > len(melhor):
            melhor = texto
    return melhor


# ----------------------------------------------------------------------------- (b) trafilatura
def _trafilatura(html: str, url: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(doc_dict, erro). Nunca levanta."""
    try:
        import trafilatura
    except ImportError:
        return None, "trafilatura não instalado (pip install trafilatura)"
    try:
        doc = trafilatura.bare_extraction(html, url=url or None, with_metadata=True,
                                          include_comments=False, favor_precision=True)
    except Exception as e:
        return None, f"trafilatura: {type(e).__name__}: {e}"[:200]
    if doc is not None and not isinstance(doc, dict):  # trafilatura 2.x devolve Document
        doc = doc.as_dict()
    return (doc or None), None


# ----------------------------------------------------------------------------- (e) ReaderLM-v2
def readerlm_ativo() -> bool:
    return os.getenv("EXTRACAO_READERLM", "0").strip().lower() in ("1", "true", "sim", "on")


def _cfg_readerlm() -> Dict[str, Any]:
    return {
        "base_url": os.getenv("READERLM_BASE_URL", "http://127.0.0.1:8889/v1").rstrip("/"),
        "modelo": os.getenv("READERLM_MODEL", "mradermacher/ReaderLM-v2-GGUF"),
        "api_key": os.getenv("READERLM_API_KEY", "not-needed"),
        "timeout": float(os.getenv("READERLM_TIMEOUT_S", "180")),
        "max_tokens": int(os.getenv("READERLM_MAX_TOKENS", "1024")),
        "max_chars": int(os.getenv("READERLM_MAX_CHARS", "120000")),  # ~30-35k tokens de HTML
    }


# Descrições curtas em inglês, como no exemplo oficial da Jina (JSON Schema "cru").
_CAMPOS_READERLM = {
    "titulo": "Headline (h1) of the article",
    "data_publicacao": "Publication date as written on the page",
    "autor": "Author / byline",
    "afirmacao_checada": "The claim being fact-checked, verbatim",
    "veredito": "Verdict label/seal as written, e.g. Falso, Enganoso, #FAKE, Verdadeiro",
    "trecho_evidencia": "Verbatim sentence from the article that justifies the verdict",
}
_CAMPO_CORPO = ("corpo", "Main article text, verbatim (first paragraphs, up to about 1500 characters)")


def schema_readerlm(com_corpo: bool = True) -> str:
    props = {k: {"type": "string", "description": d} for k, d in _CAMPOS_READERLM.items()}
    if com_corpo:
        props[_CAMPO_CORPO[0]] = {"type": "string", "description": _CAMPO_CORPO[1]}
    return json.dumps({"type": "object", "properties": props,
                       "required": ["titulo", "afirmacao_checada", "veredito"]},
                      ensure_ascii=False, indent=2)


_F = re.I | re.M | re.S
_SELETORES_RECORTE = ("[itemprop='articleBody']", "article", "main")


def remover_jsonld(html: str) -> str:
    return re.sub(r"<script\b[^>]*application/ld\+json[^>]*>.*?</script\s*>", "", html or "", flags=_F)


def _recortar_artigo(html: str, seletor_corpo: Optional[str]) -> str:
    """Container do artigo (seletor do catálogo, senão articleBody/article/main) + h1.
    Sem bs4 ou sem container com ≥ 200 chars de texto: devolve o HTML inteiro."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return html
    h1 = soup.select_one("h1")
    for sel in ([seletor_corpo] if seletor_corpo else []) + list(_SELETORES_RECORTE):
        try:
            corpo = soup.select_one(sel)
        except Exception:
            continue
        if corpo is not None and len(corpo.get_text(strip=True)) >= 200:
            titulo = h1 if (h1 is not None and h1 not in corpo.find_all("h1")) else ""
            return f"<html><body>{titulo or ''}{corpo}</body></html>"
    return html


def limpar_html_readerlm(html: str, seletor_corpo: Optional[str] = None,
                         recortar: bool = True) -> str:
    """Pré-limpeza oficial da Jina (script/style/meta/comentários/link, svg vazio,
    base64 -> #) + extras (nav/footer/atributos) e RECORTE do container do artigo
    (seletor do catálogo, senão articleBody/article/main) + h1: reduz tokens 10-50x.
    `recortar=False` só limpa (experimento com/sem recorte)."""
    html = html or ""
    if recortar:
        html = _recortar_artigo(html, seletor_corpo)
    for pat in (r"<[ ]*script.*?\/[ ]*script[ ]*>", r"<[ ]*style.*?\/[ ]*style[ ]*>", r"<[ ]*meta.*?>",
                r"<[ ]*!--.*?--[ ]*>", r"<[ ]*link.*?>"):
        html = re.sub(pat, "", html, flags=_F)
    html = re.sub(r"(<svg[^>]*>)(.*?)(<\/svg>)", r"\1\3", html, flags=re.S)
    html = re.sub(r'<img[^>]+src="data:image/[^;]+;base64,[^"]+"[^>]*>', '<img src="#"/>', html)
    html = re.sub(r"<(nav|footer|aside|form|iframe|noscript|button|select)\b.*?</\1>", "", html, flags=_F)
    html = re.sub(r'\s(class|style|id|data-[\w-]+|aria-[\w-]+|role|srcset|sizes|loading|decoding|width|height)="[^"]*"',
                  "", html)
    return re.sub(r"\s+", " ", html).strip()


def prompt_readerlm(html_limpo: str, schema: Optional[str] = None) -> str:
    """Conteúdo EXATO da mensagem `user` do model card (create_prompt). O template
    ChatML (com o system da Jina) vem do GGUF: não mande system."""
    if schema:
        instr = ("Extract the specified information from a list of news threads "
                 "and present it in a structured JSON format.")
        return f"{instr}\n```html\n{html_limpo}\n```\nThe JSON schema is as follows:```json\n{schema}\n```"
    instr = "Extract the main content from the given HTML and convert it to Markdown format."
    return f"{instr}\n```html\n{html_limpo}\n```"


class FalhaReaderLM(RuntimeError):
    pass


def chamar_readerlm(conteudo: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """POST /chat/completions via replay.llm_post. Levanta FalhaReaderLM."""
    from . import replay
    cfg = cfg or _cfg_readerlm()
    payload = {
        "model": cfg["modelo"],
        "messages": [{"role": "user", "content": conteudo}],  # sem system: o template injeta o da Jina
        "temperature": 0,             # generation_config do repo usa 0.65: mandar 0 SEMPRE
        "max_tokens": cfg["max_tokens"],
        "repeat_penalty": 1.08,       # nome llama.cpp
        "repetition_penalty": 1.08,   # nome HF/vLLM (um dos dois é ignorado)
        "top_k": 1,
        "enable_tools": False,        # Unsloth Studio: sem web-search/code-exec server-side
    }
    try:
        r = replay.llm_post(f"{cfg['base_url']}/chat/completions", payload,
                            headers={"Authorization": f"Bearer {cfg['api_key']}"},
                            timeout=cfg["timeout"], motor="readerlm", finalidade="extracao-readerlm")
    except Exception as e:
        raise FalhaReaderLM(f"{type(e).__name__}: {e}"[:200])
    if r.status_code >= 400:
        raise FalhaReaderLM(f"HTTP {r.status_code}: {r.text[:150]}")
    try:
        escolha = r.json()["choices"][0]
    except Exception as e:
        raise FalhaReaderLM(f"resposta inválida: {e}"[:200])
    if escolha.get("finish_reason") == "length":
        raise FalhaReaderLM("finish_reason=length (loop de repetição ou truncamento)")
    return (escolha.get("message") or {}).get("content") or ""


def _json_da_resposta(txt: str) -> Dict[str, Any]:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", txt, re.S) or re.search(r"(\{.*\})", txt, re.S)
    if not m:
        raise FalhaReaderLM("sem objeto JSON na resposta")
    try:
        d = json.loads(m.group(1), strict=False)
    except ValueError as e:
        raise FalhaReaderLM(f"JSON inválido: {e}"[:200])
    if not isinstance(d, dict):
        raise FalhaReaderLM("JSON não é objeto")
    return d


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def ancorado(valor: Optional[str], texto_fonte: str, min_ratio: float = 0.8,
             _vocab: Optional[set] = None) -> bool:
    """Anti-alucinação: ≥ 80% das palavras do valor aparecem no texto da página."""
    toks = _norm(valor or "").split()
    if not toks:
        return False
    fonte = _vocab if _vocab is not None else set(_norm(texto_fonte).split())
    return sum(t in fonte for t in toks) / len(toks) >= min_ratio


_VAZIOS = {"", "unknown", "n/a", "na", "none", "null", "desconhecido", "não informado"}


def readerlm_campos(html: str, seletor_corpo: Optional[str] = None, com_corpo: bool = True,
                    cfg: Optional[Dict[str, Any]] = None,
                    recortar: bool = True) -> Tuple[Dict[str, str], List[str]]:
    """Roda o ReaderLM (schema) sobre o HTML SEM JSON-LD. -> (campos ancorados, avisos).

    Campo não ancorado no texto da página é descartado (aviso). Levanta FalhaReaderLM
    para falhas de chamada (rede, HTTP, length, JSON inválido, HTML grande demais).
    """
    cfg = cfg or _cfg_readerlm()
    limpo = limpar_html_readerlm(remover_jsonld(html), seletor_corpo, recortar)
    if len(limpo) > cfg["max_chars"]:
        raise FalhaReaderLM(f"HTML limpo grande demais ({len(limpo)} > {cfg['max_chars']} chars)")
    bruto = _json_da_resposta(chamar_readerlm(prompt_readerlm(limpo, schema_readerlm(com_corpo)), cfg))
    vocab = set(_norm(limpar_texto(re.sub(r"<[^>]+>", " ", limpo))).split())
    campos: Dict[str, str] = {}
    avisos: List[str] = []
    for k, v in bruto.items():
        if not isinstance(v, str) or v.strip().lower() in _VAZIOS:
            continue
        if ancorado(v, "", _vocab=vocab):
            campos[k] = limpar_texto(v)
        else:
            avisos.append(f"readerlm: campo {k} não ancorado na página (alucinação?) -> descartado")
    return campos, avisos


def _veredito_readerlm(campos: Dict[str, str]) -> Optional[dict]:
    selo = campos.get("veredito")
    if not selo:
        return None
    try:
        from . import selos
        normalizado = selos.normalizar(selo, None)
    except Exception:
        normalizado = None
    return {"selo_original": selo, "veredito": normalizado,
            "afirmacao_checada": campos.get("afirmacao_checada"), "agencia": None,
            "origem": "readerlm", "trecho_evidencia": campos.get("trecho_evidencia"),
            "n_claim_reviews": 0, "claims": []}


# ----------------------------------------------------------------------------- orquestração
def extrair(html: str, url: str = "", seletor_corpo: Optional[str] = None) -> Extracao:
    """Cascata JSON-LD -> trafilatura -> seletor -> regex -> [ReaderLM] -> falha.

    Nunca levanta. Degradações por erro (módulo ausente, exceção) emitem
    `telemetria.fallback("extracao.<nivel>", motivo)`; a falha final emite
    `fallback("extracao", motivo)`.
    """
    ex = Extracao()
    html = html or ""
    candidatos: List[Tuple[str, str]] = []  # (metodo, texto) recusados: guarda o melhor p/ a falha

    def _tentar(metodo: str, texto: str) -> bool:
        texto = limpar_texto(texto)
        motivo = _avaliar(texto)
        if motivo is None:
            ex.texto, ex.metodo = texto, metodo
            return True
        ex.avisos.append(f"{metodo}: {motivo}")
        if texto:
            candidatos.append((metodo, texto))
        return False

    if not html.strip():
        ex.avisos.append("html vazio")
        telemetria.fallback("extracao", "html vazio", url=url)
        return ex

    # (a) JSON-LD: corpo do editor + veredito oficial (ClaimReview)
    info_ld: Dict[str, Any] = {}
    try:
        from . import jsonld
    except ImportError as e:  # módulo de outro agente ainda não existe: pula o nível
        jsonld = None
        ex.avisos.append(f"jsonld: módulo indisponível ({e})")
        telemetria.fallback("extracao.jsonld", f"módulo indisponível: {e}", url=url)
    if jsonld is not None:
        try:
            info_ld = jsonld.extrair(html) or {}
            ex.veredito_pagina = jsonld.veredito_da_pagina(html, url)
        except Exception as e:
            ex.avisos.append(f"jsonld: {type(e).__name__}: {e}"[:200])
            telemetria.fallback("extracao.jsonld", f"{type(e).__name__}: {e}", url=url)
        ex.titulo = limpar_texto(info_ld.get("headline") or "")
        ex.data_pub = info_ld.get("data_pub") or None
        ex.autor = info_ld.get("autor") or None
        corpo_ld = info_ld.get("article_body") or ""
        if corpo_ld:
            _tentar("jsonld", corpo_ld)
        else:
            ex.avisos.append("jsonld: sem articleBody")

    # (b) trafilatura: também completa metadados que o JSON-LD não trouxe
    precisa_meta = not (ex.titulo and ex.data_pub)
    if ex.metodo == "falha" or precisa_meta:
        doc, erro = _trafilatura(html, url)
        if erro:
            ex.avisos.append(erro)
            telemetria.fallback("extracao.trafilatura", erro, url=url)
        doc = doc or {}
        ex.titulo = ex.titulo or limpar_texto(doc.get("title") or "")
        ex.data_pub = ex.data_pub or doc.get("date") or None
        ex.autor = ex.autor or (limpar_texto(doc.get("author") or "") or None)
        if ex.metodo == "falha":
            if doc.get("text"):
                _tentar("trafilatura", doc["text"])
            elif not erro:
                ex.avisos.append("trafilatura: sem texto")

    # (c) seletor `corpo` do catálogo (hint por portal)
    if ex.metodo == "falha" and seletor_corpo:
        try:
            _tentar("seletor", texto_por_seletor(html, seletor_corpo))
        except Exception as e:
            ex.avisos.append(f"seletor: {type(e).__name__}: {e}"[:200])
            telemetria.fallback("extracao.seletor", f"{type(e).__name__}: {e}", url=url,
                                seletor=seletor_corpo)

    # (d) regex de <p> (extrator antigo, sem o corte em 12 parágrafos)
    if ex.metodo == "falha":
        try:
            _tentar("regex", "\n\n".join(paragrafos_regex(html)))
        except Exception as e:  # regex patológica em HTML enorme: nunca derruba
            ex.avisos.append(f"regex: {type(e).__name__}: {e}"[:200])
            telemetria.fallback("extracao.regex", f"{type(e).__name__}: {e}", url=url)

    # (e) ReaderLM-v2 (opt-in): corpo + veredito ancorados no texto da página
    if ex.metodo == "falha" and readerlm_ativo():
        try:
            campos, avisos = readerlm_campos(html, seletor_corpo)
            ex.avisos.extend(avisos)
            if campos.get("corpo"):
                _tentar("readerlm", campos["corpo"])
            else:
                ex.avisos.append("readerlm: sem corpo ancorado")
            ex.titulo = ex.titulo or campos.get("titulo", "")
            ex.data_pub = ex.data_pub or campos.get("data_publicacao")
            ex.autor = ex.autor or campos.get("autor")
            if ex.veredito_pagina is None:
                ex.veredito_pagina = _veredito_readerlm(campos)
        except FalhaReaderLM as e:
            ex.avisos.append(f"readerlm: {e}")
            telemetria.fallback("extracao.readerlm", str(e), url=url)
        except Exception as e:
            ex.avisos.append(f"readerlm: {type(e).__name__}: {e}"[:200])
            telemetria.fallback("extracao.readerlm", f"{type(e).__name__}: {e}", url=url)

    ex.titulo = ex.titulo or titulo_html(html)

    # (f) falha explícita: guarda o melhor parcial, mas metodo="falha"
    if ex.metodo == "falha":
        if candidatos:
            ex.texto = max(candidatos, key=lambda c: len(c[1]))[1]
        motivo = "nenhum nível passou: " + "; ".join(ex.avisos)[:400]
        telemetria.fallback("extracao", motivo, url=url, n_chars=len(ex.texto))
    return ex
