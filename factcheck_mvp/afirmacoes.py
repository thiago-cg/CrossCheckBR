"""Quebra do input em afirmações atômicas verificáveis.

laya NÃO gera texto, então aqui o LLM local é inevitável (preferência laya
não se aplica a geração). Fallback 100% determinístico: splitter de sentenças.
"""
from __future__ import annotations

import re
from typing import List

from . import config
from .schemas import Afirmacao

_INSTRUCAO = (
    "Divida o texto abaixo em afirmações factuais atômicas e verificáveis, uma por linha, "
    "sem numeração e sem comentários. Perguntas como 'É verdade que X?' ou 'X vai acontecer?' "
    "CONTÉM a afirmação X — extraia X (sem a moldura da pergunta). Frases curtas factuais "
    "(ex: 'Vacina causa autismo') também contam. Ignore saudações, apelos ('compartilhe', "
    "'urgente') e opiniões puras. Máximo {n} afirmações. Se não houver NENHUMA afirmação "
    "factual no texto, responda VAZIO."
)


def _limpa_linha(linha: str) -> str:
    return re.sub(r"^[\-\*\d\.\s]+", "", linha).strip()


def _fallback(texto: str, max_n: int) -> List[Afirmacao]:
    partes = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", texto) if p and len(p.strip()) > 25]
    ruins = re.compile(r"compartilhe|urgente|encaminhad|bom dia|boa (tarde|noite)|inscreva-se", re.I)
    limpas = []
    for p in partes:
        residuo = re.sub(r"[.!?]+$", "", ruins.sub("", p)).strip(" ,;:\t")
        if len(residuo) > 25:  # mantém o núcleo factual, descarta só o apelo
            limpas.append(residuo)
        if len(limpas) >= max_n:
            break
    if not limpas and partes:
        return []  # só saudação/apelo: sem afirmação factual -> caminho "indeterminada"
    return [Afirmacao(texto=p, indice=i) for i, p in enumerate(limpas)] or [Afirmacao(texto=texto[:500], indice=0)]


def _via_llm(texto: str, max_n: int) -> tuple[List[Afirmacao], str]:
    import httpx

    # 1) OpenRouter (deepseek-v4-flash via StreamLake + gemma fallback) quando há chave
    try:
        from . import llm_openrouter
        from . import config as _cfg

        if _cfg.OPENROUTER_API_KEY:
            conteudo, modelo_real = llm_openrouter.chat(
                [{"role": "user",
                  "content": _INSTRUCAO.format(n=max_n) + '\n\nTEXTO:\n"""\n' + texto[:4000] + '\n"""'}],
                max_tokens=600)
            if conteudo.strip().upper() == "VAZIO":
                return [], f"openrouter:{modelo_real}"
            linhas = [_limpa_linha(l) for l in conteudo.splitlines()]
            linhas = [l for l in linhas if len(l) > 15]
            got = [Afirmacao(texto=l[:500], indice=i) for i, l in enumerate(linhas[:max_n])]
            # Resposta recebida (mesmo filtrada): não cai no LLM local (timeout 15s).
            # VAZIO/filtragem vazio é tratado pelo caller com regex-fallback.
            return got, f"openrouter:{modelo_real}"
    except Exception:
        pass
    if not config.UNSLOTH_MODEL_NAME:
        r = httpx.get(config.UNSLOTH_BASE_URL.rstrip("/") + "/models", timeout=15)
        r.raise_for_status()
        itens = r.json().get("data", [])
        modelo = itens[0]["id"] if itens else ""
        if not modelo:
            raise RuntimeError("sem modelo local")
    else:
        modelo = config.UNSLOTH_MODEL_NAME
    payload = {
        "model": modelo,
        "messages": [{"role": "user",
                      # Delimitador: o texto do usuário nunca vira instrução.
                      "content": _INSTRUCAO.format(n=max_n) + '\n\nTEXTO:\n"""\n' + texto[:4000] + '\n"""'}],
        "temperature": 0.0,
        "max_tokens": 600,
    }
    r = httpx.post(config.UNSLOTH_BASE_URL.rstrip("/") + "/chat/completions", json=payload, timeout=60)
    r.raise_for_status()
    bruto = r.json()["choices"][0]["message"]["content"] or ""
    if bruto.strip().upper() == "VAZIO":
        return [], f"llm-local:{modelo}"
    linhas = [_limpa_linha(l) for l in bruto.splitlines()]
    linhas = [l for l in linhas if len(l) > 15]
    return [Afirmacao(texto=l[:500], indice=i) for i, l in enumerate(linhas[:max_n])], f"llm-local:{modelo}"


def extrair_afirmacoes(texto: str, max_n: int = 0, usar_llm: bool = True) -> tuple[List[Afirmacao], str]:
    """Retorna (afirmacoes, motor). Motor honesto, sem global (thread-safe)."""
    limite = max_n or config.MAX_AFIRMACOES
    if usar_llm:
        try:
            got, motor = _via_llm(texto, limite)
            if got:
                return got, motor
            # VAZIO do OpenRouter NÃO é confiável na prática (deepseek devolve VAZIO
            # para perguntas/afirmações curtas legítimas -> SerpAPI nunca consultada).
            # Sempre tenta o regex antes de desistir; vazio real só se os dois falharem.
            fb = _fallback(texto, limite)
            if fb:
                return fb, "regex-fallback-apos-llm-vazio"
            return [], motor
        except Exception:
            pass
    return _fallback(texto, limite), "regex-deterministico"
