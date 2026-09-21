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
    "sem numeração e sem comentários. Ignore saudações, apelos ('compartilhe', 'urgente') e "
    "opiniões puras. Máximo {n} afirmações. Se não houver afirmação factual, responda VAZIO."
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


def _via_llm(texto: str, max_n: int) -> List[Afirmacao]:
    import httpx

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
        return []
    linhas = [_limpa_linha(l) for l in bruto.splitlines()]
    linhas = [l for l in linhas if len(l) > 15]
    return [Afirmacao(texto=l[:500], indice=i) for i, l in enumerate(linhas[:max_n])]


def extrair_afirmacoes(texto: str, max_n: int = 0, usar_llm: bool = True) -> List[Afirmacao]:
    """Sempre retorna >= 1 afirmação (o próprio texto como fallback final)."""
    limite = max_n or config.MAX_AFIRMACOES
    if usar_llm:
        try:
            got = _via_llm(texto, limite)
            if got:
                return got
        except Exception:
            pass
    return _fallback(texto, limite)
