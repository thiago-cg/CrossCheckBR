"""Ingestor de checagens: RSS/Atom (+ WP REST) das agências -> data/checagens.jsonl.

Substitui o br_news_crawler como fonte do índice de vereditos (achado A1 da
revisão: o índice antigo tinha 17 docs, todos homepages/menus). Usa só os
canais que as agências publicam para máquinas: feed, JSON-LD ClaimReview,
NewsArticle.articleBody. Nenhum LLM, nenhum seletor CSS.

Uso:
    python3 -m factcheck_mvp.ingestor [--max-por-feed N] [--sem-paginas]
                                      [--paginas-feed P] [--saida ARQ]

Veredito, em ordem (a primeira que achar vence):
  (a) ClaimReview do JSON-LD da página do artigo           -> origem "claimreview"
  (b) regex de título por agência (#FAKE/#FATO, "É falso que…") ou
      categoria do RSS que é selo na tabela (E-farsas)       -> origem "rss-titulo"
  (b') frase-selo no começo do corpo ("É falso.", "Boato –") -> origem "articlebody"
  (c) nada -> veredito None, registrado mesmo assim          -> origem "rss-titulo"

Idempotente: faz merge com o arquivo existente (dedupe por URL canônica, sem
query/fragmento/utm). Registros com origem "manual" nunca são sobrescritos.
URLs listadas em data/checagens_holdout_excluidas.json nunca entram (eval
`fora_do_indice`).
"""
from __future__ import annotations

import argparse
import html as _html
import json
import re
import sys
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from . import jsonld, selos

BASE_DIR = Path(__file__).resolve().parent
ARQ_CHECAGENS = BASE_DIR / "data" / "checagens.jsonl"
ARQ_EXCLUIDAS = BASE_DIR / "data" / "checagens_holdout_excluidas.json"
ARQ_CATALOGO = BASE_DIR / "data" / "catalogo.json"

UA_NAVEGADOR = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TETO_BYTES = 5 * 1024 * 1024  # BBC tem HTML de 9 MB; agências cabem folgado em 5 MB
INTERVALO_HOST = 1.0  # segundos entre requisições ao mesmo host
TIMEOUT = 25.0

# Feeds conferidos ao vivo em 28/09/2026 (status registrado no relatório do ingestor).
# `agencia` = id do portal no catálogo (é a chave de override em data/selos.json).
FONTES: List[Dict[str, Any]] = [
    {"agencia": "lupa", "nome": "Lupa",
     "feeds": ["https://www.agencialupa.org/feed/"],
     # o feed da Lupa ignora ?paged=N; a API REST do WordPress pagina de verdade
     "wpjson": "https://www.agencialupa.org/wp-json/wp/v2/posts"},
    {"agencia": "boatos", "nome": "Boatos.org",
     "feeds": ["https://www.boatos.org/feed"], "paginar": True,
     # API REST pagina o histórico inteiro (~15 mil posts em 10/2026; o RSS só os recentes)
     "wpjson": "https://www.boatos.org/wp-json/wp/v2/posts",
     # fora: Eleições-2026 (páginas de resultado por município), English, Español, Lista, Opinião
     "wpjson_params": "&categories_exclude=9312,1721,3684,1533,535",
     "ignorar_titulo": r"^resultado das eleicoes",  # mesmas páginas de resultado, vindas pelo RSS
     "ignorar_url": r"/(espanol|english)/"},  # traduções da mesma checagem
    {"agencia": "e-farsas", "nome": "E-Farsas",
     "feeds": ["https://www.e-farsas.com/feed"], "paginar": True,
     # posts institucionais/retrospectivas também levam a categoria "Falso"/"Verdadeiro"
     "ignorar_titulo": r"^\d+ anos do e-farsas|^relembre|^as previsoes|^como saber|^caso havaianas|"
                       r"^8 de janeiro|^ouca a participacao|podcast"},
    {"agencia": "fato-ou-fake", "nome": "Fato ou Fake (G1)",
     "feeds": ["https://g1.globo.com/rss/g1/fato-ou-fake/"],
     "ignorar_titulo": r"^fato ou fake: veja como|^candidatos .* assinam compromisso"},
    {"agencia": "aos-fatos", "nome": "Aos Fatos",
     # descoberto via <link rel="alternate"> da home; /rss/ do catálogo dá 404
     "feeds": ["https://www.aosfatos.org/noticias/feed/"]},
    {"agencia": "estadao-verifica", "nome": "Estadão Verifica",
     # /estadao-verifica/feed/ dá 404; o feed Arc da seção funciona
     "feeds": ["https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/estadao-verifica/"]},
    {"agencia": "comprova", "nome": "Projeto Comprova",
     # /feed/ raiz está parado em 2019; as checagens estão em /publicações/
     "feeds": ["https://projetocomprova.com.br/publica%C3%A7%C3%B5es/feed/"], "paginar": True,
     "ignorar_titulo": r"^comprova lan[cç]a"},
    # 403 (Akamai/Cloudflare) para GET simples em 28/09 — mantidos para registrar o status
    {"agencia": "uol-confere", "nome": "UOL Confere",
     "feeds": ["https://noticias.uol.com.br/confere/index.xml"]},
    {"agencia": "afp-checamos", "nome": "AFP Checamos",
     "feeds": ["https://checamos.afp.com/rss"]},
]

# Portais `tipo: checagem` do catálogo que NÃO são agências de checagem (a revisão
# apontou: ValorInveste é cobertura de golpes, Tatu é jornalismo de dados) ou
# que são páginas institucionais sem feed. Não são ingeridos como checagens.
NAO_AGENCIAS = {"valorinveste-checagem", "tatu", "saude-sem-fake", "rede-saude-fiocruz"}


# ----------------------------------------------------------------------------- URL
_PARAMS_RASTREIO = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|amp$)", re.I)


def canonizar_url(url: str) -> str:
    """URL publicável: sem query/fragmento; host minúsculo; path com unicode normalizado."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip()
    host = (p.hostname or "").lower()
    if p.port and p.port not in (80, 443):
        host = f"{host}:{p.port}"
    caminho = quote(unquote(p.path or "/"), safe="/%:@-._~!$&'()*+,;=")
    return urlunsplit(((p.scheme or "https").lower(), host, caminho, "", ""))


def chave_url(url: str) -> str:
    """Chave de dedupe: ignora esquema, www., barra final, query, fragmento e %-encoding."""
    try:
        p = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    caminho = unquote(p.path or "/").rstrip("/") or "/"
    caminho = re.sub(r"\.(ghtml|html?|php)$", "", caminho)  # http/https e .html duplicados
    return f"{host}{caminho}".lower()


# ----------------------------------------------------------------------------- HTTP
class _Limitador:
    """1 requisição por INTERVALO_HOST segundos por host (thread-safe)."""

    def __init__(self, intervalo: float = INTERVALO_HOST):
        self.intervalo = intervalo
        self._ultimo: Dict[str, float] = {}
        self._travas: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._global = threading.Lock()

    def esperar(self, host: str) -> None:
        with self._global:
            trava = self._travas[host]
        with trava:
            agora = time.monotonic()
            falta = self._ultimo.get(host, 0.0) + self.intervalo - agora
            if falta > 0:
                time.sleep(falta)
            self._ultimo[host] = time.monotonic()


class Baixador:
    """GET com UA de navegador, teto de bytes e 1 req/s por host, via `replay.http_get`
    (ganha cassetes em eval/cassettes com IO_MODO=record|replay e evento `http` na telemetria)."""

    HEADERS = {"User-Agent": UA_NAVEGADOR,
               "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
               "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                         "application/rss+xml,*/*;q=0.8"}

    def __init__(self, teto_bytes: int = TETO_BYTES, intervalo: float = INTERVALO_HOST):
        self.teto = teto_bytes
        self.lim = _Limitador(intervalo)

    def baixar(self, url: str) -> Tuple[int, bytes, str]:
        """(status, corpo até o teto, url final). status 0 = erro de rede/cassete ausente."""
        from . import replay, telemetria

        host = (urlsplit(url).hostname or "").lower()
        self.lim.esperar(host)
        try:
            r = replay.http_get(url, headers=dict(self.HEADERS), timeout=TIMEOUT, follow_redirects=True)
        except Exception as e:  # rede/TLS/timeout/ReplayMiss: registra e segue
            telemetria.fallback("ingestor.http", f"{type(e).__name__}: {e}", url=url)
            return 0, str(e).encode("utf-8", "replace"), url
        corpo = r.content or b""
        if len(corpo) > self.teto:
            telemetria.fallback("ingestor.teto", f"corpo {len(corpo)} B > {self.teto} B, truncado", url=url)
            corpo = corpo[: self.teto]
        if r.status_code != 200:
            telemetria.fallback("ingestor.http", f"HTTP {r.status_code}", url=url)
        return r.status_code, corpo, str(getattr(r, "url", None) or url)

    def fechar(self) -> None:  # compat: não há cliente persistente
        pass


def _decodificar(corpo: bytes) -> str:
    m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", corpo[:4096], re.I)
    for cod in ([m.group(1).decode("ascii", "ignore")] if m else []) + ["utf-8", "latin-1"]:
        try:
            return corpo.decode(cod)
        except (LookupError, UnicodeDecodeError):
            continue
    return corpo.decode("utf-8", "replace")


# ----------------------------------------------------------------------------- texto
def texto_de_html(fragmento: Optional[str]) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragmento or "", flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</p>|</h\d>|</li>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(_html.unescape(t))
    t = re.sub(r"[ \t\r\f\v ]+", " ", t)
    return re.sub(r"\s*\n\s*", "\n", t).strip()


def _paragrafos_pagina(html: str, limite: int = 8) -> str:
    """Fallback de corpo: <p> com texto real (mesma ideia do aprofundar.extrair_corpo)."""
    t = re.sub(r"<(header|nav|footer|aside|form|noscript|script|style)[^>]*>.*?</\1>", " ",
               html or "", flags=re.I | re.S)
    uteis = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", t, re.S)[:60]:
        limpo = texto_de_html(p).replace("\n", " ")
        if len(limpo) >= 60:
            uteis.append(limpo)
        if len(uteis) >= limite:
            break
    return " ".join(uteis)


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def _data_iso(valor: Optional[str]) -> Optional[str]:
    if not valor:
        return None
    v = valor.strip()
    try:
        return parsedate_to_datetime(v).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        if d.tzinfo is None:
            return d.date().isoformat() if len(v) <= 10 else d.isoformat()
        return d.astimezone(timezone.utc).isoformat()
    except ValueError:
        return v


# ----------------------------------------------------------------------------- feeds
def parse_feed(conteudo: bytes) -> List[Dict[str, Any]]:
    """RSS 0.9x/1.0/2.0 e Atom -> [{titulo, link, data, corpo_html, categorias}]."""
    from lxml import etree

    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    try:
        raiz = etree.fromstring(conteudo, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        return []
    if raiz is None:
        return []
    itens = []
    for no in raiz.xpath("//*[local-name()='item' or local-name()='entry']"):
        campos: Dict[str, List[Any]] = defaultdict(list)
        for filho in no:
            if not isinstance(filho.tag, str):
                continue
            nome = etree.QName(filho).localname
            campos[nome].append(filho)

        def txt(*nomes: str) -> Optional[str]:
            for n in nomes:
                for el in campos.get(n, []):
                    t = "".join(el.itertext()).strip()
                    if t:
                        return t
            return None

        link = None
        for el in campos.get("link", []):
            href = el.get("href")
            if href and el.get("rel", "alternate") == "alternate":
                link = href
                break
            if (el.text or "").strip():
                link = el.text.strip()
                break
        link = link or txt("guid")
        cats = []
        for el in campos.get("category", []) + campos.get("subject", []):
            c = el.get("term") or "".join(el.itertext())
            c = _html.unescape(c or "").strip()
            if c:
                cats.append(c)
        itens.append({
            "titulo": texto_de_html(txt("title") or "").replace("\n", " "),
            "link": (link or "").strip(),
            "data": _data_iso(txt("pubDate", "published", "updated", "date", "issued")),
            "corpo_html": txt("encoded", "content") or txt("description", "summary") or "",
            "subtitulo": texto_de_html(txt("subtitle") or ""),
            "categorias": cats,
        })
    return itens


def parse_wpjson(conteudo: bytes) -> List[Dict[str, Any]]:
    """WordPress /wp-json/wp/v2/posts -> mesmo formato do parse_feed."""
    try:
        dados = json.loads(conteudo.decode("utf-8", "replace"))
    except ValueError:
        return []
    if not isinstance(dados, list):
        return []
    itens = []
    for p in dados:
        if not isinstance(p, dict):
            continue
        itens.append({
            "titulo": texto_de_html((p.get("title") or {}).get("rendered", "")).replace("\n", " "),
            "link": p.get("link") or "",
            "data": _data_iso(p.get("date_gmt") and p["date_gmt"] + "Z" or p.get("date")),
            "corpo_html": (p.get("content") or {}).get("rendered", ""),
            "subtitulo": texto_de_html((p.get("excerpt") or {}).get("rendered", "")),
            "categorias": [],
        })
    return itens


def descobrir_feeds(html: str, base: str) -> List[str]:
    """<link rel="alternate" type="application/rss+xml|atom+xml" href=…> da página."""
    achados = []
    for tag in re.findall(r"<link\b[^>]*>", html or "", re.I):
        if re.search(r"rel\s*=\s*[\"']?alternate", tag, re.I) and \
                re.search(r"type\s*=\s*[\"']?application/(rss|atom)\+xml", tag, re.I):
            m = re.search(r"href\s*=\s*[\"']([^\"']+)", tag, re.I)
            if m:
                achados.append(urljoin(base, _html.unescape(m.group(1))))
    return list(dict.fromkeys(achados))


# ----------------------------------------------------------------------------- veredito do título
_E = r"(?:é|e|É)"
# (regex, grupo do selo ou selo fixo, grupo da afirmação)
_PADROES_TITULO: List[Tuple[re.Pattern, Any, int]] = [
    # G1: "É #FAKE que …", "É #FATO: …", "É FATO: …", "#NÃOÉBEMASSIM"
    (re.compile(rf"^\s*{_E}\s*#\s*(fake|fato)\b\s*(?:que\b)?\s*[:\-–—]?\s*(.+)$", re.I), 1, 2),
    (re.compile(rf"^\s*{_E}\s+(fake|fato)\s*[:\-–—]\s*(.+)$", re.I), 1, 2),
    (re.compile(r"^\s*#\s*n[ãa]o\s*[ée]\s*bem\s*assim\b\s*[:\-–—]?\s*(?:que\s+)?(.+)$", re.I),
     "não é bem assim", 1),
    # "Não é verdade que …"
    (re.compile(r"^\s*n[ãa]o\s+[ée]\s+verdade\s+que\s+(.+)$", re.I), "não é verdade", 1),
    # "É falso que …", "É falsa a …", "É falso áudio em que …", "É enganoso …", "É verdadeiro que …"
    (re.compile(rf"^\s*{_E}\s+(falso|falsa|enganoso|enganosa|verdadeiro|verdadeira|verdade|golpe)\b"
                r"\s*(?:que\s+)?[:\-–—]?\s*(.+)$", re.I), 1, 2),
    # "Não há registros/evidências/provas de (que) …"
    (re.compile(r"^\s*(n[ãa]o\s+h[áa]\s+(?:registros?|evid[êe]ncias?|provas?|ind[íi]cios?))\s+"
                r"(?:de\s+que\s+|de\s+|que\s+)?(.+)$", re.I), 1, 2),
    # Selo como prefixo: "[Falso] …", "Falso: …", "Enganoso – …"
    (re.compile(r"^\s*[\[(]?\s*(falso|enganoso|verdadeiro|sem contexto|s[áa]tira|distorcido|exagerado|"
                r"insustent[áa]vel|boato|farsa|fake|hoax)\s*[\])]?\s*[:\-–—|]\s*(.+)$", re.I), 1, 2),

]
# Selo como sufixo (Boatos.org até ~2019): "Água gelada faz mal; causa câncer #boato".
# O título inteiro é a alegação: aqui o ";" NÃO separa explicação (não passa por _limpar).
_RE_SELO_SUFIXO = re.compile(r"^\s*(.+?)\s*(#\s*(?:boato|fake|hoax))\s*$", re.I)
# Pergunta ("É verdade que X?") não é selo: E-farsas titula assim e responde no corpo.
_RE_PERGUNTA = re.compile(r"\?\s*$")
_RE_RESUMO_MULTIPLO = re.compile(r"#\s*fato\b.*#\s*fake\b|#\s*fake\b.*#\s*fato\b", re.I)
_RE_CAUDA_EXPLICACAO = re.compile(r"\s*[;|]\s+.*$")
_RE_CAUDA_PERGUNTA = re.compile(
    r"\s*(!|\.)?\s*(real ou fake|ser[áa] verdade|verdade ou mentira|fato ou fake|confira)\s*\?*\s*$", re.I)


def _limpar_afirmacao(texto: str) -> str:
    t = _RE_CAUDA_EXPLICACAO.sub("", texto or "").strip()
    t = _RE_CAUDA_PERGUNTA.sub("", t).strip()
    t = re.sub(r"^\s*(?:é\s+verdade\s+que|e\s+verdade\s+que)\s+", "", t, flags=re.I)
    t = t.strip(" \t–—-:;,.")
    t = re.sub(r"\?+$", "", t).strip()
    return (t[:1].upper() + t[1:]) if t else t


# Segundo nível: convenções de manchete de checagem sem selo explícito. O literal
# registrado em selo_original é a frase da manchete ("não disse", "é fake"); a
# tabela `frases_de_titulo` de data/selos.json decide o enum (revisão humana).
_VERBOS_NEG = (r"disse|afirmou|declarou|falou|revelou|defendeu|publicou|protocolou|anunciou|mostra|mostram|"
               r"teve|ocorreu|aconteceu|pediu|assinou|postou|compartilhou|jogou|gastou|morreu|proibiu|"
               r"autorizou|aprovou|comprou|chamou|ofendeu|fez|recebeu|criou|deu|mandou|ordenou|taxou|fechou|"
               r"cobrar[áa]|cobra|leva|levou|admitiu|desmente|desmentiu|pagou|prendeu|vai|v[ãa]o|ser[áa]|"
               r"ser[ãa]o|foi|foram|existe|existem|sofreu|venceu|perdeu|votou|vetou|sancionou|extinguiu|acabou")
_PADROES_FRASE: List[Tuple[re.Pattern, str]] = [
    # "Golpe usa/cita/promete …"
    (re.compile(r"^\s*(?P<selo>golpe)\b\s+(?P<af>.+)$", re.I), "golpe"),
    # "Desta vez, é real: …"
    (re.compile(r"^\s*desta vez,?\s+(?P<selo>[ée] real)\s*[:\-–—]\s*(?P<af>.+)$", re.I), ""),
    # "… foi criado/gerado/fabricado por/com IA"
    (re.compile(r"^(?P<af>.+?),?\s+(?:foi|foram|[ée]|s[ãa]o)\s+(?P<selo>(?:criad|gerad|fabricad|feit)[oa]s?\s+"
                r"(?:por|com)\s+(?:ia|intelig[êe]ncia artificial))\b.*$", re.I), ""),
    # "… é falso/fake/deepfake/montagem/encenação/antigo/verdadeira" (sufixo)
    (re.compile(r"^(?P<af>.+?),?\s+(?:[ée]|s[ãa]o|foi|foram)\s+(?:um\s+|uma\s+)?(?P<selo>falso|falsa|falsos|falsas|"
                r"fake|deepfake|montagem|encena[çc][ãa]o|farsa|boato|verdadeira|verdadeiro|verdadeiras|"
                r"verdadeiros|antigo|antiga|antigos|antigas)\b.*$", re.I), ""),
    # "… tira de contexto / circula sem contexto / distorce / engana(m) ao sugerir que …"
    (re.compile(r"^(?P<af>.+?)\s+(?P<selo>(?:tira|tiram|tirad[oa]s?)\s+de\s+contexto|circulam?\s+sem\s+contexto|"
                r"distorcem?|enganam?)\b(?:\s+ao\s+(?:sugerir|afirmar|dizer|alegar)\s+que\s+(?P<af2>.+))?(?P<resto2>.*)$",
                re.I), ""),
    # "… não procede" / "… usa dados errados" / "… sem respaldo científico"
    (re.compile(r"^(?P<af>.+?)\s+(?P<selo>n[ãa]o procede|usa dados errados|sem respaldo cient[íi]fico)\b.*$", re.I), ""),
    # "Não há qualquer confirmação de que …"
    (re.compile(r"^\s*(?P<selo>n[ãa]o h[áa] (?:qualquer )?confirma[çc][ãa]o)\s+(?:de\s+que\s+|de\s+)?(?P<af>.+)$",
                re.I), ""),
    # "Foto/Vídeo falso(a) de …"
    (re.compile(r"^\s*(?P<af0>foto|v[íi]deo|imagem|[áa]udio|print|mensagem)s?\s+(?P<selo>falsos?|falsas?)\s+"
                r"(?P<af>.+)$", re.I), ""),
    # "Fulano não disse/mostra/… X" — manchete-correção: a afirmação é a frase sem o "não"
    (re.compile(rf"^(?P<suj>.{{3,90}}?)\s+(?P<selo>(?:n[ãa]o|nunca)\s+(?:{_VERBOS_NEG}))\b(?P<resto>.*)$", re.I), ""),
]


def _frase_de_titulo(t: str, agencia: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    for rx, _ in _PADROES_FRASE:
        m = rx.match(t)
        if not m:
            continue
        literal = re.sub(r"\s+", " ", m.group("selo")).strip()
        if not selos.normalizar(literal, agencia):
            continue
        g = m.groupdict()
        if g.get("suj") is not None:  # negação: devolve a afirmação positiva
            verbo = re.sub(r"^(?:n[ãa]o|nunca)\s+", "", literal, flags=re.I)
            af = f"{g['suj']} {verbo}{g['resto']}"
            af = re.split(r",\s+mas\b|;\s+|,\s+e\s+sim\b", af, maxsplit=1)[0]
        elif g.get("af2"):
            af = g["af2"]
        elif g.get("af0"):
            af = f"{g['af0']} {g['af']}"
        else:
            af = g.get("af") or t
            if g.get("resto2") and len(af) < 25:  # "Posts distorcem X" -> X
                af = re.sub(r"^\s*(?:sobre|a|o)\s+", "", g["resto2"])
        return literal, af
    return None, None


def veredito_do_titulo(titulo: str, agencia: Optional[str] = None) -> Tuple[Optional[str], str]:
    """-> (selo_literal ou None, afirmação derivada do título)."""
    t = (titulo or "").strip()
    if not t or _RE_RESUMO_MULTIPLO.search(t) or re.match(r"^\s*veja o que [ée]", t, re.I):
        return None, _limpar_afirmacao(t)
    if not _RE_PERGUNTA.search(t):
        m = _RE_SELO_SUFIXO.match(t)
        if m and selos.normalizar(m.group(2), agencia):
            return m.group(2), m.group(1).strip(" \t–—-:;,.")
        for rx, selo, g_af in _PADROES_TITULO:
            m = rx.match(t)
            if not m:
                continue
            literal = m.group(selo) if isinstance(selo, int) else selo
            if isinstance(selo, int) and literal.lower() in ("fake", "fato") and "#" in t[: m.end(selo)]:
                literal = "#" + literal.upper()
            if selos.normalizar(literal, agencia) is None:
                continue
            return literal, _limpar_afirmacao(m.group(g_af))
        # frases olham só a oração principal (antes do ";" explicativo)
        literal, af = _frase_de_titulo(re.split(r"\s*;\s+", t, maxsplit=1)[0], agencia)
        if literal:
            return literal, _limpar_afirmacao(af)
    return None, _limpar_afirmacao(t)


# ----------------------------------------------------------------------------- veredito do corpo
_RE_SELO_CORPO = [
    # Lupa: "… alega que X. É falso." / "Não é verdade."
    re.compile(r"(?:^|[.!?…]\s+)((?:é|e)\s+(?:falso|falsa|enganoso|enganosa|verdadeiro|verdadeira|exagerado|"
               r"insustent[áa]vel)|n[ãa]o\s+[ée]\s+verdade)\s*[.!]", re.I),
    # "O conteúdo/post/vídeo é falso"
    re.compile(r"\b(?:conte[úu]do|post|publica[çc][ãa]o|v[íi]deo|informa[çc][ãa]o|alega[çc][ãa]o|imagem|foto|"
               r"[áa]udio|mensagem)\s+(?:[ée]|s[ãa]o)\s+(falso|falsa|enganoso|enganosa|verdadeiro|verdadeira|"
               r"exagerado|insustent[áa]vel)\b", re.I),
    # Boatos.org: corpo começa com "Boato –" (nos primeiros anos, "Hoax –")
    re.compile(r"^\s*(boato|hoax)\s*[–—-]", re.I),
]


def veredito_do_corpo(corpo: str, agencia: Optional[str] = None) -> Optional[str]:
    inicio = (corpo or "")[:700]
    for rx in _RE_SELO_CORPO:
        m = rx.search(inicio)
        if m:
            literal = re.sub(r"\s+", " ", m.group(1)).strip()
            if selos.normalizar(literal, agencia):
                return literal
    return None


def veredito_das_categorias(categorias: Iterable[str], agencia: Optional[str]) -> Optional[str]:
    """Categoria do RSS que é selo na tabela (E-farsas marca "Falso"/"Fora de Contexto").
    Mais de um veredito distinto -> ambíguo -> None."""
    achados = {}
    for c in categorias or []:
        v = selos.normalizar(c, agencia)
        if v:
            achados.setdefault(v, c)
    return next(iter(achados.values())) if len(achados) == 1 else None


# ----------------------------------------------------------------------------- montagem do registro
def montar_registro(item: Dict[str, Any], agencia: str, html_pagina: Optional[str],
                    url_final: Optional[str] = None) -> Dict[str, Any]:
    url = canonizar_url(url_final or item["link"])
    titulo = item.get("titulo") or ""
    corpo_rss = texto_de_html(item.get("corpo_html"))
    info = jsonld.extrair(html_pagina) if html_pagina else {"claim_reviews": [], "article_body": None,
                                                           "headline": None, "data_pub": None, "autor": None}
    titulo = titulo or info.get("headline") or ""
    corpo = info.get("article_body") or corpo_rss or (_paragrafos_pagina(html_pagina) if html_pagina else "")

    reg: Dict[str, Any] = {
        "url": url, "titulo": titulo, "agencia": agencia,
        "afirmacao_checada": None, "selo_original": None, "veredito": None,
        "data_pub": _data_iso(info.get("data_pub")) or item.get("data"),
        "trecho": None, "origem": "rss-titulo",
    }
    crs = [c for c in info.get("claim_reviews", []) if c.get("afirmacao_checada") or c.get("selo_original")]
    if crs:
        vereditos = {selos.normalizar(c.get("selo_original"), agencia) for c in crs}
        principal = crs[0]
        reg["afirmacao_checada"] = principal.get("afirmacao_checada")
        reg["selo_original"] = principal.get("selo_original")
        reg["origem"] = "claimreview"
        if len(crs) > 1:
            # Página com várias checagens (debates, discursos): um registro por URL;
            # veredito só se todas concordam.
            reg["detalhe_origem"] = f"claimreview-multiplo:{len(crs)}"
            reg["afirmacoes_extras"] = [c.get("afirmacao_checada") for c in crs[1:] if c.get("afirmacao_checada")][:20]
            reg["selo_original"] = " | ".join(dict.fromkeys(c.get("selo_original") or "?" for c in crs))
            reg["veredito"] = next(iter(vereditos)) if len(vereditos) == 1 else None
        else:
            reg["veredito"] = selos.normalizar(principal.get("selo_original"), agencia)
        reg["trecho"] = principal.get("trecho")
        if not reg["afirmacao_checada"]:
            reg["afirmacao_checada"] = veredito_do_titulo(titulo, agencia)[1]
    else:
        selo_t, afirm = veredito_do_titulo(titulo, agencia)
        reg["afirmacao_checada"] = afirm
        if selo_t:
            reg["selo_original"], reg["origem"] = selo_t, "rss-titulo"
        else:
            selo_c = veredito_das_categorias(item.get("categorias") or [], agencia)
            if selo_c:
                reg["selo_original"], reg["origem"] = selo_c, "rss-titulo"
                reg["detalhe_origem"] = "rss-categoria"
            else:
                # o resumo do WordPress também abre com "Boato –" quando o corpo não abre
                selo_b = veredito_do_corpo(corpo, agencia) or veredito_do_corpo(item.get("subtitulo") or "", agencia)
                if selo_b:
                    reg["selo_original"], reg["origem"] = selo_b, "articlebody"
        reg["veredito"] = selos.normalizar(reg["selo_original"], agencia)
    reg["trecho"] = (reg.get("trecho") or item.get("subtitulo") or corpo or "")[:600].strip() or None
    if info.get("autor"):
        reg["autor"] = info["autor"]
    reg["coletado_em"] = datetime.now(timezone.utc).date().isoformat()
    return reg


# ----------------------------------------------------------------------------- persistência
def carregar_jsonl(caminho: Path) -> List[Dict[str, Any]]:
    if not caminho.exists():
        return []
    saida = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha:
            try:
                saida.append(json.loads(linha))
            except ValueError:
                continue
    return saida


def carregar_excluidas(caminho: Optional[Path] = None) -> set:
    caminho = caminho or ARQ_EXCLUIDAS
    if not caminho.exists():
        return set()
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except ValueError:
        return set()
    regs = dados.get("checagens", []) if isinstance(dados, dict) else dados
    return {chave_url(r["url"] if isinstance(r, dict) else r) for r in regs}


def mesclar(existentes: List[Dict[str, Any]], novos: List[Dict[str, Any]],
            excluidas: set = frozenset()) -> List[Dict[str, Any]]:
    """Merge idempotente por URL canônica. 'manual' nunca é sobrescrito; um registro novo
    sem veredito não apaga um veredito que já existia; ClaimReview só é trocado por ClaimReview."""
    por_chave: Dict[str, Dict[str, Any]] = {}
    for r in existentes:
        k = chave_url(r.get("url", ""))
        if k and k not in excluidas:
            por_chave[k] = r
    for r in novos:
        k = chave_url(r.get("url", ""))
        if not k or k in excluidas:
            continue
        velho = por_chave.get(k)
        if velho and (velho.get("origem") == "manual"
                      or (velho.get("veredito") and not r.get("veredito"))
                      # --sem-paginas não rebaixa um ClaimReview para veredito de título
                      or (velho.get("origem") == "claimreview" and r.get("origem") != "claimreview")):
            continue
        por_chave[k] = r
    return sorted(por_chave.values(), key=lambda r: (r.get("agencia", ""), r.get("data_pub") or "",
                                                      r.get("url", "")), reverse=False)


def gravar_jsonl(caminho: Path, registros: List[Dict[str, Any]]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in registros:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(caminho)


# ----------------------------------------------------------------------------- orquestração
def _fontes_com_catalogo() -> List[Dict[str, Any]]:
    """FONTES + feeds `rss` dos portais de checagem do catálogo (se houver)."""
    fontes = [dict(f, feeds=list(f["feeds"])) for f in FONTES]
    por_id = {f["agencia"]: f for f in fontes}
    try:
        cat = json.loads(ARQ_CATALOGO.read_text(encoding="utf-8"))
        for p in cat.get("portais", []):
            if p.get("tipo") != "checagem" or p.get("id") in NAO_AGENCIAS:
                continue
            f = por_id.get(p["id"])
            if f is None:
                f = {"agencia": p["id"], "nome": p.get("nome", p["id"]), "feeds": [],
                     "homepage": p.get("homepage")}
                fontes.append(f)
                por_id[p["id"]] = f
            for u in p.get("rss") or []:
                if u not in f["feeds"]:
                    f["feeds"].append(u)
            f.setdefault("homepage", p.get("homepage"))
    except (OSError, ValueError):
        pass
    return fontes


def coletar_itens(b: Baixador, fonte: Dict[str, Any], max_por_feed: int, paginas_feed: int,
                  status: List[Dict[str, Any]], paginas_historico: Optional[int] = None) -> List[Dict[str, Any]]:
    itens: List[Dict[str, Any]] = []
    feeds = list(fonte.get("feeds") or [])
    if not feeds and fonte.get("homepage"):
        cod, corpo, final = b.baixar(fonte["homepage"])
        achados = descobrir_feeds(_decodificar(corpo), final) if cod == 200 else []
        status.append({"agencia": fonte["agencia"], "feed": fonte["homepage"], "tipo": "descoberta",
                       "http": cod, "itens": len(achados), "achados": achados})
        feeds = achados[:2]
    for feed in feeds:
        paginas = paginas_feed if fonte.get("paginar") else 1
        for pg in range(1, paginas + 1):
            url = feed if pg == 1 else f"{feed}{'&' if '?' in feed else '?'}paged={pg}"
            cod, corpo, _ = b.baixar(url)
            lidos = (parse_feed(corpo) if cod == 200 else [])[:max_por_feed]
            status.append({"agencia": fonte["agencia"], "feed": url, "tipo": "rss", "http": cod,
                           "itens": len(lidos)})
            itens.extend(lidos)
            if not lidos:
                break
    if fonte.get("wpjson"):
        # 50/página com _fields: 100 posts com conteúdo passam do teto de 5 MB
        # Histórico: páginas da API (50 posts cada) independentes da paginação do RSS.
        for pg in range(1, max(1, paginas_historico or paginas_feed) + 1):
            url = (f"{fonte['wpjson']}?per_page=50&page={pg}"
                   "&_fields=link,title,date_gmt,date,excerpt,content" + fonte.get("wpjson_params", ""))
            cod, corpo, _ = b.baixar(url)
            lidos = (parse_wpjson(corpo) if cod == 200 else [])[:max_por_feed]
            status.append({"agencia": fonte["agencia"], "feed": url, "tipo": "wp-json", "http": cod,
                           "itens": len(lidos)})
            itens.extend(lidos)
            if len(lidos) < 50:
                break
    # dedupe dentro da agência (Aos Fatos repete itens no feed) e filtros
    vistos, saida = set(), []
    rx_url = re.compile(fonte["ignorar_url"], re.I) if fonte.get("ignorar_url") else None
    rx_tit = re.compile(fonte["ignorar_titulo"], re.I) if fonte.get("ignorar_titulo") else None
    for it in itens:
        k = chave_url(it.get("link", ""))
        if not it.get("link") or k in vistos:
            continue
        if rx_url and rx_url.search(it["link"]):
            continue
        if rx_tit and rx_tit.search(_sem_acento(it.get("titulo", "")).lower()):
            continue
        vistos.add(k)
        saida.append(it)
    return saida


def processar_fonte(b: Baixador, fonte: Dict[str, Any], max_por_feed: int, paginas_feed: int,
                    sem_paginas: bool, excluidas: set, status: List[Dict[str, Any]],
                    log=print, paginas_historico: Optional[int] = None) -> List[Dict[str, Any]]:
    itens = coletar_itens(b, fonte, max_por_feed, paginas_feed, status, paginas_historico)
    regs = []
    for it in itens:
        if chave_url(it["link"]) in excluidas:
            continue
        html_pag, final = None, None
        if not sem_paginas:
            cod, corpo, final = b.baixar(it["link"])
            if cod == 200:
                html_pag = _decodificar(corpo)
            else:
                final = None
                status.append({"agencia": fonte["agencia"], "feed": it["link"], "tipo": "pagina",
                               "http": cod, "itens": 0})
        regs.append(montar_registro(it, fonte["agencia"], html_pag, final))
    log(f"  [{fonte['agencia']}] {len(regs)} itens")
    return regs


def estatisticas(registros: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    por_ag: Dict[str, Dict[str, Any]] = {}
    for r in registros:
        e = por_ag.setdefault(r["agencia"], {"n": 0, "com_veredito": 0, "origem": Counter(),
                                              "veredito": Counter()})
        e["n"] += 1
        if r.get("veredito"):
            e["com_veredito"] += 1
            e["origem"][r["origem"]] += 1
        else:
            e["origem"]["sem-veredito"] += 1
        e["veredito"][r.get("veredito") or "None"] += 1
    return por_ag


def imprimir_estatisticas(registros: List[Dict[str, Any]], status: List[Dict[str, Any]], log=print) -> None:
    por_ag = estatisticas(registros)
    log("\nFeeds:")
    for s in status:
        if s["tipo"] != "pagina":
            log(f"  {s['agencia']:<18} {s['tipo']:<10} HTTP {s['http']:<3} itens={s['itens']:<4} {s['feed']}")
    falhas_pag = Counter(s["agencia"] for s in status if s["tipo"] == "pagina")
    if falhas_pag:
        log(f"  páginas de artigo com erro: {dict(falhas_pag)}")
    log(f"\n{'agência':<18} {'n':>4} {'%veredito':>9}  origem do veredito / distribuição")
    tot = tot_v = 0
    for ag, e in sorted(por_ag.items()):
        tot += e["n"]
        tot_v += e["com_veredito"]
        pct = 100.0 * e["com_veredito"] / max(1, e["n"])
        log(f"{ag:<18} {e['n']:>4} {pct:>8.0f}%  {dict(e['origem'])}  {dict(e['veredito'])}")
    log(f"{'TOTAL':<18} {tot:>4} {100.0 * tot_v / max(1, tot):>8.0f}%")


def ingerir(max_por_feed: int = 100, sem_paginas: bool = False, paginas_feed: int = 3,
            saida: Optional[Path] = None, log=print, paginas_historico: Optional[int] = None,
            agencias: Optional[List[str]] = None) -> Dict[str, Any]:
    saida = saida or ARQ_CHECAGENS
    excluidas = carregar_excluidas()
    fontes = _fontes_com_catalogo()
    if agencias:
        fontes = [f for f in fontes if f["agencia"] in agencias]
    status: List[Dict[str, Any]] = []
    b = Baixador()
    novos: List[Dict[str, Any]] = []
    try:
        # uma thread por agência; o limitador garante 1 req/s por host
        with ThreadPoolExecutor(max_workers=max(1, len(fontes))) as ex:
            futuros = [ex.submit(processar_fonte, b, f, max_por_feed, paginas_feed, sem_paginas,
                                 excluidas, status, log, paginas_historico) for f in fontes]
            for fu in futuros:
                try:
                    novos.extend(fu.result())
                except Exception as e:  # uma agência quebrada não derruba as outras
                    log(f"  erro numa fonte: {e!r}")
    finally:
        b.fechar()
    existentes = carregar_jsonl(saida)
    todos = mesclar(existentes, novos, excluidas)
    gravar_jsonl(saida, todos)
    log(f"\nNovos nesta execução: {len(novos)} | no arquivo: {len(todos)} ({saida})")
    imprimir_estatisticas(todos, status, log)
    return {"novos": len(novos), "total": len(todos), "status": status, "estatisticas": estatisticas(todos)}


def reprocessar(caminho: Optional[Path] = None, log=print) -> Dict[str, int]:
    """Reaplica as regras de selo/afirmação ao que JÁ está no arquivo (título + trecho),
    sem rede. Não toca em ClaimReview nem em 'manual'; só ganha selo quem não tinha e
    corrige a afirmação de títulos com selo no fim ("… #boato")."""
    caminho = caminho or ARQ_CHECAGENS
    regs = carregar_jsonl(caminho)
    n_selo = n_afirm = 0
    for r in regs:
        if r.get("origem") in ("claimreview", "manual"):
            continue
        agencia, titulo = r.get("agencia"), r.get("titulo") or ""
        literal, afirm = veredito_do_titulo(titulo, agencia)
        if _RE_SELO_SUFIXO.match(titulo) and afirm and afirm != r.get("afirmacao_checada"):
            r["afirmacao_checada"] = afirm
            n_afirm += 1
        if r.get("veredito"):
            continue
        origem = "rss-titulo"
        if not literal:
            literal, origem = veredito_do_corpo(r.get("trecho") or "", agencia), "articlebody"
        if literal and selos.normalizar(literal, agencia):
            r["selo_original"], r["origem"] = literal, origem
            r["veredito"] = selos.normalizar(literal, agencia)
            if origem == "rss-titulo":
                r["afirmacao_checada"] = afirm
            n_selo += 1
    gravar_jsonl(caminho, regs)
    log(f"reprocessadas {len(regs)} checagens: +{n_selo} com selo, {n_afirm} afirmação(ões) corrigida(s)")
    return {"total": len(regs), "selo": n_selo, "afirmacao": n_afirm}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Ingere checagens (RSS + JSON-LD ClaimReview) em data/checagens.jsonl")
    ap.add_argument("--max-por-feed", type=int, default=100, help="teto de itens por feed/página de feed")
    ap.add_argument("--sem-paginas", action="store_true", help="não baixa a página do artigo (sem ClaimReview)")
    ap.add_argument("--paginas-feed", type=int, default=3, help="páginas (?paged=N) nos feeds WordPress")
    ap.add_argument("--saida", type=Path, default=None, help="padrão: data/checagens.jsonl")
    ap.add_argument("--historico", type=int, default=None,
                    help="páginas da API WordPress (50 posts cada) para buscar o histórico; padrão = --paginas-feed")
    ap.add_argument("--agencias", default="", help="só estas agências (ids separados por vírgula)")
    ap.add_argument("--reprocessar", action="store_true",
                    help="sem rede: reaplica as regras de selo/afirmação ao arquivo existente")
    args = ap.parse_args(argv)
    if args.reprocessar:
        reprocessar(args.saida)
        return 0
    from . import telemetria

    with telemetria.run({"entrada": "ingestor", "tipo": "ingestao", "max_por_feed": args.max_por_feed,
                         "sem_paginas": args.sem_paginas, "paginas_feed": args.paginas_feed}):
        ingerir(args.max_por_feed, args.sem_paginas, args.paginas_feed, args.saida,
                paginas_historico=args.historico,
                agencias=[a.strip() for a in args.agencias.split(",") if a.strip()] or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
