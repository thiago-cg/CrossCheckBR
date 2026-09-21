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
