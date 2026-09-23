"""Configuração via ambiente (.env). Nenhum segredo tem default utilizável."""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


TELEGRAM_TOKEN = _get("TELEGRAM_TOKEN")
SERPAPI_KEY = _get("SERPAPI_KEY")
OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
OPENROUTER_FALLBACK_MODEL = _get("OPENROUTER_FALLBACK_MODEL", "google/gemma-4-26b-a4b-it:free")
# Roteamento de provider (ex: StreamLake fp8 p/ deepseek-v4-flash).
# Lista separada por vírgula; vazio = roteamento padrão da OpenRouter.
OPENROUTER_PROVIDER_ORDER = _get("OPENROUTER_PROVIDER_ORDER", "StreamLake")
OPENROUTER_TIMEOUT_S = _int("OPENROUTER_TIMEOUT_S", 30)
OPENROUTER_MAX_TOKENS = _int("OPENROUTER_MAX_TOKENS", 600)
LLM_DAILY_CAP = _int("LLM_DAILY_CAP", 50)
# TypeSafe Jev (Decisions API): 1 request = 3 noul (~centenas de ms).
# Uso restrito à classificação de schema (descoberta_site); o julgamento de
# notícias é do LLM-juiz (juiz_llm.py).
JEV_TIMEOUT_S = _int("JEV_TIMEOUT_S", 12)
# LLM-juiz: 1 resumo por notícia relevante (teto) + 1 juiz-termômetro em lote.
JUIZ_MAX_NOTICIAS = _int("JUIZ_MAX_NOTICIAS", 10)
JUIZ_RELEVANCIA_MIN = _int("JUIZ_RELEVANCIA_MIN", 30)  # |score| p/ relevante
JUIZ_RESUMO_MAX_TOKENS = _int("JUIZ_RESUMO_MAX_TOKENS", 300)
# Resumos concorrentes (rajada de 10 estourou 429 no fallback; 4 passa liso)
JUIZ_CONCORRENCIA = _int("JUIZ_CONCORRENCIA", 4)
UNSLOTH_BASE_URL = _get("UNSLOTH_BASE_URL", "http://127.0.0.1:8888/v1")
UNSLOTH_MODEL_NAME = _get("UNSLOTH_MODEL_NAME")
UNSLOTH_API_KEY = _get("UNSLOTH_API_KEY", "not-needed")
LAYA_THRESHOLD = _float("LAYA_THRESHOLD", 0.80)
LAYA_MODEL = _get("LAYA_MODEL", "auto")
LAYA_DEVICE = _get("LAYA_DEVICE", "")  # vazio = cpu (seguro); cuda|mps só com teste local
RELEVANCIA_MIN = _float("RELEVANCIA_MIN", 0.30)
INDICE_SCORE_MIN = _float("INDICE_SCORE_MIN", 1.0)
MAX_AFIRMACOES = _int("MAX_AFIRMACOES", 3)
MAX_EVIDENCIAS = _int("MAX_EVIDENCIAS", 5)
CACHE_SERPAPI_TTL = _int("CACHE_SERPAPI_TTL", 3600)
FAKE_MODEL_PATH = _get("FAKE_MODEL_PATH")  # vazio = mock explícito (RF08 até o modelo real)
# Safety / cost caps (não quebram bot+API: só degradam p/ parcial/pulada)
SERPAPI_DAILY_CAP = _int("SERPAPI_DAILY_CAP", 100)
DEEP_CRAWL_MAX_PAGES = _int("DEEP_CRAWL_MAX_PAGES", 3)
DEEP_CRAWL_TIMEOUT_S = _int("DEEP_CRAWL_TIMEOUT_S", 15)
DEEP_CRAWL_MAX_BYTES = _int("DEEP_CRAWL_MAX_BYTES", 500000)
API_RATE_LIMIT_PER_MIN = _int("API_RATE_LIMIT_PER_MIN", 30)
# Descoberta de catálogo (modo descoberta: tetos MENORES que o deep crawl)
DISCOVERY_MAX_SITES = _int("DISCOVERY_MAX_SITES", 2)
DISCOVERY_TIMEOUT_S = _int("DISCOVERY_TIMEOUT_S", 8)
DISCOVERY_MAX_BYTES = _int("DISCOVERY_MAX_BYTES", 200000)
