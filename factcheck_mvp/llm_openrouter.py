"""Camada OpenRouter (deepseek-v4-flash via StreamLake + gemma fallback) p/ geração.

Uso: afirmações + padrões. Nunca levanta sem fallback: caller cai p/ regex.
Chave só via env local (.env, nunca logada, nunca no recibo).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Tuple

from . import config

log = logging.getLogger("factcheck.llm_openrouter")
_URL = "https://openrouter.ai/api/v1/chat/completions"
# Jev/TypeSafe: endpoint Decisions (NÃO chat/completions — 400 se usar /v1/...).
_URL_DECISIONS = "https://openrouter.ai/api/alpha/decisions"
_uso_dia: defaultdict = defaultdict(int)
_dia_atual: str = date.today().isoformat()


def _hoje() -> str:
    global _dia_atual
    hoje = date.today().isoformat()
    if hoje != _dia_atual:
        _dia_atual = hoje
        _uso_dia.clear()
    return hoje


def _cap_estourado() -> bool:
    _hoje()
    return sum(_uso_dia.values()) >= (config.LLM_DAILY_CAP or 50)


def _provider() -> Dict[str, Any]:
    """Bloco provider p/ fixar inference (ex StreamLake fp8). Vazio = padrão OR."""
    ordem = [p.strip() for p in (config.OPENROUTER_PROVIDER_ORDER or "").split(",") if p.strip()]
    return {"order": ordem} if ordem else {}


def _post(modelo: str, messages: List[dict], max_tokens: int, timeout_s: int) -> str:
    import httpx

    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY ausente")
    payload: Dict[str, Any] = {"model": modelo, "messages": messages,
                               "temperature": 0.0, "max_tokens": max_tokens}
    prov = _provider()
    if prov:
        payload["provider"] = prov  # fallbacks p/ outros providers do MESMO modelo seguem ativos
    r = httpx.post(
        _URL,
        headers={
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://crosscheckbr.local",
            "X-Title": "CrossCheckBR-MVP",
        },
        json=payload,
        timeout=timeout_s,
    )
    r.raise_for_status()
    data = r.json()
    choices = (data or {}).get("choices", [])
    texto = ((choices[0] or {}).get("message", {}) or {}).get("content", "") if choices else ""
    texto = (texto or "").strip()
    if not texto:
        raise RuntimeError(f"resposta vazia do modelo {modelo}")
    uso = (data or {}).get("usage", {})
    # log seguro: modelo + uso, nunca chave/headers
    log.info("openrouter ok model=%s prompt_tokens=%s completion_tokens=%s",
             modelo, uso.get("prompt_tokens"), uso.get("completion_tokens"))
    return texto


def chat(messages: List[dict], max_tokens: int = 0, temperature: float = 0.0,
         timeout_s: int = 0) -> Tuple[str, str]:
    """Retorna (texto, modelo_usado). Tenta primary -> fallback. Respeita cap diário.

    Resposta vazia (200 com conteúdo vazio, comum no StreamLake) ganha 1 retry
    no MESMO modelo antes de cair p/ o próximo — evita queimar o fallback à toa.
    """
    if _cap_estourado():
        raise RuntimeError("teto LLM diário atingido")
    mt = max_tokens or config.OPENROUTER_MAX_TOKENS
    ts = timeout_s or config.OPENROUTER_TIMEOUT_S
    modelos = [m for m in (config.OPENROUTER_MODEL, config.OPENROUTER_FALLBACK_MODEL) if m]
    ultimo_erro: Exception | None = None
    for modelo in modelos:
        for tentativa in range(2):
            try:
                t0 = time.time()
                texto = _post(modelo, messages, mt, ts)
                _hoje()
                _uso_dia[modelo] += 1
                log.info("openrouter model=%s lat=%.1fs", modelo, time.time() - t0)
                return texto, modelo
            except Exception as e:  # tenta de novo 1x se vazio, senão próximo
                ultimo_erro = e
                if "resposta vazia" in str(e) and tentativa == 0:
                    log.info("openrouter resposta vazia model=%s: repetindo 1x", modelo)
                    continue
                break
        log.warning("openrouter falhou model=%s: %s", modelo, str(ultimo_erro)[:150])
    raise RuntimeError(f"openrouter primary+fallback falharam: {ultimo_erro}")


def decisions(model: str, state: Any, questions: Dict[str, Any],
              timeout_s: int = 0) -> Dict[str, Any]:
    """POST /api/alpha/decisions (Jev). Retorna JSON {model, answers, usage}.

    Mesmo cap diário do chat. Sem provider.order StreamLake (Jev é TypeSafe).
    Nunca envia a chave no log. Levanta em erro/HTTP não-2xx (caller tem fallback).
    """
    import httpx

    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY ausente")
    if _cap_estourado():
        raise RuntimeError("teto LLM diário atingido")
    payload: Dict[str, Any] = {"model": model, "state": state, "questions": questions}
    r = httpx.post(
        _URL_DECISIONS,
        headers={
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "HTTP-Referer": "https://crosscheckbr.local",
            "X-Title": "CrossCheckBR-MVP",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout_s or config.JEV_TIMEOUT_S,
    )
    if r.status_code >= 400:
        # corpo pode embutir validação; trunca p/ não vazar payload gigante
        raise RuntimeError(f"decisions HTTP {r.status_code}: {r.text[:300]}")
    data = r.json() or {}
    answers = data.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise RuntimeError("decisions sem answers")
    _hoje()
    _uso_dia[model] += 1
    uso = data.get("usage") or {}
    log.info("decisions ok model=%s resolved=%s in=%s out=%s",
             model, data.get("model"), uso.get("input_tokens"), uso.get("output_tokens"))
    return data
