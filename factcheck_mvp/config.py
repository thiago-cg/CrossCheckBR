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
OPENROUTER_MODEL_JUIZ = _get("OPENROUTER_MODEL_JUIZ")  # vazio = mesmo modelo dos demais passos
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
# Juiz de 4 classes (fase 2): itens por chamada, tamanho do trecho, tokens de saída
# (o modelo local é de raciocínio: reserve folga) e limiar da citação verificada.
JUIZ_LOTE = _int("JUIZ_LOTE", 5)
JUIZ_TRECHO_MAX = _int("JUIZ_TRECHO_MAX", 2500)
JUIZ_MAX_TOKENS = _int("JUIZ_MAX_TOKENS", 4000)
JUIZ_CITACAO_MIN = _float("JUIZ_CITACAO_MIN", 0.9)
JUIZ_TIMEOUT_TOTAL_S = _int("JUIZ_TIMEOUT_TOTAL_S", 600)
# Lotes do juiz em paralelo (o Studio local serve 1 slot bem; OpenRouter aguenta mais)
JUIZ_CONCORRENCIA = _int("JUIZ_CONCORRENCIA", 1)
# Timeout de 1 chamada LLM (local de raciocínio + prompt de ~5k tokens é lento)
LLM_TIMEOUT_S = _int("LLM_TIMEOUT_S", 240)
# LLM local de raciocínio: 0 (padrão) pula o <think> via prefill; 1 deixa raciocinar (lento)
LLM_LOCAL_RACIOCINIO = _get("LLM_LOCAL_RACIOCINIO", "0").strip().lower() in ("1", "true", "sim", "on")
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
# Modelo real só opina em texto longo: em manchete solta o BERTimbau v6 erra
# por formato (curto → fake). Confiança baixa: é um sinal a mais, não o principal.
FAKE_MODEL_MIN_PALAVRAS = _int("FAKE_MODEL_MIN_PALAVRAS", 80)
FAKE_MODEL_CONFIANCA = _float("FAKE_MODEL_CONFIANCA", 0.5)
# Cache de relatórios completos (mesmo texto repetido não refaz busca/LLM).
RESULT_CACHE_TTL = _int("RESULT_CACHE_TTL", 1800)
RESULT_CACHE_MAX = _int("RESULT_CACHE_MAX", 256)
# Feedback 👍/👎 do bot (JSONL local; vira dado rotulado p/ calibrar pesos).
FEEDBACK_PATH = _get("FEEDBACK_PATH", "")
# Safety / cost caps (não quebram bot+API: só degradam p/ parcial/pulada)
SERPAPI_DAILY_CAP = _int("SERPAPI_DAILY_CAP", 100)
# Estratégia no orgânico: `agente` (padrão: onda 1 determinística → extração → juiz →
# crítico pós-juiz com onda extra reescrita por LLM; ver agente.py), `simples` (crua +
# dirigida leve), `avancada` (crua + site: das agências do catálogo) ou `ambas` (união).
# Motor news legado via SERP_ENGINE=google_news.
SERP_ENGINE = _get("SERP_ENGINE", "google")
SERP_ESTRATEGIA = _get("SERP_ESTRATEGIA", "agente")
# Teto de buscas SerpAPI do agente POR REQUISIÇÃO (onda 1 + ondas extras). Padrão 9 =
# 3 afirmações x (2 da onda 1 + 1 extra). A onda 1 reserva n_afirmações x AGENTE_MAX_ONDAS_EXTRAS.
AGENTE_MAX_BUSCAS = _int("AGENTE_MAX_BUSCAS", 9)
# Ondas extras por afirmação (o crítico pós-juiz decide se gasta; 0 = só onda 1).
AGENTE_MAX_ONDAS_EXTRAS = _int("AGENTE_MAX_ONDAS_EXTRAS", 1)
# Teto de tempo de UMA onda do agente (o que voltou antes do teto é preservado).
AGENTE_TIMEOUT_S = _int("AGENTE_TIMEOUT_S", 60)
# Corte de URLs por afirmação (round-robin preservado; a seleção p/ o juiz vem depois).
AGENTE_MAX_POR_AFIRMACAO = _int("AGENTE_MAX_POR_AFIRMACAO", 12)
# Hosts extras de agências de checagem além das derivadas do catálogo (vírgula).
SERP_SITES_EXTRAS = _get("SERP_SITES_EXTRAS", "")
DEEP_CRAWL_MAX_PAGES = _int("DEEP_CRAWL_MAX_PAGES", 3)
# Teto total de páginas lidas por consulta (todas as afirmações) antes do juiz
DEEP_CRAWL_TOTAL = _int("DEEP_CRAWL_TOTAL", 10)
# Confiabilidade (confiabilidade.py): fora do catálogo, "site muito acessado" = entre os
# N domínios mais acessados da lista Tranco (data/trafego_tranco.csv.gz guarda até 200 mil).
TRAFEGO_RANK_MAX = _int("TRAFEGO_RANK_MAX", 200_000)
DEEP_CRAWL_TIMEOUT_S = _int("DEEP_CRAWL_TIMEOUT_S", 15)
DEEP_CRAWL_MAX_BYTES = _int("DEEP_CRAWL_MAX_BYTES", 5_000_000)
API_RATE_LIMIT_PER_MIN = _int("API_RATE_LIMIT_PER_MIN", 30)
# Descoberta de catálogo (modo descoberta: tetos MENORES que o deep crawl)
DISCOVERY_MAX_SITES = _int("DISCOVERY_MAX_SITES", 2)
DISCOVERY_TIMEOUT_S = _int("DISCOVERY_TIMEOUT_S", 8)
DISCOVERY_MAX_BYTES = _int("DISCOVERY_MAX_BYTES", 5_000_000)
# Cascata de extração: ReaderLM-v2 como penúltimo nível (desligado por padrão; servidor próprio)
EXTRACAO_READERLM = _get("EXTRACAO_READERLM", "0")
READERLM_BASE_URL = _get("READERLM_BASE_URL", "http://127.0.0.1:8889/v1")
