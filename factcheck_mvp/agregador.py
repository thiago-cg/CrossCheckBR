"""RF12/RNF02: redação neutra do relatório (escala, nunca binário).

Desde a fase 2 a DECISÃO mora em `decisao.decidir` (uma função pura; log-odds
simétrico; um voto por cluster). Este módulo guarda só o que é de linguagem:
frases proibidas, emoji por nível, perguntas-guia e o header. O antigo
`agregar` (soma ponderada de sinais heterogêneos, "cobertura ampla" = −0,5,
direção de selo por substring) foi removido.
"""
from __future__ import annotations

from typing import Dict, List

# Qualquer conclusão binária é proibida na saída (RF12). Varredura em testes.
FRASES_BINARIAS = (
    "é falso", "é falsa", "é verdade", "é verdadeiro", "é mentira",
    "fake news confirmada", "notícia falsa", "notícia verdadeira",
)
# Neutralidade (check #6): adjetivos/partidarismo nunca saem no relatório
EXPRESSOES_PROIBIDAS = FRASES_BINARIAS + (
    "com certeza", "sem dúvida", "absurdo", "ridículo",
    "esquerdalha", "bolsominion", "petista", "bolsonarista",
    "corrupto", "idiota", "chocante", "incrível",
)
EMOJI_PROPENSAO = {"baixa": "🟢", "media": "🟡", "alta": "🔴", "indeterminada": "⚪"}
# Linguagem para o usuário: sempre "propensão de ser fake news", nunca veredito.
TITULO_PROPENSAO = {
    "alta": "Alta propensão de ser fake news",
    "media": "Propensão média de ser fake news",
    "baixa": "Baixa propensão de ser fake news",
    "indeterminada": "Não foi possível estimar a propensão de ser fake news",
}

_PERGUNTAS_GUIA = [
    "Compare a data do fato com a data da publicação: o conteúdo é atual ou reciclado?",
    "Quem assina o conteúdo original? Há veículo conhecido por trás?",
    "A mesma informação aparece em mais de um veículo independente? Quais?",
]


def verificar_neutralidade(texto: str) -> List[str]:
    """Retorna expressões proibidas encontradas (vazio = neutro)."""
    base = (texto or "").lower()
    return [e for e in EXPRESSOES_PROIBIDAS if e in base]


def perguntas_guia() -> List[str]:
    return list(_PERGUNTAS_GUIA)


def titulo_propensao(propensao: str) -> str:
    return TITULO_PROPENSAO.get(propensao, TITULO_PROPENSAO["indeterminada"])


def gerar_header(propensao: str, why: str = "") -> Dict[str, str]:
    """Header legível em segundos (check #1). O `why` vem de `Decisao.why_1linha()`."""
    emoji = EMOJI_PROPENSAO.get(propensao, "⚪")
    return {"header": f"{emoji} {titulo_propensao(propensao)}", "why_1linha": why}
