"""RF10: padrões de linguagem no texto — SINAL DE ESTILO, fora do nível.

Desde a fase 2 os padrões não entram na decisão (não indicam veracidade: "É falso
que X" e "X" têm o mesmo estilo). Aparecem no relatório como "sinais de estilo do
texto (não indicam veracidade)".

Preferência laya não se aplica (é geração de lista explicada) -> LLM local,
com fallback DETERMINÍSTICO por regex (funciona até sem modelo).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from . import llm, telemetria

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


def _parse(bruto: str) -> List[Dict[str, str]]:
    """Linhas 'PADRAO: trecho' -> lista. 'NENHUM' (com ou sem pontuação) = lista vazia válida."""
    bruto = (bruto or "").strip()
    if re.sub(r"[^a-z]", "", bruto.lower()) in ("nenhum", "nenhuma", "none"):
        return []
    saida = []
    descs = {c[0]: c[1] for c in _CATALOGO}
    for linha in bruto.splitlines():
        if ":" not in linha:
            continue
        chave, evid = linha.split(":", 1)
        chave = chave.strip("- *\t`").lower()
        if chave in descs:
            saida.append({"padrao": chave, "descricao": descs[chave], "evidencia": evid.strip()[:160]})
    if not saida and bruto:
        raise RuntimeError("resposta sem padrões parseáveis")
    return saida


def _via_llm(texto: str) -> tuple[List[Dict[str, str]], str]:
    """Retorna (padroes, motor real usado) — sempre uma TUPLA (bug C5: `return []`)."""
    rotulos = ",".join(c[0] for c in _CATALOGO)
    res = llm.chat_texto(
        [{"role": "user", "content": _INSTRUCAO.format(rotulos=rotulos)
          + '\n\nTEXTO:\n"""\n' + (texto or "")[:4000] + '\n"""'}],
        finalidade="padroes", max_tokens=1500)
    if not res.ok:
        raise RuntimeError(res.erro or "LLM indisponível")
    return _parse(res.texto), f"{res.motor}:{res.modelo}"


def analisar_padroes(texto: str, usar_llm: bool = True) -> Dict[str, Any]:
    """Retorna {padroes[], motor}. Fallback regex nunca falha."""
    if usar_llm:
        try:
            achados, motor = _via_llm(texto)
            return {"padroes": achados, "motor": motor}
        except Exception as e:
            telemetria.fallback("padroes", f"{type(e).__name__}: {e}")
    return {"padroes": _fallback(texto), "motor": "fallback-regex"}
