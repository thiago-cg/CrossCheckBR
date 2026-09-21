"""RF10: padrões de desinformação no texto.

Preferência laya não se aplica (é geração de lista explicada) -> LLM local,
com fallback DETERMINÍSTICO por regex (funciona até sem modelo).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from . import config

_CATALOGO = [
    ("apelo_urgencia", "apelo a urgência/compartilhamento",
     re.compile(r"\b(urgente|compartilhe|repasse já|não deixe de compartilhar|encaminhe)\b", re.I)),
    ("tom_alarmista", "tom alarmista (caixa alta / exclamações em excesso)",
     re.compile(r"[A-ZÁÉÍÓÚÇ ]{20,}|[!?]{3,}")),
    ("fonte_vaga", "fonte vaga ou ausente ('dizem', 'vazou', sem autoria)",
     re.compile(r"\b(dizem por aí|vazou|fontes anônimas|não querem que você saiba)\b", re.I)),
    ("sem_data_local", "sem data ou local verificável no texto",
     re.compile(r"^(?!.*\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b)(?!.*\b(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b).*$", re.I | re.S)),
    ("clickbait", "título sensacionalista desconectado do corpo",
     re.compile(r"\b(você não vai acreditar|chocante|bomba|incrível)\b", re.I)),
]

_INSTRUCAO = (
    "Liste padrões típicos de desinformação presentes no texto, um por linha no formato "
    "'PADRAO: trecho curto que evidencia'. Use só estes rótulos: {rotulos}. "
    "Se nenhum, responda NENHUM. Sem explicações extras."
)


def _fallback(texto: str) -> List[Dict[str, str]]:
    achados = []
    for chave, descricao, rx in _CATALOGO:
        if chave == "sem_data_local" and len(texto or "") < 300:
            continue  # em texto curto, ausência de data não é sinal
        m = rx.search(texto or "")
        if m:
            achados.append({"padrao": chave, "descricao": descricao,
                            "evidencia": (m.group(0) or "")[:120]})
    return achados


def _via_llm(texto: str) -> List[Dict[str, str]]:
    import httpx

    modelo = config.UNSLOTH_MODEL_NAME
    if not modelo:
        r = httpx.get(config.UNSLOTH_BASE_URL.rstrip("/") + "/models", timeout=15)
        r.raise_for_status()
        itens = r.json().get("data", [])
        modelo = itens[0]["id"] if itens else ""
        if not modelo:
            raise RuntimeError("sem modelo local")
    rotulos = ",".join(c[0] for c in _CATALOGO)
    payload = {"model": modelo,
               "messages": [{"role": "user",
                             "content": _INSTRUCAO.format(rotulos=rotulos)
                             + '\n\nTEXTO:\n"""\n' + (texto or "")[:4000] + '\n"""'}],
               "temperature": 0.0, "max_tokens": 500}
    r = httpx.post(config.UNSLOTH_BASE_URL.rstrip("/") + "/chat/completions", json=payload, timeout=60)
    r.raise_for_status()
    bruto = (r.json()["choices"][0]["message"]["content"] or "").strip()
    if bruto.upper() == "NENHUM":
        return []
    saida = []
    descs = {c[0]: c[1] for c in _CATALOGO}
    for linha in bruto.splitlines():
        if ":" not in linha:
            continue
        chave, evid = linha.split(":", 1)
        chave = chave.strip("- *\t").lower()
        if chave in descs:
            saida.append({"padrao": chave, "descricao": descs[chave], "evidencia": evid.strip()[:160]})
    return saida


def analisar_padroes(texto: str, usar_llm: bool = True) -> Dict[str, Any]:
    """Retorna {padroes[], motor}. Fallback regex nunca falha."""
    if usar_llm:
        try:
            achados = _via_llm(texto)
            return {"padroes": achados, "motor": "llm-local"}
        except Exception:
            pass
    return {"padroes": _fallback(texto), "motor": "regex-deterministico"}
