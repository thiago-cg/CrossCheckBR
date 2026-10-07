"""Avaliador 1:1 manchete+corpo (estágio antes do juiz-agregador).

Para cada peça relevante com corpo lido, monta 1 item
`{veiculo, dominio, tipo_fonte, data, titulo, veredito_pagina, trecho}` e o
julga com `juiz_llm.julgar_lote` de 1 item; remapeia `classe` → `posicao`.
Sem manchete+corpo (só snippet) não há julgamento: volta `posicao=None`
com `motor="fallback-sem-corpo"`, sem chamar o LLM.
`url/titulo/snippet/fonte/data` nunca saem do LLM.
"""
from __future__ import annotations

from typing import Any, Dict

from . import juiz_llm, telemetria
from .extracao import MIN_CHARS

MOTOR_SEM_CORPO = "fallback-sem-corpo"
ERRO_SEM_CORPO = "sem corpo lido"

_CAMPOS_CORPO = ("corpo", "texto_completo", "trecho_juiz")


def _corpo(peca: Dict[str, Any]) -> str:
    for campo in _CAMPOS_CORPO:
        texto = peca.get(campo) or ""
        if isinstance(texto, str) and texto.strip():
            return texto
    return ""


def avaliar(afirmacao_nucleo: str, peca: Dict[str, Any]) -> Dict[str, Any]:
    """Avalia 1 peça frente ao núcleo da afirmação.

    Entrada `peca`: `{url, titulo, corpo|texto_completo|trecho_juiz, snippet,
    veiculo, dominio, data_pub, veredito_pagina, corpo_lido}`.
    Saída: `{posicao, citacao, citacao_score, citacao_verificada, pagina_diz,
    motor, erro, corpo_lido}`. Nunca levanta.
    """
    peca = peca or {}
    titulo = (peca.get("titulo") or "").strip()
    corpo = _corpo(peca)
    tem_corpo = bool(peca.get("corpo_lido")) and len(corpo.strip()) >= MIN_CHARS
    if not titulo or not tem_corpo:
        telemetria.fallback("avaliador", ERRO_SEM_CORPO)
        return {"posicao": None, "citacao": "", "citacao_score": None,
                "citacao_verificada": None, "pagina_diz": "",
                "motor": MOTOR_SEM_CORPO, "erro": ERRO_SEM_CORPO,
                "corpo_lido": False}
    nucleo = (afirmacao_nucleo or "").strip()
    item: Dict[str, Any] = {
        "veiculo": peca.get("veiculo") or "",
        "dominio": peca.get("dominio") or "",
        "tipo_fonte": peca.get("tipo_fonte") or "",
        "data": peca.get("data_pub") or peca.get("data") or "",
        "titulo": titulo,
        "trecho": juiz_llm.montar_trecho(titulo, corpo, nucleo),
    }
    if peca.get("veredito_pagina"):
        item["veredito_pagina"] = peca["veredito_pagina"]
    elif peca.get("veredito") or peca.get("selo_original"):
        item["veredito_pagina"] = {
            "selo": peca.get("selo_original") or peca.get("veredito"),
            "alegacao_checada": peca.get("afirmacao_checada") or "",
        }
    r = juiz_llm.julgar_lote(nucleo, [item])[0]
    return {"posicao": r.get("classe"), "citacao": r.get("citacao") or "",
            "citacao_score": r.get("citacao_score"),
            "citacao_verificada": r.get("citacao_verificada"),
            "pagina_diz": r.get("pagina_diz") or "",
            "motor": r.get("motor") or "", "erro": r.get("erro"),
            "corpo_lido": True}
