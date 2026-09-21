#!/usr/bin/env python3
"""
br_news_crawler.py — Web-scraping + crawling de notícias BR com Crawl4AI + LLM local (Unsloth).

Portais cobertos (curadoria):
  - G1 (geral)                     https://g1.globo.com/
  - Fato ou Fake / G1 (checagem)   https://g1.globo.com/fato-ou-fake/
  - CNN Brasil (geral)             https://www.cnnbrasil.com.br/
  - UOL Notícias                   https://noticias.uol.com.br/
  - Folha de S.Paulo               https://www.folha.uol.com.br/
  - Estadão                        https://www.estadao.com.br/
  - BBC News Brasil                https://www.bbc.com/portuguese
  - Poder360                       https://www.poder360.com.br/
  - Aos Fatos (checagem)           https://www.aosfatos.org/
  - Lupa (checagem)                https://lupa.uol.com.br/

Pipeline (Crawl4AI v0.9.x):
  Fase 0 — probe do LLM local OpenAI-compatível (Unsloth) em http://127.0.0.1:8888/v1
  Fase 1 — crawl das homepages com `arun_many` (markdown + links + mídia + metadata)
  Fase 2 — (opcional, --deep) descoberta BFS limitada por portal (max_depth=1, max_pages=N)
  Fase 3 — seleção heurística de URLs de matéria/checagem por portal
  Fase 4 — extração de amostra de artigos (JsonCss rápido + LLM local com fallback)
  Fase 4b — (opcional, --classifier laya) classificação híbrida dos campos ENUM
           (tipo_conteudo/editoria/veredito) com o laya + gating de confiança
  Fase 5 — consolidação e emissão do SCHEMA final (contrato reutilizável)

Saídas (em --output-dir, padrão ./output):
  - portais_schema.json   <- CONTRATO principal: schema canônico + schema por portal
  - artigos_amostra.json  <- amostra extraída (para validar o contrato)
  - crawl_report.json     <- relatório do crawl (status, tempos, erros)

Modo --offline:
  Não faz rede nem LLM; apenas emite o schema curado. Útil para gerar o contrato
  de interface da outra aplicação imediatamente.

LLM local (Unsloth):
  O script espera um servidor OpenAI-compatível em:
      http://127.0.0.1:8888/v1
  Ex.: unsloth / vLLM / llama.cpp / ollama servindo OpenAI API.
  O nome do modelo é autodetectado via GET /v1/models, ou informado via
      --modelo <nome>  ou  env UNSLOTH_MODEL_NAME
  No Crawl4AI isso vira:
      LLMConfig(provider=f"openai/{modelo}", api_token="not-needed",
                base_url="http://127.0.0.1:8888/v1")

Uso:
  pip install -U crawl4ai pydantic httpx python-dotenv
  crawl4ai-setup            # instala o browser (1ª vez)
  python -m playwright install --with-deps chromium   # se necessário

  # 1) Só gerar o contrato (sem rede, sem LLM):
  python br_news_crawler.py --offline

  # 2) Crawl real das homepages (sem LLM, rápido):
  python br_news_crawler.py --no-llm --max-artigos 3

  # 3) Completo, com LLM local + deep crawl limitado:
  python br_news_crawler.py --deep --max-artigos 5 --max-pages 12

  # 4) Apontar para outro modelo/endpoint + classificador laya (híbrido):
  UNSLOTH_BASE_URL=http://127.0.0.1:8888/v1 UNSLOTH_MODEL_NAME=qwen2.5-7b-instruct \
    python br_news_crawler.py --deep --classifier laya --laya-threshold 0.85

  # 5) Checagem rápida com laya SEM LLM generativo (CSS + laya, ~ms por artigo):
  python br_news_crawler.py --no-llm --classifier laya --portais fato-ou-fake aos-fatos lupa

Requisitos legais/éticos:
  Respeite robots.txt, termos de uso e direitos autorais de cada portal.
  Use para pesquisa, fact-checking e agregação com atribuição. Não republicar na íntegra.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Imports opcionais (crawl4ai pode não estar instalado no modo --offline)
# ---------------------------------------------------------------------------
try:
    from crawl4ai import (
        AsyncWebCrawler,
        BrowserConfig,
        CacheMode,
        CrawlerRunConfig,
        LLMConfig,
    )
    from crawl4ai import LLMExtractionStrategy, JsonCssExtractionStrategy
    CRAWL4AI_AVAILABLE = True
except Exception:  # pragma: no cover
    AsyncWebCrawler = BrowserConfig = CacheMode = CrawlerRunConfig = None  # type: ignore
    LLMConfig = LLMExtractionStrategy = JsonCssExtractionStrategy = None  # type: ignore
    CRAWL4AI_AVAILABLE = False

try:
    from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
    from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter, ContentTypeFilter
    DEEP_CRAWL_AVAILABLE = True
except Exception:  # pragma: no cover
    BFSDeepCrawlStrategy = FilterChain = URLPatternFilter = ContentTypeFilter = None  # type: ignore
    DEEP_CRAWL_AVAILABLE = False

try:
    from pydantic import BaseModel, Field, ValidationError
    PYDANTIC_AVAILABLE = True
except Exception:  # pragma: no cover
    BaseModel = object  # type: ignore
    Field = lambda *a, **k: None  # type: ignore
    ValidationError = Exception  # type: ignore
    PYDANTIC_AVAILABLE = False

try:
    import httpx
    HTTPX_AVAILABLE = True
except Exception:  # pragma: no cover
    httpx = None  # type: ignore
    HTTPX_AVAILABLE = False

try:
    import laya as _laya
    LAYA_AVAILABLE = True
except Exception:  # pragma: no cover
    _laya = None  # type: ignore
    LAYA_AVAILABLE = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

log = logging.getLogger("br_news_crawler")

# ---------------------------------------------------------------------------
# Config padrão do LLM local (Unsloth)
# ---------------------------------------------------------------------------
DEFAULT_BASE_URL = os.getenv("UNSLOTH_BASE_URL", "http://127.0.0.1:8888/v1")
DEFAULT_MODEL = os.getenv("UNSLOTH_MODEL_NAME", "")  # autodetecta via /models se vazio
DEFAULT_API_TOKEN = os.getenv("UNSLOTH_API_KEY", "not-needed")

# ---------------------------------------------------------------------------
# Curadoria de portais
# ---------------------------------------------------------------------------
# article_patterns: regex aplicadas aos links internos para achar matérias.
# css_hints: seletores CSS prováveis (fallback rápido / documentação).
#   Portais mudam o DOM com frequência — o schema final marca esses campos como
#   "hints" + estratégia recomendada (JsonCss para re-crawl barato, LLM p/ robustez).
PORTAIS: List[Dict[str, Any]] = [
    {
        "id": "g1",
        "nome": "G1",
        "tipo": "geral",
        "homepage": "https://g1.globo.com/",
        "rss": ["https://g1.globo.com/rss/g1/"],
        "sitemap": "https://g1.globo.com/sitemap.xml",
        "editorias": ["política", "economia", "mundo", "brasil", "tecnologia", "saúde", "educação"],
        "article_patterns": [r"/noticia/", r"/economia/noticia/", r"/politica/noticia/", r"/mundo/noticia/", r"/tecnologia/noticia/"],
        "css_hints": {
            "titulo": "h1.content-head__title, h1[itemprop='headline']",
            "subtitulo": "h2.content-head__subtitle",
            "autor": ".content-publication-data__from, [itemprop='author']",
            "data": "time[itemprop='datePublished'], .content-publication-data__updated time",
            "corpo": ".mc-article-body, div[itemprop='articleBody']",
            "imagem": ".content-media-figure img, meta[property='og:image']",
        },
        "observacoes": "Globo/G1 tem anti-bot moderado; usar headless + viewport desktop + wait domcontentloaded. Priorizar sitemap/RSS.",
        "estrategia_recomendada": "JsonCssExtractionStrategy (barato) + LLM local como fallback/validação",
    },
    {
        "id": "fato-ou-fake",
        "nome": "Fato ou Fake (G1)",
        "tipo": "checagem",
        "homepage": "https://g1.globo.com/fato-ou-fake/",
        "rss": ["https://g1.globo.com/rss/g1/fato-ou-fake/"],
        "sitemap": "https://g1.globo.com/sitemap.xml",
        "editorias": ["checagem", "desinformação"],
        "article_patterns": [r"/fato-ou-fake/noticia/", r"/fato-ou-fake/"],
        "css_hints": {
            "titulo": "h1.content-head__title",
            "subtitulo": "h2.content-head__subtitle",
            "autor": ".content-publication-data__from",
            "data": "time[itemprop='datePublished']",
            "corpo": ".mc-article-body, div[itemprop='articleBody']",
            "veredito_selo": ".content-head__subtitle, .mc-article-body strong:first-child",
            "imagem": ".content-media-figure img",
        },
        "observacoes": "Veredito costuma vir no chapéu/título ('É #FAKE', 'É FATO'). Extrair selo via LLM com instrução específica.",
        "estrategia_recomendada": "LLM local (classificar selo) + JsonCss para corpo",
        "responsavel": "Guilherme",
    },
    {
        "id": "cnn-brasil",
        "nome": "CNN Brasil",
        "tipo": "geral",
        "homepage": "https://www.cnnbrasil.com.br/",
        "rss": [],
        "sitemap": "https://www.cnnbrasil.com.br/sitemap.xml",
        "editorias": ["política", "economia", "nacional", "internacional"],
        "article_patterns": [r"/politica/", r"/economia/", r"/nacional/", r"/internacional/", r"/saude/", r"/tecnologia/"],
        "css_hints": {
            "titulo": "h1.single__title, h1",
            "subtitulo": "p.single__excerpt, h2",
            "autor": "[rel='author'], .single__author, .author__name",
            "data": "time[datetime], .single__publish-time",
            "corpo": ".single__content, article[itemprop='articleBody']",
            "imagem": "figure.single__media img, meta[property='og:image']",
        },
        "observacoes": "SPA/Next.js parcial; às vezes requer scan_full_page ou wait networkidle leve.",
        "estrategia_recomendada": "JsonCssExtractionStrategy + LLM local p/ autor/data (varia por template)",
    },
    {
        "id": "uol",
        "nome": "UOL Notícias",
        "tipo": "geral",
        "homepage": "https://noticias.uol.com.br/",
        "rss": [],
        "sitemap": "https://noticias.uol.com.br/sitemap.xml",
        "editorias": ["política", "cotidiano", "economia", "internacional"],
        "article_patterns": [r"noticias\.uol\.com\.br/.*/\d{4}/\d{2}/\d{2}.*\.htm"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .text-subtit",
            "autor": ".p-author, [itemprop='author']",
            "data": "time[itemprop='datePublished'], .p-date",
            "corpo": ".text, div[itemprop='articleBody']",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Paywall/anti-bot agressivo em parte do conteúdo; respeitar e usar apenas trechos públicos/metadata.",
        "estrategia_recomendada": "Metadata + JsonCss; LLM só no markdown fit (economia de tokens)",
    },
    {
        "id": "folha",
        "nome": "Folha de S.Paulo",
        "tipo": "geral",
        "homepage": "https://www.folha.uol.com.br/",
        "rss": [],
        "sitemap": "https://www.folha.uol.com.br/sitemap.xml",
        "editorias": ["poder", "mercado", "mundo", "cotidiano", "ciência"],
        "article_patterns": [r"folha\.uol\.com\.br/.*/202\d/.*\.shtml"],
        "css_hints": {
            "titulo": "h1.c-title, h1",
            "subtitulo": "h2.c-article__sub, .c-article__standfirst",
            "autor": ".c-signature, [itemprop='author']",
            "data": "time[itemprop='datePublished']",
            "corpo": ".c-news__body, div[itemprop='articleBody']",
            "imagem": "figure.c-galleries img, meta[property='og:image']",
        },
        "observacoes": "Paywall; crawler deve coletar homepage + metadata aberta, sem burlar paywall.",
        "estrategia_recomendada": "Homepage/listing via JsonCss; artigo completo só se público",
    },
    {
        "id": "estadao",
        "nome": "Estadão",
        "tipo": "geral",
        "homepage": "https://www.estadao.com.br/",
        "rss": [],
        "sitemap": "https://www.estadao.com.br/sitemap.xml",
        "editorias": ["política", "economia", "brasil", "internacional"],
        "article_patterns": [r"estadao\.com\.br/.*/.*-noticia"],
        "css_hints": {
            "titulo": "h1.title, h1",
            "subtitulo": "h2.subtitle, .lead",
            "autor": ".author, [itemprop='author']",
            "data": "time[itemprop='datePublished']",
            "corpo": ".n--noticia__content, div[itemprop='articleBody']",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Paywall parcial; mesmo cuidado da Folha.",
        "estrategia_recomendada": "JsonCss + metadata",
    },
    {
        "id": "bbc-brasil",
        "nome": "BBC News Brasil",
        "tipo": "geral",
        "homepage": "https://www.bbc.com/portuguese",
        "rss": [],
        "sitemap": "https://www.bbc.com/sitemaps/https-portuguese-brazil.xml",
        "editorias": ["brasil", "internacional", "economia", "ciência"],
        "article_patterns": [r"bbc\.com/portuguese/articles/", r"bbc\.com/portuguese/.*-\d+$"],
        "css_hints": {
            "titulo": "h1[data-testid='headline'], h1",
            "subtitulo": "p[data-testid='standfirst'], h2",
            "autor": "[data-testid='byline'], [itemprop='author']",
            "data": "time[data-testid='timestamp'], time[datetime]",
            "corpo": "div[data-component='text-block'], main article",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "HTML semântico e estável; ótimo para JsonCss. Sem paywall.",
        "estrategia_recomendada": "JsonCssExtractionStrategy (preferencial)",
    },
    {
        "id": "poder360",
        "nome": "Poder360",
        "tipo": "geral",
        "homepage": "https://www.poder360.com.br/",
        "rss": ["https://www.poder360.com.br/feed/"],
        "sitemap": "https://www.poder360.com.br/sitemap.xml",
        "editorias": ["governo", "congresso", "justiça", "economia", "eleições"],
        "article_patterns": [r"poder360\.com\.br/.*"],
        "css_hints": {
            "titulo": "h1.entry-title, h1",
            "subtitulo": ".entry-excerpt, h2",
            "autor": ".entry-author, [rel='author']",
            "data": "time.entry-date, time[datetime]",
            "corpo": ".entry-content, article",
            "imagem": ".entry-media img, meta[property='og:image']",
        },
        "observacoes": "WordPress; estrutura estável e RSS utilizável.",
        "estrategia_recomendada": "RSS + JsonCss",
    },
    {
        "id": "aos-fatos",
        "nome": "Aos Fatos",
        "tipo": "checagem",
        "homepage": "https://www.aosfatos.org/",
        "rss": ["https://www.aosfatos.org/rss/"],
        "sitemap": "https://www.aosfatos.org/sitemap.xml",
        "editorias": ["checagem", "verificação"],
        "article_patterns": [r"aosfatos\.org/noticias/", r"aosfatos\.org/.*"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .excerpt",
            "autor": "[rel='author'], .author",
            "data": "time[datetime]",
            "corpo": "article, .post-content",
            "veredito_selo": ".tag-verdict, .selo, h2",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Selo de veredito (FALSO, ENGANOSO, VERDADEIRO etc.) — extrair via LLM com taxonomia fechada.",
        "estrategia_recomendada": "LLM local (selo) + JsonCss (corpo)",
        "responsavel": "Gabi",
        "subsecoes": ["Radar Aos Fatos"],
    },
    {
        "id": "lupa",
        "nome": "Lupa",
        "tipo": "checagem",
        "homepage": "https://lupa.uol.com.br/",
        "rss": [],
        "sitemap": "https://lupa.uol.com.br/sitemap.xml",
        "editorias": ["checagem", "verificação"],
        "article_patterns": [r"lupa\.uol\.com\.br/.*"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .excerpt",
            "autor": "[rel='author'], .author",
            "data": "time[datetime]",
            "corpo": "article, .post-content",
            "veredito_selo": ".selo-lupa, .veredict, h2",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Taxonomia própria de selos; normalizar para o enum canônico no pós-processamento.",
        "estrategia_recomendada": "LLM local (selo) + JsonCss (corpo)",
        "responsavel": "Eliane",
    },
    # ---- Expansão: rede de checagem (15 plataformas pedidas, sem redundância) ----
    # Redundâncias eliminadas: Agência Lupa (=lupa), Aos Fatos (=aos-fatos),
    # Radar Aos Fatos (subseção de aos-fatos), Fato ou Fake G1 (=fato-ou-fake),
    # Projeto Comprova listado 2x (entrada única, 2 responsáveis).
    {
        "id": "fato-ou-boato-tse",
        "nome": "Fato ou Boato (TSE)",
        "tipo": "checagem",
        "homepage": "https://www.justicaeleitoral.jus.br/fato-ou-boato/",
        "rss": [],
        "sitemap": None,
        "editorias": ["checagem eleitoral", "desinformação"],
        "article_patterns": [r"fato-ou-boato", r"justicaeleitoral\.jus\.br/.*boato"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .lead",
            "autor": ".autor, [itemprop='author']",
            "data": "time[datetime], .data",
            "corpo": "article, .conteudo, main",
            "veredito_selo": "h2, .selo, .destaque",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "WAF/Akamai bloqueia curl (403); o crawl com browser real deve passar. Foco em boatos eleitorais.",
        "estrategia_recomendada": "LLM local (selo) + laya p/ veredito + JsonCss (corpo)",
        "responsavel": "Eliane",
    },
    {
        "id": "comprova",
        "nome": "Projeto Comprova",
        "tipo": "checagem",
        "homepage": "https://projetocomprova.com.br/",
        "rss": [],
        "sitemap": "https://projetocomprova.com.br/sitemap.xml",
        "editorias": ["checagem colaborativa", "eleições"],
        "article_patterns": [r"projetocomprova\.com\.br/.+"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .excerpt",
            "autor": "[rel='author'], .author",
            "data": "time[datetime]",
            "corpo": "article, .post-content, main",
            "veredito_selo": ".selo, .verdict, h2",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Coalizão de veículos; selo próprio. Cobertura compartilhada Eliane + Guilherme.",
        "estrategia_recomendada": "LLM local (selo) + laya p/ veredito + JsonCss (corpo)",
        "responsavel": "Eliane, Guilherme",
    },
    {
        "id": "saude-sem-fake",
        "nome": "Saúde sem Fake News / Saúde com Ciência (Ministério da Saúde)",
        "tipo": "checagem",
        "homepage": "https://www.gov.br/saude/pt-br/assuntos/saude-com-ciencia",
        "rss": [],
        "sitemap": None,
        "editorias": ["saúde", "vacinas", "desinformação em saúde"],
        "article_patterns": [r"saude-com-ciencia", r"gov\.br/saude/.*"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .lead",
            "autor": ".autor",
            "data": "time[datetime], .data",
            "corpo": "article, #content, main",
            "veredito_selo": "h2, .destaque",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Marca 'Saúde sem Fake News' migrada p/ programa 'Saúde com Ciência'. WAF gov.br barra curl; browser real passa. Canal WhatsApp (61) 99289-4640 fora do escopo web.",
        "estrategia_recomendada": "JsonCss + laya p/ veredito",
        "responsavel": "Gabi",
    },
    {
        "id": "rede-saude-fiocruz",
        "nome": "Rede de Checagem em Saúde (Fiocruz / Canal Saúde)",
        "tipo": "checagem",
        "homepage": "https://www.canalsaude.fiocruz.br/",
        "rss": [],
        "sitemap": None,
        "editorias": ["saúde", "ciência", "sus"],
        "article_patterns": [r"canalsaude\.fiocruz\.br/.*"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2",
            "autor": ".autor, [rel='author']",
            "data": "time[datetime]",
            "corpo": "article, main, .content",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Em 21/09/2026 a home redirecionava p/ aviso de período eleitoral (conteúdo institucional restrito). Recrawlear após o período.",
        "estrategia_recomendada": "JsonCss + laya p/ veredito",
        "responsavel": "Gabi",
    },
    {
        "id": "e-farsas",
        "nome": "E-Farsas",
        "tipo": "checagem",
        "homepage": "https://www.e-farsas.com/",
        "rss": [],
        "sitemap": None,
        "editorias": ["boatos", "correntes", "golpes"],
        "article_patterns": [r"e-farsas\.com/.*"],
        "css_hints": {
            "titulo": "h1.entry-title, h1",
            "subtitulo": "h2",
            "autor": ".author, [rel='author']",
            "data": "time.entry-date, time[datetime]",
            "corpo": ".entry-content, article",
            "veredito_selo": "h1, h2, .selo",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Um dos mais antigos do BR; selo costuma estar no título ('É farsa!', 'Verdadeiro'). WordPress.",
        "estrategia_recomendada": "JsonCss + laya p/ veredito (título já sinaliza)",
        "responsavel": "Daltro",
    },
    {
        "id": "valorinveste-checagem",
        "nome": "ValorInveste (cobertura anti-golpes)",
        "tipo": "checagem",
        "homepage": "https://valorinveste.globo.com/",
        "rss": [],
        "sitemap": None,
        "editorias": ["golpes financeiros", "fraudes", "finanças pessoais"],
        "article_patterns": [r"valorinveste\.globo\.com/.*golpe.*", r"valorinveste\.globo\.com/.*fraude.*"],
        "css_hints": {
            "titulo": "h1.content-head__title, h1",
            "subtitulo": "h2.content-head__subtitle",
            "autor": ".content-publication-data__from",
            "data": "time[itemprop='datePublished']",
            "corpo": ".mc-article-body, div[itemprop='articleBody']",
            "imagem": ".content-media-figure img",
        },
        "observacoes": "Sem seção dedicada de checagem; cobertura anti-golpe diluída ('Olha o golpe!', Minha Casa Minha Vida/Desenrola). Padrões restritos a golpe|fraude.",
        "estrategia_recomendada": "Busca por golpe|fraude + JsonCss (padrão Globo, igual ao G1)",
        "responsavel": "Daltro",
    },
    {
        "id": "uol-confere",
        "nome": "UOL Confere",
        "tipo": "checagem",
        "homepage": "https://noticias.uol.com.br/confere/",
        "rss": [],
        "sitemap": None,
        "editorias": ["checagem", "verificação"],
        "article_patterns": [r"confere", r"noticias\.uol\.com\.br/confere/.*"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .text-subtit",
            "autor": ".p-author, [itemprop='author']",
            "data": "time[itemprop='datePublished'], .p-date",
            "corpo": ".text, div[itemprop='articleBody']",
            "veredito_selo": "h1, h2, .selo",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Akamai barra curl (403); browser real deve passar. Mesmo cuidado anti-bot/paywall do UOL.",
        "estrategia_recomendada": "LLM local (selo) + laya p/ veredito + JsonCss (corpo)",
        "responsavel": "Tito",
    },
    {
        "id": "boatos",
        "nome": "Boatos.org",
        "tipo": "checagem",
        "homepage": "https://www.boatos.org/",
        "rss": [],
        "sitemap": None,
        "editorias": ["boatos", "whatsapp", "redes sociais"],
        "article_patterns": [r"boatos\.org/.*"],
        "css_hints": {
            "titulo": "h1.entry-title, h1",
            "subtitulo": "h2",
            "autor": ".author, [rel='author']",
            "data": "time[datetime]",
            "corpo": ".entry-content, article",
            "veredito_selo": "h1, .selo",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Foco em boatos de WhatsApp/redes; selo frequentemente no título. WordPress.",
        "estrategia_recomendada": "JsonCss + laya p/ veredito",
        "responsavel": "Tito",
    },
    {
        "id": "tatu",
        "nome": "Agência Tatu (jornalismo de dados)",
        "tipo": "checagem",
        "homepage": "https://www.agenciatatu.com.br/",
        "rss": [],
        "sitemap": None,
        "editorias": ["dados", "checagem", "transparência"],
        "article_patterns": [r"agenciatatu\.com\.br/.*"],
        "css_hints": {
            "titulo": "h1",
            "subtitulo": "h2, .excerpt",
            "autor": "[rel='author'], .author",
            "data": "time[datetime]",
            "corpo": "article, .post-content, main",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Agência de jornalismo de dados (não só fact-checking); filtrar peças de verificação no pós-processamento via laya (tipo_conteudo).",
        "estrategia_recomendada": "laya p/ separar checagem de reportagem de dados + JsonCss",
        "responsavel": "Tito",
    },
    {
        "id": "estadao-verifica",
        "nome": "Estadão Verifica",
        "tipo": "checagem",
        "homepage": "https://www.estadao.com.br/estadao-verifica/",
        "rss": [],
        "sitemap": None,
        "editorias": ["checagem", "verificação"],
        "article_patterns": [r"estadao-verifica", r"estadao\.com\.br/estadao-verifica/.*"],
        "css_hints": {
            "titulo": "h1.title, h1",
            "subtitulo": "h2.subtitle, .lead",
            "autor": ".author, [itemprop='author']",
            "data": "time[itemprop='datePublished']",
            "corpo": ".n--noticia__content, div[itemprop='articleBody']",
            "veredito_selo": "h1, h2, .selo",
            "imagem": "figure img, meta[property='og:image']",
        },
        "observacoes": "Paywall parcial do Estadão; coletar listagem + conteúdo público.",
        "estrategia_recomendada": "LLM local (selo) + laya p/ veredito + JsonCss (corpo)",
        "responsavel": "Guilherme",
    },
]

# ---------------------------------------------------------------------------
# Contrato canônico (Pydantic) — também exportado como JSON Schema
# ---------------------------------------------------------------------------
if PYDANTIC_AVAILABLE:
    class FonteNoticia(BaseModel):
        id: str = Field(..., description="ID do portal, ex: g1, cnn-brasil, fato-ou-fake")
        nome: str = Field(..., description="Nome de exibição do portal")
        homepage: str = Field(..., description="URL da homepage")
        tipo: str = Field(..., description="geral | checagem")

    class ImagemNoticia(BaseModel):
        src: Optional[str] = Field(default=None, description="URL absoluta da imagem principal")
        legenda: Optional[str] = Field(default=None, description="Legenda/crédito, se houver")
        alt: Optional[str] = Field(default=None, description="Texto alternativo")

    class ArtigoNoticia(BaseModel):
        """Contrato canônico de UMA matéria/checagem. Usar como interface entre
        o scraper (produtor) e a outra aplicação (consumidora)."""
        url: str = Field(..., description="URL canônica da matéria")
        titulo: str = Field(..., description="Título principal (h1)")
        subtitulo: Optional[str] = Field(default=None, description="Linha fina / chapéu")
        autores: List[str] = Field(default_factory=list, description="Autores/bylines")
        data_publicacao: Optional[str] = Field(default=None, description="ISO-8601, ex: 2026-09-21T10:00:00-03:00")
        data_atualizacao: Optional[str] = Field(default=None, description="ISO-8601 da atualização, se houver")
        editoria: Optional[str] = Field(default=None, description="Editoria/seção, ex: política, economia")
        tags: List[str] = Field(default_factory=list)
        corpo_texto: Optional[str] = Field(default=None, description="Texto limpo concatenado")
        corpo_markdown: Optional[str] = Field(default=None, description="Markdown fit gerado pelo Crawl4AI")
        imagem_principal: Optional[ImagemNoticia] = Field(default=None)
        fonte: FonteNoticia = Field(...)
        tipo_conteudo: str = Field(default="noticia", description="noticia | checagem")
        # Campos de fact-checking (preencher quando tipo_conteudo == checagem)
        veredito: Optional[str] = Field(default=None, description="Enum normalizado: FATO | FAKE | ENGANOSO | EXAGERADO | INSUSTENTAVEL | VERDADEIRO | FALSO | INCONCLUSIVO | DESCONTEXTUALIZADO")
        selo_original: Optional[str] = Field(default=None, description="Selo literal do portal, ex: 'É #FAKE'")
        metodo_checagem: Optional[str] = Field(default=None, description="Resumo de como foi checado / links de fonte")
        links_fontes: List[str] = Field(default_factory=list, description="URLs citadas na checagem")
        coletado_em: Optional[str] = Field(default=None, description="ISO-8601 da coleta")
        idioma: str = Field(default="pt-BR")
        # Proveniência da classificação (preenchido pelo pipeline híbrido laya/LLM)
        classificador: Optional[str] = Field(default=None, description="Motor que decidiu os campos enum (tipo_conteudo/veredito): laya | llm-local | fallback")
        confianca_veredito: Optional[float] = Field(default=None, description="Confiança calibrada (0-1) do veredito, quando via laya")

    class PortalSchema(BaseModel):
        """Contrato por portal: como (re)extrair barato sem LLM."""
        id: str
        nome: str
        tipo: str
        homepage: str
        rss: List[str] = Field(default_factory=list)
        sitemap: Optional[str] = None
        editorias: List[str] = Field(default_factory=list)
        article_url_patterns: List[str] = Field(default_factory=list)
        css_selectors: Dict[str, str] = Field(default_factory=dict)
        estrategia_recomendada: Optional[str] = None
        observacoes: Optional[str] = None
        exemplo_urls: List[str] = Field(default_factory=list)
        responsavel: Optional[str] = Field(default=None, description="Responsável da equipe pela cobertura (ex: Eliane, Gabi)")
        subsecoes: List[str] = Field(default_factory=list, description="Subseções/editorias especiais (ex: Radar Aos Fatos)")

    class CatalogoPortais(BaseModel):
        """Envelope final retornado pelo script."""
        versao_schema: str = "1.0.0"
        gerado_em: str
        modelo_llm: Optional[str] = None
        llm_base_url: Optional[str] = None
        artigo_json_schema: Dict[str, Any] = Field(..., description="JSON Schema do ArtigoNoticia")
        portal_json_schema: Dict[str, Any] = Field(..., description="JSON Schema do PortalSchema")
        portais: List[PortalSchema] = Field(...)
        css_extraction_schemas: Dict[str, Dict[str, Any]] = Field(
            default_factory=dict,
            description="Schemas prontos p/ JsonCssExtractionStrategy, chaveados por portal id",
        )
else:  # pragma: no cover
    FonteNoticia = ImagemNoticia = ArtigoNoticia = PortalSchema = CatalogoPortais = None  # type: ignore


# JSON Schemas estáticos (fallback quando pydantic não está instalado).
# Garantem que --offline sempre retorne um contrato utilizável.
STATIC_ARTIGO_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "ArtigoNoticia",
    "type": "object",
    "required": ["url", "titulo", "fonte"],
    "properties": {
        "url": {"type": "string", "format": "uri", "description": "URL canônica da matéria"},
        "titulo": {"type": "string", "description": "Título principal (h1)"},
        "subtitulo": {"type": ["string", "null"], "description": "Linha fina / chapéu"},
        "autores": {"type": "array", "items": {"type": "string"}},
        "data_publicacao": {"type": ["string", "null"], "description": "ISO-8601"},
        "data_atualizacao": {"type": ["string", "null"], "description": "ISO-8601 da atualização"},
        "editoria": {"type": ["string", "null"], "description": "Ex: política, economia"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "corpo_texto": {"type": ["string", "null"]},
        "corpo_markdown": {"type": ["string", "null"]},
        "imagem_principal": {
            "type": ["object", "null"],
            "properties": {
                "src": {"type": ["string", "null"]},
                "legenda": {"type": ["string", "null"]},
                "alt": {"type": ["string", "null"]},
            },
        },
        "fonte": {
            "type": "object",
            "required": ["id", "nome", "homepage", "tipo"],
            "properties": {
                "id": {"type": "string"},
                "nome": {"type": "string"},
                "homepage": {"type": "string", "format": "uri"},
                "tipo": {"type": "string", "enum": ["geral", "checagem"]},
            },
        },
        "tipo_conteudo": {"type": "string", "enum": ["noticia", "checagem"], "default": "noticia"},
        "veredito": {"type": ["string", "null"], "enum": ["FATO", "FAKE", "ENGANOSO", "EXAGERADO", "INSUSTENTAVEL", "VERDADEIRO", "FALSO", "INCONCLUSIVO", "DESCONTEXTUALIZADO", None]},
        "selo_original": {"type": ["string", "null"]},
        "metodo_checagem": {"type": ["string", "null"]},
        "links_fontes": {"type": "array", "items": {"type": "string", "format": "uri"}},
        "coletado_em": {"type": ["string", "null"], "description": "ISO-8601 da coleta"},
        "idioma": {"type": "string", "default": "pt-BR"},
        "classificador": {"type": ["string", "null"], "enum": ["laya", "llm-local", "fallback", None]},
        "confianca_veredito": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    },
}

STATIC_PORTAL_JSON_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "PortalSchema",
    "type": "object",
    "required": ["id", "nome", "tipo", "homepage"],
    "properties": {
        "id": {"type": "string"},
        "nome": {"type": "string"},
        "tipo": {"type": "string", "enum": ["geral", "checagem"]},
        "homepage": {"type": "string", "format": "uri"},
        "rss": {"type": "array", "items": {"type": "string"}},
        "sitemap": {"type": ["string", "null"]},
        "editorias": {"type": "array", "items": {"type": "string"}},
        "article_url_patterns": {"type": "array", "items": {"type": "string"}},
        "css_selectors": {"type": "object", "additionalProperties": {"type": "string"}},
        "estrategia_recomendada": {"type": ["string", "null"]},
        "observacoes": {"type": ["string", "null"]},
        "exemplo_urls": {"type": "array", "items": {"type": "string", "format": "uri"}},
        "responsavel": {"type": ["string", "null"]},
        "subsecoes": {"type": "array", "items": {"type": "string"}},
    },
}


LLM_INSTRUCTION_ARTIGO_PT = """\
A partir do conteúdo raspado (markdown/HTML) de uma página de NOTÍCIA brasileira,
extraia UM objeto JSON com exatamente estas chaves:
url, titulo, subtitulo, autores (lista), data_publicacao (ISO-8601 ou string original),
data_atualizacao, editoria, tags (lista), corpo_texto (texto corrido, sem menus),
imagem_principal {src, legenda}, tipo_conteudo ("noticia" ou "checagem"),
veredito (só se checagem: um de FATO, FAKE, ENGANOSO, EXAGERADO, VERDADEIRO, FALSO, INCONCLUSIVO, DESCONTEXTUALIZADO),
selo_original (texto literal do selo no portal), metodo_checagem, links_fontes (lista de URLs).

Regras:
- Responda SOMENTE com JSON válido, sem markdown fence, sem comentários.
- Se um campo não existir, use null (ou [] para listas).
- autores e tags sempre como listas de strings.
- Não invente datas/URLs: se ausente, null.
- corpo_texto: mínimo 2 frases quando houver conteúdo; sem navegação/rodapé.
Exemplo: {"url": "...", "titulo": "...", "subtitulo": null, "autores": ["..."], \
"data_publicacao": null, "data_atualizacao": null, "editoria": null, "tags": [], \
"corpo_texto": "...", "imagem_principal": {"src": null, "legenda": null}, \
"tipo_conteudo": "noticia", "veredito": null, "selo_original": null, \
"metodo_checagem": null, "links_fontes": []}
"""

# ---------------------------------------------------------------------------
# Classificador laya (https://github.com/NandhaKishorM/laya)
# Motor de decisão não-autoregressivo: responde perguntas tipadas (choice/noul)
# em ~33ms, sem gerar texto (sem alucinação), com confiança calibrada.
# Papel no pipeline HÍBRIDO: classifica os campos ENUM do contrato
# (tipo_conteudo, editoria, veredito). Campos de texto livre continuam com o
# LLM local; abaixo do limiar de confiança, mantém-se o valor do LLM.
# ---------------------------------------------------------------------------
# Limiar padrão 0.80 (não 0.85): os checkpoints base têm ECE ~0.08-0.21, logo
# confiança 0.80 já é um ponto de operação sólido p/ classificação assistiva em
# 6 opções (acaso=0.167) — e o pipeline registra a confiança e mantém o valor do
# LLM como fallback. Para ações automáticas sem revisão, prefira >= 0.90.
DEFAULT_LAYA_THRESHOLD = float(os.getenv("LAYA_THRESHOLD", "0.80"))
DEFAULT_LAYA_MODEL = os.getenv("LAYA_MODEL", "auto")  # auto | english | multilingual | typed-decisions
DEFAULT_LAYA_DEVICE = os.getenv("LAYA_DEVICE", "")    # vazio = auto (cuda se disponível)

LAYA_VEREDITO_MAP = {
    "verdadeiro": "VERDADEIRO",
    "falso": "FALSO",
    "enganoso": "ENGANOSO",
    "descontextualizado": "DESCONTEXTUALIZADO",
    "exagerado": "EXAGERADO",
    "inconclusivo": "INCONCLUSIVO",
}

LAYA_EDITORIA_MAP = {
    "politica": "política",
    "economia": "economia",
    "nacional": "nacional",
    "internacional": "internacional",
    "saude": "saúde",
    "ciencia-tecnologia": "tecnologia",
    "esporte": "esporte",
    "cultura": "cultura",
    "outra": None,
}


def build_laya_questions() -> Dict[str, Any]:
    """Perguntas tipadas em PT-BR. Rótulos curtos e disjuntos (recomendação do
    próprio projeto: >20 opções ou sinônimos sobrepostos degradam o acerto)."""
    return {
        "tem_selo": {
            "type": "noul",
            "instructions": "O texto afirma que a informação é falsa?",
        },
        "tipo": {
            "type": "choice",
            "instructions": "A manchete abaixo é uma notícia comum ou uma checagem de fatos?",
            "criteria": {
                "noticia": "reportagem jornalística comum sobre um acontecimento",
                "checagem": "verificação de fato, desmentido ou confirmação com selo",
            },
        },
        "editoria": {
            "type": "choice",
            "instructions": "Qual a editoria da notícia?",
            "criteria": {
                "politica": "governo, congresso, eleições, partidos",
                "economia": "juros, inflação, mercado, empresas",
                "nacional": "brasil, cidades, cotidiano",
                "internacional": "mundo, outros países",
                "saude": "saúde, vacinas, hospitais",
                "ciencia-tecnologia": "ciência, tecnologia, internet",
                "esporte": "futebol, olimpíadas, esportes",
                "cultura": "cinema, música, livros, famosos",
                "outra": "nenhuma das anteriores",
            },
        },
        "veredito": {
            "type": "choice",
            "instructions": "Se for checagem de fatos, qual o veredito? Se não for checagem, responda inconclusivo.",
            "criteria": {
                "verdadeiro": "conteúdo confirmado como verdadeiro",
                "falso": "conteúdo falso, fake news desmentida",
                "enganoso": "parcialmente verdadeiro mas induz ao erro",
                "descontextualizado": "fato real usado fora do contexto original",
                "exagerado": "fato com exagero ou distorção",
                "inconclusivo": "não é checagem ou sem conclusão",
            },
        },
    }


_laya_router = None


def get_laya_router(model: str = DEFAULT_LAYA_MODEL, device: str = DEFAULT_LAYA_DEVICE, preload: bool = False):
    """Singleton do Router laya. model='auto' deixa o roteamento por idioma
    (PT-BR -> checkpoint multilíngue). Primeira chamada baixa os pesos do HF.
    preload=False (padrão): carrega sob demanda só o checkpoint usado."""
    global _laya_router
    if _laya_router is not None:
        return _laya_router
    if not LAYA_AVAILABLE:
        raise RuntimeError("laya não instalado. Rode: pip install laya")
    kw: Dict[str, Any] = {"preload": preload}
    if device:
        kw["device"] = device
    _laya_router = _laya.Router(**kw)
    log.info("laya Router pronto (model=%s, device=%s)", model, device or "auto")
    return _laya_router


def classify_with_laya(titulo: str = "", subtitulo: str = "", corpo: str = "",
                       threshold: float = DEFAULT_LAYA_THRESHOLD,
                       laya_model: str = DEFAULT_LAYA_MODEL) -> Dict[str, Any]:
    """Classifica UMA matéria com o laya. Retorna campos + confianças + flags de
    aceite pelo limiar. Nunca levanta exceção (falha -> tudo None, pipeline segue)."""
    out: Dict[str, Any] = {
        "tipo_conteudo": None, "editoria": None, "veredito": None,
        "tem_selo": None, "confiancas": {}, "aceito": {},
        "laya_modelo_usado": None, "erro": None,
    }
    try:
        router = get_laya_router(model=laya_model)
        estado = " ".join(p for p in [titulo or "", subtitulo or "", (corpo or "")[:2000]] if p).strip()
        if not estado:
            out["erro"] = "estado vazio"
            return out
        model_override = None if laya_model == "auto" else laya_model
        res = router.predict({"manchete": titulo or "", "texto": estado},
                             build_laya_questions(), model=model_override)
        answers = res.get("answers", {})
        out["laya_modelo_usado"] = (res.get("routing", {}) or {}).get("model")

        selo = (answers.get("tem_selo") or {}).get("noul", 0.0) or 0.0
        out["tem_selo"] = float(selo)

        tipo = answers.get("tipo") or {}
        tipo_choice, tipo_conf = tipo.get("choice"), float(tipo.get("confidence", 0.0) or 0.0)
        out["confiancas"]["tipo_conteudo"] = tipo_conf
        # OU lógico: choice confiante OU selo forte (noul validado como primitiva forte)
        tipo_is_checagem = (tipo_choice == "checagem" and tipo_conf >= threshold) or selo >= 0.80
        if tipo_is_checagem:
            out["tipo_conteudo"] = "checagem"
            out["aceito"]["tipo_conteudo"] = True
        elif tipo_choice == "noticia" and tipo_conf >= threshold:
            out["tipo_conteudo"] = "noticia"
            out["aceito"]["tipo_conteudo"] = True
        else:
            out["aceito"]["tipo_conteudo"] = False

        ed = answers.get("editoria") or {}
        ed_choice, ed_conf = ed.get("choice"), float(ed.get("confidence", 0.0) or 0.0)
        out["confiancas"]["editoria"] = ed_conf
        if ed_conf >= threshold and ed_choice in LAYA_EDITORIA_MAP and LAYA_EDITORIA_MAP[ed_choice]:
            out["editoria"] = LAYA_EDITORIA_MAP[ed_choice]
            out["aceito"]["editoria"] = True
        else:
            out["aceito"]["editoria"] = False

        vd = answers.get("veredito") or {}
        vd_choice, vd_conf = vd.get("choice"), float(vd.get("confidence", 0.0) or 0.0)
        out["confiancas"]["veredito"] = vd_conf
        # A pergunta do veredito instrui responder "inconclusivo" se não for checagem;
        # logo, um veredito confiante e não-inconclusivo é por si só evidência de
        # checagem — aceita mesmo se o "tipo" veio só pelo sinal do selo.
        veredito_valido = vd_conf >= threshold and (vd_choice or "") in LAYA_VEREDITO_MAP
        if veredito_valido and (tipo_is_checagem or (selo >= 0.60 and vd_choice != "inconclusivo")):
            out["veredito"] = LAYA_VEREDITO_MAP[vd_choice]
            out["aceito"]["veredito"] = True
            if not tipo_is_checagem:
                out["tipo_conteudo"] = "checagem"
                out["aceito"]["tipo_conteudo"] = True
        else:
            out["aceito"]["veredito"] = False
    except Exception as e:
        out["erro"] = str(e)[:300]
        log.warning("laya falhou (seguindo com LLM/fallback): %s", e)
    return out


def apply_laya_classification(artigos: List[Dict[str, Any]],
                              threshold: float = DEFAULT_LAYA_THRESHOLD,
                              laya_model: str = DEFAULT_LAYA_MODEL) -> Dict[str, int]:
    """Passa o laya sobre os artigos já extraídos; sobrescreve campos ENUM só
    quando a confiança passa no limiar. Retorna estatísticas p/ o relatório."""
    stats = {"artigos": len(artigos), "tipo_aceito": 0, "editoria_aceita": 0,
             "veredito_aceito": 0, "erros": 0}
    if not artigos:
        return stats
    for a in artigos:
        r = classify_with_laya(a.get("titulo", ""), a.get("subtitulo", "") or "",
                               a.get("corpo_texto", "") or "", threshold, laya_model)
        if r.get("erro") and not r.get("confiancas"):
            stats["erros"] += 1
            continue
        mudou = False
        if r["aceito"].get("tipo_conteudo") and r.get("tipo_conteudo"):
            a["tipo_conteudo"] = r["tipo_conteudo"]
            stats["tipo_aceito"] += 1
            mudou = True
        if r["aceito"].get("editoria") and r.get("editoria"):
            a["editoria"] = r["editoria"]
            stats["editoria_aceita"] += 1
            mudou = True
        if r["aceito"].get("veredito") and r.get("veredito"):
            a["veredito"] = r["veredito"]
            a["confianca_veredito"] = round(r["confiancas"]["veredito"], 4)
            stats["veredito_aceito"] += 1
            mudou = True
        elif r["confiancas"].get("veredito") is not None and a.get("tipo_conteudo") == "checagem":
            # registra a confiança mesmo quando mantém o veredito do LLM
            a["confianca_veredito"] = round(r["confiancas"]["veredito"], 4)
        if mudou:
            a["classificador"] = "laya"
            if r.get("laya_modelo_usado"):
                a["classificador"] = f"laya:{r['laya_modelo_usado']}"
    return stats


# ---------------------------------------------------------------------------
# Helpers — LLM local
# ---------------------------------------------------------------------------
def get_llm_config(modelo: str = "", base_url: str = DEFAULT_BASE_URL, api_token: str = DEFAULT_API_TOKEN):
    """Monta LLMConfig do Crawl4AI apontando para o servidor local (Unsloth)."""
    if LLMConfig is None:
        raise RuntimeError("crawl4ai não instalado. Rode: pip install -U crawl4ai")
    provider = f"openai/{modelo}" if modelo else "openai/gpt-4o-mini"
    # base_url customizado é o que permite usar Unsloth/vLLM/llama.cpp/ollama local.
    try:
        return LLMConfig(provider=provider, api_token=api_token or "not-needed", base_url=base_url)
    except TypeError:
        # versões antigas do LLMConfig sem base_url explícito: usa kwargs extras
        return LLMConfig(provider=provider, api_token=api_token or "not-needed", **{"base_url": base_url})


def detect_local_model(base_url: str = DEFAULT_BASE_URL, timeout: float = 8.0) -> str:
    """Tenta GET {base_url}/models e retorna o id do 1º modelo. Retorna '' se falhar."""
    if not HTTPX_AVAILABLE:
        return ""
    url = base_url.rstrip("/") + "/models"
    try:
        r = httpx.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        items = data.get("data", []) if isinstance(data, dict) else []
        if items and isinstance(items[0], dict) and items[0].get("id"):
            return str(items[0]["id"])
    except Exception as e:
        log.warning("Não foi possível autodetectar modelo em %s: %s", url, e)
    return ""


# ---------------------------------------------------------------------------
# Helpers — crawl4ai configs
# ---------------------------------------------------------------------------
def build_homepage_config(usar_llm: bool = False, llm_config=None) -> "CrawlerRunConfig":
    kwargs: Dict[str, Any] = dict(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=10,
        scan_full_page=False,
        scroll_delay=0.3,
        page_timeout=45000,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
        verbose=True,
    )
    if usar_llm and llm_config is not None and LLMExtractionStrategy is not None:
        # Na homepage queremos LISTA de chamadas (título+url+editoria), não o artigo.
        kwargs["extraction_strategy"] = LLMExtractionStrategy(
            llm_config=llm_config,
            extraction_type="schema",
            instruction=(
                "Extraia da homepage uma LISTA de destaques com titulo, url e editoria. "
                "Responda somente JSON válido: lista de objetos."
            ),
            input_format="fit_markdown",
            chunk_token_threshold=2000,
            overlap_rate=0.0,
            apply_chunking=False,
            extra_args={"temperature": 0.0, "max_tokens": 1500},
            verbose=True,
        )
    return CrawlerRunConfig(**kwargs)


def build_article_config(llm_config=None) -> "CrawlerRunConfig":
    """Config de extração por artigo: LLM local (robusto) com input fit_markdown."""
    if llm_config is None or LLMExtractionStrategy is None:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS, word_count_threshold=30, verbose=True,
        )
    strategy = LLMExtractionStrategy(
        llm_config=llm_config,
        extraction_type="schema",
        instruction=LLM_INSTRUCTION_ARTIGO_PT,
        input_format="fit_markdown",
        chunk_token_threshold=4000,
        overlap_rate=0.05,
        apply_chunking=True,
        extra_args={"temperature": 0.0, "max_tokens": 2000},
        verbose=True,
    )
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=30,
        extraction_strategy=strategy,
        verbose=True,
    )


def build_css_listing_schema() -> Dict[str, Any]:
    """Schema barato (sem LLM) para listar chamadas da homepage. Reutilizável."""
    return {
        "name": "Chamadas homepage",
        "baseSelector": "a[href]",
        "fields": [
            {"name": "titulo", "selector": "h1,h2,h3,.title", "type": "text", "default": ""},
            {"name": "url", "type": "attribute", "attribute": "href", "default": ""},
            {"name": "texto_link", "type": "text", "default": ""},
        ],
    }


def build_css_article_schema(portal: Dict[str, Any]) -> Dict[str, Any]:
    """Gera um JsonCssExtractionStrategy-schema por portal a partir dos css_hints.
    O portal consumidor pode usar direto, sem LLM (rápido/barato)."""
    hints = portal.get("css_hints", {})
    return {
        "name": f"Artigo — {portal['nome']}",
        "baseSelector": "article, main, body",
        "fields": [
            {"name": "titulo", "selector": hints.get("titulo", "h1"), "type": "text", "default": ""},
            {"name": "subtitulo", "selector": hints.get("subtitulo", "h2"), "type": "text", "default": ""},
            {"name": "autor", "selector": hints.get("autor", "[rel='author']"), "type": "text", "default": ""},
            {"name": "data", "selector": hints.get("data", "time"), "type": "text", "default": ""},
            {"name": "corpo", "selector": hints.get("corpo", "article"), "type": "text", "default": ""},
            {"name": "imagem", "selector": hints.get("imagem", "figure img"), "type": "attribute", "attribute": "src", "default": ""},
        ],
    }


def is_article_url(url: str, patterns: List[str]) -> bool:
    try:
        return any(re.search(p, url) for p in patterns)
    except re.error:
        return False


# Substrings que indicam URL utilitária (login, paywall, playlist etc.) — nunca é matéria.
URL_JUNK_HINTS = (
    "login", "paywall", "playlist", "registro-de-dominio", "programacao-",
    "agregador-de-pesquisas", "/topics/", "/academia", "/observatorio-",
    "minhafolha", "/playlist",
)


def pick_article_urls(links: Dict[str, Any], portal: Dict[str, Any], homepage: str, limite: int = 5) -> List[str]:
    """Heurística: filtra links internos que casam com article_patterns do portal."""
    vistos: List[str] = []
    internos = (links or {}).get("internal", []) or []

    def _junk(abs_url: str) -> bool:
        low = abs_url.lower()
        return any(h in low for h in URL_JUNK_HINTS)

    for item in internos:
        href = item.get("href", "") if isinstance(item, dict) else str(item)
        if not href:
            continue
        abs_url = urljoin(homepage, href).split("#")[0]
        if urlparse(abs_url).netloc not in urlparse(homepage).netloc and urlparse(homepage).netloc not in urlparse(abs_url).netloc:
            continue
        if _junk(abs_url):
            continue
        if is_article_url(abs_url, portal.get("article_patterns", [])) and abs_url not in vistos:
            vistos.append(abs_url)
        if len(vistos) >= limite * 3:  # coleta folga p/ filtrar depois
            break
    # Se o portal tem padrão restritivo demais e nada casou, pega os 1os internos longos
    if not vistos:
        for item in internos:
            href = item.get("href", "") if isinstance(item, dict) else str(item)
            abs_url = urljoin(homepage, href).split("#")[0]
            if _junk(abs_url):
                continue
            if len(abs_url) > len(homepage) + 15 and abs_url not in vistos:
                vistos.append(abs_url)
            if len(vistos) >= limite:
                break
    return vistos[:limite]


def to_artigo_canonical(raw: Dict[str, Any], portal: Dict[str, Any], url: str,
                        markdown: str = "", metadata: Optional[Dict] = None,
                        classificador: Optional[str] = None) -> Dict[str, Any]:
    """Normaliza a saída do LLM (ou fallback) para o contrato ArtigoNoticia."""
    meta = metadata or {}
    img = raw.get("imagem_principal") if isinstance(raw.get("imagem_principal"), dict) else None
    artigo = {
        "url": raw.get("url") or url,
        "titulo": raw.get("titulo") or meta.get("title") or "",
        "subtitulo": raw.get("subtitulo"),
        "autores": raw.get("autores") or ([meta.get("author")] if meta.get("author") else []),
        "data_publicacao": raw.get("data_publicacao"),
        "data_atualizacao": raw.get("data_atualizacao"),
        "editoria": raw.get("editoria"),
        "tags": raw.get("tags") or [],
        "corpo_texto": raw.get("corpo_texto"),
        "corpo_markdown": (markdown or "")[:20000] or None,
        "imagem_principal": {
            "src": (img or {}).get("src") or meta.get("og:image"),
            "legenda": (img or {}).get("legenda"),
            "alt": None,
        },
        "fonte": {"id": portal["id"], "nome": portal["nome"], "homepage": portal["homepage"], "tipo": portal["tipo"]},
        "tipo_conteudo": raw.get("tipo_conteudo") or ("checagem" if portal["tipo"] == "checagem" else "noticia"),
        "veredito": raw.get("veredito"),
        "selo_original": raw.get("selo_original"),
        "metodo_checagem": raw.get("metodo_checagem"),
        "links_fontes": raw.get("links_fontes") or [],
        "coletado_em": datetime.now(timezone.utc).isoformat(),
        "idioma": "pt-BR",
        "classificador": classificador,
        "confianca_veredito": None,
    }
    # Validação best-effort com Pydantic (não quebra o crawl se falhar)
    if PYDANTIC_AVAILABLE and ArtigoNoticia is not None:
        try:
            artigo = ArtigoNoticia(**artigo).model_dump()
        except ValidationError as e:
            log.warning("Artigo fora do contrato (%s): %s", url, e)
    return artigo


def fallback_article_from_crawl(url: str, portal: Dict[str, Any], result) -> Dict[str, Any]:
    """Quando o LLM local está offline/falha: monta artigo só com metadata+markdown."""
    md = getattr(result, "markdown", None)
    texto_md = getattr(md, "fit_markdown", None) or getattr(md, "raw_markdown", None) or ""
    if not isinstance(texto_md, str):
        texto_md = str(texto_md or "")
    metadata = getattr(result, "metadata", None) or {}
    linhas = [l.strip("# ").strip() for l in texto_md.splitlines() if l.strip()]
    titulo = metadata.get("title") or (linhas[0] if linhas else "")
    return to_artigo_canonical(
        {"titulo": titulo, "corpo_texto": texto_md[:8000].strip() or None},
        portal, url, markdown=texto_md, metadata=metadata,
        classificador="fallback",
    )


# ---------------------------------------------------------------------------
# Fases de crawl
# ---------------------------------------------------------------------------
async def crawl_homepages(portais: List[Dict], usar_llm: bool, llm_config,
                          max_concorrencia: int = 4) -> List[Dict[str, Any]]:
    # NOTA: crawl sequencial de propósito. O arun_many() do Crawl4AI retorna os
    # resultados em ordem de conclusão (não na ordem das URLs), o que embaralhava
    # o vínculo portal <-> homepage. Homepages levam ~1-4s cada; o sequencial é
    # rápido o suficiente e garante o mapeamento correto.
    browser = BrowserConfig(headless=True, verbose=True, viewport_width=1280, viewport_height=800)
    run_cfg = build_homepage_config(usar_llm=usar_llm, llm_config=llm_config)
    resultados: List[Dict[str, Any]] = []
    async with AsyncWebCrawler(config=browser) as crawler:
        for portal in portais:
            try:
                res = await crawler.arun(url=portal["homepage"], config=run_cfg)
            except Exception as e:
                log.warning("[homepage] %s ERRO: %s", portal["id"], e)
                res = None
            if res is None:
                resultados.append({
                    "portal": portal, "url": portal["homepage"], "success": False,
                    "error": "exception no arun", "markdown_len": 0, "links": {},
                    "media": {}, "metadata": {}, "fit_markdown": "", "result_obj": None,
                })
                continue
            md = getattr(res, "markdown", None)
            resultados.append({
                "portal": portal,
                "url": getattr(res, "url", portal["homepage"]),
                "success": bool(getattr(res, "success", False)),
                "error": getattr(res, "error_message", None),
                "markdown_len": len(getattr(md, "fit_markdown", "") or "") if md else 0,
                "links": getattr(res, "links", {}) or {},
                "media": getattr(res, "media", {}) or {},
                "metadata": getattr(res, "metadata", {}) or {},
                "fit_markdown": (getattr(md, "fit_markdown", "") or "")[:12000] if md else "",
                "result_obj": res,
            })
            status = "OK" if resultados[-1]["success"] else f"FALHA: {resultados[-1]['error']}"
            log.info("[homepage] %s -> %s", portal["id"], status)
    return resultados


async def deep_discover(portal: Dict, llm_config, max_pages: int = 10) -> List[str]:
    """BFS limitado (depth 1) para descobrir URLs de matéria. Retorna lista de URLs."""
    if not DEEP_CRAWL_AVAILABLE:
        log.warning("[deep] crawl4ai.deep_crawling indisponível; pulando Deep para %s", portal["id"])
        return []
    filt = FilterChain([ContentTypeFilter(allowed_types=["text/html"])])
    strategy = BFSDeepCrawlStrategy(max_depth=1, max_pages=max_pages, include_external=False, filter_chain=filt)
    cfg = CrawlerRunConfig(deep_crawl_strategy=strategy, cache_mode=CacheMode.BYPASS,
                           word_count_threshold=10, verbose=False, prefetch=True)
    urls: List[str] = []
    try:
        async with AsyncWebCrawler() as crawler:
            out = await crawler.arun(url=portal["homepage"], config=cfg)
            seq = out if isinstance(out, list) else [out]
            for r in seq:
                u = getattr(r, "url", "")
                if u and is_article_url(u, portal.get("article_patterns", [])):
                    urls.append(u)
    except Exception as e:
        log.warning("[deep] %s falhou: %s", portal["id"], e)
    return urls


async def crawl_articles(portal: Dict, urls: List[str], llm_config, usar_llm: bool) -> List[Dict[str, Any]]:
    artigos: List[Dict[str, Any]] = []
    browser = BrowserConfig(headless=True, verbose=False)
    run_cfg = build_article_config(llm_config) if usar_llm else CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS, word_count_threshold=30, verbose=False)
    async with AsyncWebCrawler(config=browser) as crawler:
        for u in urls:
            try:
                res = await crawler.arun(url=u, config=run_cfg)
                if not getattr(res, "success", False):
                    log.warning("[artigo] FAIL %s: %s", u, getattr(res, "error_message", "?"))
                    continue
                raw: Dict[str, Any] = {}
                if usar_llm and getattr(res, "extracted_content", None):
                    try:
                        parsed = json.loads(res.extracted_content)
                        raw = parsed[0] if isinstance(parsed, list) and parsed else (parsed if isinstance(parsed, dict) else {})
                    except (json.JSONDecodeError, TypeError) as e:
                        log.warning("[artigo] LLM retornou JSON inválido (%s): %s", u, e)
                        artigos.append(fallback_article_from_crawl(u, portal, res))
                        continue
                if raw:
                    md = getattr(res, "markdown", None)
                    md_txt = (getattr(md, "fit_markdown", "") or "") if md else ""
                    artigos.append(to_artigo_canonical(raw, portal, u, markdown=md_txt,
                                                       metadata=getattr(res, "metadata", {}) or {},
                                                       classificador="llm-local" if usar_llm else "fallback"))
                    log.info("[artigo] OK %s", u)
                else:
                    artigos.append(fallback_article_from_crawl(u, portal, res))
                    log.info("[artigo] OK (fallback sem LLM) %s", u)
            except Exception as e:
                log.warning("[artigo] erro %s: %s", u, e)
    return artigos


# ---------------------------------------------------------------------------
# Consolidação — o SCHEMA final (contrato)
# ---------------------------------------------------------------------------
def build_final_schema(portais: List[Dict], exemplo_urls: Dict[str, List[str]],
                       modelo: str, base_url: str) -> Dict[str, Any]:
    """Monta o CatalogoPortais serializável (o contrato da outra aplicação)."""
    if PYDANTIC_AVAILABLE and CatalogoPortais is not None:
        artigo_schema = ArtigoNoticia.model_json_schema()
        portal_schema = PortalSchema.model_json_schema()
        lista_portais = [
            PortalSchema(
                id=p["id"], nome=p["nome"], tipo=p["tipo"], homepage=p["homepage"],
                rss=p.get("rss", []), sitemap=p.get("sitemap"),
                editorias=p.get("editorias", []),
                article_url_patterns=p.get("article_patterns", []),
                css_selectors=p.get("css_hints", {}),
                estrategia_recomendada=p.get("estrategia_recomendada"),
                observacoes=p.get("observacoes"),
                exemplo_urls=exemplo_urls.get(p["id"], []),
                responsavel=p.get("responsavel"),
                subsecoes=p.get("subsecoes", []),
            ).model_dump()
            for p in portais
        ]
        cat = CatalogoPortais(
            gerado_em=datetime.now(timezone.utc).isoformat(),
            modelo_llm=modelo or None, llm_base_url=base_url,
            artigo_json_schema=artigo_schema, portal_json_schema=portal_schema,
            portais=lista_portais,  # type: ignore
            css_extraction_schemas={p["id"]: build_css_article_schema(p) for p in portais},
        )
        return cat.model_dump()
    # Fallback sem pydantic: usa os schemas estáticos (contrato continua válido)
    return {
        "versao_schema": "1.0.0",
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "modelo_llm": modelo or None,
        "llm_base_url": base_url,
        "artigo_json_schema": STATIC_ARTIGO_JSON_SCHEMA,
        "portal_json_schema": STATIC_PORTAL_JSON_SCHEMA,
        "portais": [
            {**{k: p.get(k) for k in ("id", "nome", "tipo", "homepage")},
             "rss": p.get("rss", []), "sitemap": p.get("sitemap"),
             "editorias": p.get("editorias", []),
             "article_url_patterns": p.get("article_patterns", []),
             "css_selectors": p.get("css_hints", {}),
             "estrategia_recomendada": p.get("estrategia_recomendada"),
             "observacoes": p.get("observacoes"),
             "exemplo_urls": exemplo_urls.get(p["id"], []),
             "responsavel": p.get("responsavel"),
             "subsecoes": p.get("subsecoes", [])}
            for p in portais
        ],
        "css_extraction_schemas": {p["id"]: build_css_article_schema(p) for p in portais},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def amain(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(message)s")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    portais = [p for p in PORTAIS if not args.portais or p["id"] in args.portais]
    if not portais:
        log.error("Nenhum portal bate com --portais %s. IDs: %s", args.portais, [p["id"] for p in PORTAIS])
        return 2

    modelo = args.modelo or DEFAULT_MODEL
    base_url = args.base_url or DEFAULT_BASE_URL
    usar_llm = (not args.no_llm) and (not args.offline)

    # ---- Fase 0: probe LLM local ----
    if usar_llm and not modelo:
        modelo = detect_local_model(base_url)
        if modelo:
            log.info("Modelo local autodetectado: %s", modelo)
    llm_config = None
    if usar_llm:
        if not CRAWL4AI_AVAILABLE:
            log.error("crawl4ai não instalado — não dá para crawlear. Use --offline ou instale: pip install -U crawl4ai && crawl4ai-setup")
            return 3
        if not modelo:
            log.warning("Nenhum modelo detectado em %s/v1/models. Tentando 'default' e seguindo; "
                        "informe --modelo ou UNSLOTH_MODEL_NAME. O crawl segue com fallback sem LLM se falhar.",
                        base_url.rstrip("/"))
            modelo = "default"
        try:
            llm_config = get_llm_config(modelo, base_url, args.api_token or DEFAULT_API_TOKEN)
            log.info("LLM local: provider=openai/%s base_url=%s", modelo, base_url)
        except Exception as e:
            log.warning("Falha ao montar LLMConfig (%s). Seguindo sem LLM.", e)
            usar_llm = False
            llm_config = None

    # ---- Modo offline: só emite o contrato ----
    if args.offline:
        schema = build_final_schema(portais, {}, modelo, base_url)
        (out_dir / "portais_schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(schema, indent=2, ensure_ascii=False)[:6000])
        log.info("Modo --offline: contrato salvo em %s (sem rede, sem LLM).", out_dir / "portais_schema.json")
        return 0

    if not CRAWL4AI_AVAILABLE:
        log.error("crawl4ai não instalado. Instale com: pip install -U crawl4ai && crawl4ai-setup. Ou use --offline.")
        return 3

    # ---- Fase 1: homepages ----
    log.info("Fase 1: crawl sequencial de %d homepages...", len(portais))
    homes = await crawl_homepages(portais, usar_llm=False, llm_config=None)

    # ---- Fase 2+3: deep opcional + seleção de artigos ----
    exemplo_urls: Dict[str, List[str]] = {}
    alvos: Dict[str, List[str]] = {}
    for h in homes:
        portal = h["portal"]
        if not h["success"]:
            exemplo_urls[portal["id"]] = []
            alvos[portal["id"]] = []
            continue
        urls = pick_article_urls(h["links"], portal, portal["homepage"], limite=args.max_artigos)
        if args.deep:
            log.info("Fase 2: deep BFS %s (max_pages=%d)...", portal["id"], args.max_pages)
            deep_urls = await deep_discover(portal, llm_config, max_pages=args.max_pages)
            for u in deep_urls:
                if u not in urls and len(urls) < args.max_artigos:
                    urls.append(u)
        exemplo_urls[portal["id"]] = urls
        alvos[portal["id"]] = urls[:args.max_artigos]
        log.info("[seleção] %s: %d URLs -> %s", portal["id"], len(urls), urls)

    # ---- Fase 4: extração de artigos ----
    todos_artigos: List[Dict[str, Any]] = []
    for h in homes:
        portal = h["portal"]
        urls = alvos.get(portal["id"], [])
        if not urls:
            continue
        log.info("Fase 4: extraindo %d artigos de %s (LLM=%s)...", len(urls), portal["id"], usar_llm)
        arts = await crawl_articles(portal, urls, llm_config, usar_llm=usar_llm)
        todos_artigos.extend(arts)

    # ---- Fase 4b: classificação laya (híbrida, com gating de confiança) ----
    laya_stats: Optional[Dict[str, Any]] = None
    if args.classifier == "laya" and todos_artigos:
        if not LAYA_AVAILABLE:
            log.warning("--classifier laya pedido mas pacote 'laya' não instalado (pip install laya). Pulando.")
        else:
            # Roda em thread separada: torch/inferência bloqueante não deve travar o loop asyncio
            log.info("Fase 4b: classificando %d artigos com laya (threshold=%.2f, model=%s)...",
                     len(todos_artigos), args.laya_threshold, args.laya_model)
            laya_stats = await asyncio.to_thread(
                apply_laya_classification, todos_artigos, args.laya_threshold, args.laya_model)
            log.info("laya: %s", laya_stats)

    # ---- Fase 5: schema final + arquivos ----
    schema = build_final_schema(portais, exemplo_urls, modelo, base_url)
    (out_dir / "portais_schema.json").write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "artigos_amostra.json").write_text(json.dumps(todos_artigos, indent=2, ensure_ascii=False), encoding="utf-8")
    report = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "modelo_llm": modelo, "llm_base_url": base_url, "usou_llm": usar_llm,
        "classificador": args.classifier,
        "laya_threshold": args.laya_threshold if args.classifier == "laya" else None,
        "laya_model": args.laya_model if args.classifier == "laya" else None,
        "laya_stats": laya_stats,
        "portais": [
            {"id": h["portal"]["id"], "homepage": h["portal"]["homepage"],
             "success": h["success"], "error": h["error"],
             "markdown_len": h["markdown_len"],
             "n_links_internos": len((h["links"] or {}).get("internal", []) or []),
             "n_artigos_extraidos": len([a for a in todos_artigos if a.get("fonte", {}).get("id") == h["portal"]["id"]]),
             "exemplo_urls": exemplo_urls.get(h["portal"]["id"], [])}
            for h in homes
        ],
        "total_artigos": len(todos_artigos),
    }
    (out_dir / "crawl_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n================ SCHEMA (contrato) ================")
    print(f"Versão: {schema['versao_schema']} | Portais: {len(schema['portais'])} | Artigos amostra: {len(todos_artigos)}")
    print(f"Arquivos em ./{out_dir}/: portais_schema.json, artigos_amostra.json, crawl_report.json")
    print("\n--- ArtigoNoticia JSON Schema (resumo) ---")
    props = (schema.get("artigo_json_schema", {}) or {}).get("properties", {})
    print(json.dumps({"required": (schema.get("artigo_json_schema", {}) or {}).get("required", []),
                      "properties": list(props.keys())}, indent=2, ensure_ascii=False))
    print("\n--- Por portal (id -> estratégia) ---")
    for p in schema["portais"]:
        print(f"  - {p['id']}: {p.get('estrategia_recomendada', '')} | ex: {(p.get('exemplo_urls') or ['—'])[0]}")
    if laya_stats:
        print("\n--- laya (classificação híbrida) ---")
        print(json.dumps(laya_stats, indent=2, ensure_ascii=False))
    print("\nReuso rápido (outra aplicação / scraping barato sem LLM):")
    print("  from crawl4ai import JsonCssExtractionStrategy")
    print("  import json; cat = json.load(open('output/portais_schema.json'))")
    print("  strat = JsonCssExtractionStrategy(cat['css_extraction_schemas']['g1'])")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Crawl4AI + Unsloth local: notícias BR -> schema reutilizável")
    ap.add_argument("--portais", nargs="*", default=[], help="IDs a crawlear (padrão: todos). Ex: --portais g1 fato-ou-fake cnn-brasil")
    ap.add_argument("--max-artigos", type=int, default=4, help="Artigos por portal (padrão 4)")
    ap.add_argument("--deep", action="store_true", help="Ativa descoberta BFS limitada por portal")
    ap.add_argument("--max-pages", type=int, default=10, help="max_pages do BFS por portal (padrão 10)")
    ap.add_argument("--no-llm", action="store_true", help="Não usa LLM; só metadata/markdown + CSS (mais rápido)")
    ap.add_argument("--offline", action="store_true", help="Só emite o schema curado, sem rede e sem LLM")
    ap.add_argument("--output-dir", default="output", help="Diretório de saída (padrão output)")
    ap.add_argument("--modelo", default="", help="Nome do modelo no servidor local (ou env UNSLOTH_MODEL_NAME)")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL OpenAI-compatível (padrão http://127.0.0.1:8888/v1)")
    ap.add_argument("--api-token", default="", help="API token (padrão 'not-needed' p/ servidor local)")
    ap.add_argument("--classifier", choices=["none", "laya"], default="none",
                    help="Classificador dos campos ENUM (tipo_conteudo/editoria/veredito): none (só LLM) ou laya (híbrido com gating de confiança)")
    ap.add_argument("--laya-threshold", type=float, default=DEFAULT_LAYA_THRESHOLD,
                    help="Confiança mínima do laya p/ sobrescrever o LLM (padrão 0.80, ou env LAYA_THRESHOLD)")
    ap.add_argument("--laya-model", default=DEFAULT_LAYA_MODEL,
                    help="Checkpoint laya: auto (roteia p/ multilíngue em PT-BR) | english | multilingual | typed-decisions (ou env LAYA_MODEL)")
    ap.add_argument("--laya-device", default=DEFAULT_LAYA_DEVICE,
                    help="Dispositivo do laya (ex: cuda, cpu; vazio=auto, ou env LAYA_DEVICE)")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(amain(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
