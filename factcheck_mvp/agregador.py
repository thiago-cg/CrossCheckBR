"""RF12/RNF02: agregador de propensão — escala, nunca binário, linguagem neutra.

Entradas: sinais ponderados. Saída: baixa|media|alta|indeterminada + justificativa
que lista indícios nos dois sentidos. Redação 100% por templates (determinística):
LLM redigindo a conclusão poderia enviesar o usuário.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .schemas import SinalAnalise

# Pesos por motor (somam ~1; ajuste fino com dados rotulados futuros).
PESOS = {
    "veredito-existente": 0.40,
    "corroboracao": 0.25,
    "modelo-fake": 0.20,
    "llm-padroes": 0.10,
    "laya": 0.05,
    "llm-juiz": 0.05,
}

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


def verificar_neutralidade(texto: str) -> List[str]:
    """Retorna expressões proibidas encontradas (vazio = neutro)."""
    base = (texto or "").lower()
    return [e for e in EXPRESSOES_PROIBIDAS if e in base]
EMOJI_PROPENSAO = {"baixa": "🟢", "media": "🟡", "alta": "🔴", "indeterminada": "⚪"}

_PERGUNTAS_GUIA = [
    "Compare a data do fato com a data da publicação: o conteúdo é atual ou reciclado?",
    "Quem assina o conteúdo original? Há veículo conhecido por trás?",
    "A mesma informação aparece em mais de um veículo independente? Quais?",
]


def _direcao(sinal: SinalAnalise) -> float:
    """+1 aumenta propensão a desinformação, -1 reduz, 0 neutro.

    Regra-mestra: o teste de implicação domina o selo. Um selo VERDADEIRO numa
    peça que REFUTA a afirmação indica afirmação provavelmente falsa (+1).
    Lê rótulo+valor: sinal "cobertura ampla" com valor neutro antes pesava 0
    (recibo desonesto — exibia 0.75 que não contava).
    """
    v = f"{sinal.rotulo or ''} {sinal.valor or ''}".lower()
    if "refutada" in v or "refutam" in v or "contesta" in v:
        return 1.0
    if "sustentada" in v or "sustentam" in v or "corroborada" in v:
        return -1.0
    if sinal.motor == "veredito-existente":
        return 1.0 if any(k in v for k in ("fake", "falso", "enganoso")) else (-1.0 if any(k in v for k in ("verdadeiro", "fato")) else 0.0)
    if sinal.motor == "corroboracao":
        if "convergem" in v or "ampla" in v:  # convergência herda o sentido do veredito
            if any(k in v for k in ("falso", "fake", "enganoso")):
                return 1.0
            if any(k in v for k in ("verdadeiro", "fato")):
                return -1.0
            return -0.5  # convergem sem veredito explícito: cobertura ampla reduz propensão
        if "ausente" in v or "isolada" in v:
            return 1.0
        if "discordam" in v:
            return 0.5  # vereditos conflitantes: eleva incerteza a favor da cautela
        return 0.0
    if sinal.motor == "modelo-fake":
        try:
            p = float((sinal.valor or "").strip())
            return 1.0 if p >= 0.6 else (-1.0 if p <= 0.4 else 0.0)
        except ValueError:
            return 0.0
    if sinal.motor == "llm-padroes":
        return 1.0 if "padrao" in v or "encontrado" in v else 0.0
    if sinal.motor in ("laya", "llm-juiz"):
        return 1.0 if "selo" in v or "falso" in v else 0.0
    return 0.0


def agregar(sinais: List[SinalAnalise]) -> Dict[str, Any]:
    if not sinais:
        return {"propensao": "indeterminada",
                "justificativa": "Não encontrei elementos suficientes para avaliar a propensão. Vale buscar a informação nos veículos e agências de checagem listados nas fontes.",
                "score": 0.0}
    num = den = 0.0
    for s in sinais:
        peso = PESOS.get(s.motor, 0.05) * (s.confianca if s.confianca is not None else 0.7)
        num += peso * _direcao(s)
        den += peso
    score = num / den if den else 0.0  # -1 (confiável) .. +1 (propenso)
    if abs(score) < 0.15 and den < 0.5:
        prop = "indeterminada"
    elif score < 0.35:
        prop = "baixa"
    elif score <= 0.65:
        prop = "media"
    else:
        prop = "alta"
    aumenta = [f"{s.rotulo} ({s.motor})" for s in sinais if _direcao(s) > 0]
    reduz = [f"{s.rotulo} ({s.motor})" for s in sinais if _direcao(s) < 0]
    partes = [f"Propensão {prop} a se tratar de desinformação."]
    if aumenta:
        partes.append("Aumentam a propensão: " + "; ".join(aumenta) + ".")
    if reduz:
        partes.append("Reduzem a propensão: " + "; ".join(reduz) + ".")
    partes.append("Isso não é um veredito: compare as fontes abaixo e tire sua própria conclusão.")
    return {"propensao": prop, "justificativa": " ".join(partes), "score": round(score, 3)}


def perguntas_guia() -> List[str]:
    return list(_PERGUNTAS_GUIA)


def gerar_header(propensao: str, sinais: List[SinalAnalise], n_fontes: int = 0,
                 n_corpo_lido: int = 0) -> Dict[str, str]:
    """Header legível em segundos (check #1). Neutro, sem binário."""
    emoji = EMOJI_PROPENSAO.get(propensao, "⚪")
    n_ref = sum(1 for s in sinais if _direcao(s) > 0)
    n_sus = sum(1 for s in sinais if _direcao(s) < 0)
    if propensao == "indeterminada":
        why = (f"{n_fontes} fonte(s) consultada(s); elementos insuficientes "
               f"para avaliar — compare as fontes abaixo.")
    else:
        why = (f"{n_ref} indício(s) elevam a propensão, {n_sus} a reduzem; "
               f"{n_fontes} fonte(s), {n_corpo_lido} com corpo lido.")
    header = f"{emoji} Propensão {propensao.upper()} a se tratar de desinformação"
    return {"header": header, "why_1linha": why}
