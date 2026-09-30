"""Implicação de UMA fonte frente a uma afirmação (atalho sobre o juiz de 4 classes).

O pipeline julga em lote (`juiz_llm.julgar`); esta função existe para uso avulso
(scripts, testes). Sem LLM, NÃO há fallback léxico: sobreposição de palavras mede
tema, não postura (achado C3 da revisão) — o item volta sem classe
(`motor="fallback-sem-juiz"`), e a decisão o ignora.
"""
from __future__ import annotations

from typing import Any, Dict

from . import juiz_llm


def implicacao(texto_fonte: str, afirmacao: str, titulo: str = "") -> Dict[str, Any]:
    """Retorna {classe, relevante, citacao, citacao_verificada, motor, erro}. Nunca levanta."""
    afirmacao = (afirmacao or "").strip()[:500]
    corpo = (texto_fonte or "").strip()
    if len(afirmacao) < 5 or len(corpo) < 20:
        return {"classe": None, "relevante": None, "citacao": "", "citacao_verificada": None,
                "motor": juiz_llm.MOTOR_FALLBACK, "erro": "texto insuficiente"}
    trecho = juiz_llm.montar_trecho(titulo, corpo, afirmacao)
    r = juiz_llm.julgar_lote(afirmacao, [{"titulo": titulo, "trecho": trecho}])[0]
    r["relevante"] = juiz_llm.postura_para_relevante(r.get("classe"))
    return r
