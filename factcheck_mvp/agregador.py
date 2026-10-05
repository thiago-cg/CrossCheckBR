"""RF12/RNF02: agregador de propensão — escala, nunca binário, linguagem neutra.

Entradas: sinais ponderados. Saída: baixa|media|alta|indeterminada + justificativa
que lista indícios nos dois sentidos. Redação 100% por templates (determinística):
LLM redigindo a conclusão poderia enviesar o usuário.
"""
from __future__ import annotations

import re
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
# Faixas do score (-1 confiável .. +1 propenso a desinformação)
LIMIAR_ALTA = 0.6
LIMIAR_MEDIA = 0.25
LIMIAR_BAIXA = -0.25
# Comunicação com o usuário: sempre "propensão", nunca veredito.
TITULO_PROPENSAO = {
    "alta": "Alta propensão de ser fake news",
    "media": "Propensão média de ser fake news",
    "baixa": "Baixa propensão de ser fake news",
    "indeterminada": "Não foi possível estimar a propensão de ser fake news",
}
AVISO_NAO_VEREDITO = ("Isto é uma estimativa, não um veredito: confira as fontes "
                      "e tire sua própria conclusão.")
_NOMES_PADROES = {
    "apelo_urgencia": "apelo para compartilhar com urgência",
    "tom_alarmista": "tom alarmista",
    "fonte_vaga": "fonte vaga ou ausente",
    "sem_data_local": "sem data ou local verificável",
    "clickbait": "título sensacionalista",
}


def frase_propensao(propensao: str) -> str:
    return TITULO_PROPENSAO.get(propensao, TITULO_PROPENSAO["indeterminada"]) + "."


def explicar(sinal: SinalAnalise) -> str:
    """Sinal -> frase em linguagem simples (sem nome interno de motor)."""
    v = f"{sinal.rotulo or ''} {sinal.valor or ''}".lower()
    if sinal.motor == "corroboracao":
        n = re.match(r"\d+", (sinal.valor or "").strip())
        n = n.group(0) if n else "Alguns"
        if "refutam" in v or "contesta" in v:
            return f"{n} veículo(s) contestam o conteúdo"
        if "sustentam" in v:
            return f"{n} veículo(s) noticiam o conteúdo como fato"
        if "ampla" in v:
            return "o assunto aparece em 3 ou mais veículos independentes"
        if "isolada" in v or "ausente" in v:
            return "o assunto não aparece em veículos confiáveis consultados"
        if "discordam" in v:
            return "agências de checagem discordam entre si"
        if "convergem" in v:
            return "agências de checagem chegaram à mesma conclusão"
    if sinal.motor == "veredito-existente":
        return f"agência de checagem já analisou o tema ({sinal.rotulo.split(': ', 1)[-1]})"
    if sinal.motor == "llm-padroes":
        nomes = (sinal.valor or "").split(":", 1)[-1]
        legiveis = [_NOMES_PADROES.get(n.strip(), n.strip()) for n in nomes.split(",") if n.strip()]
        return "o texto tem padrões comuns em desinformação (" + ", ".join(legiveis) + ")"
    if sinal.motor == "modelo-fake":
        return "modelo automático de detecção (ainda em teste)"
    if sinal.motor == "laya":
        return "classificador automático confirma o selo da agência"
    return sinal.rotulo or sinal.motor

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
    # Faixas simétricas: "baixa" exige indícios a favor do conteúdo (score
    # negativo). Antes, qualquer score < 0.35 era "baixa" — evidência mista
    # (3 veículos contestam x 1 sustenta) saía verde.
    if score >= LIMIAR_ALTA:
        prop = "alta"
    elif score >= LIMIAR_MEDIA:
        prop = "media"
    elif score <= LIMIAR_BAIXA:
        prop = "baixa"
    else:
        prop = "indeterminada"  # indícios fracos ou contraditórios
    return {"propensao": prop, "justificativa": montar_justificativa(prop, sinais),
            "score": round(score, 3)}


def montar_justificativa(propensao: str, sinais: List[SinalAnalise], extra: str = "") -> str:
    """Frase de propensão + porquês nos dois sentidos + aviso. Chamada de novo
    pelo pipeline quando a propensão final muda (evita "alta" no texto e
    "indeterminada" no título)."""
    aumenta = [explicar(s) for s in sinais if _direcao(s) > 0]
    reduz = [explicar(s) for s in sinais if _direcao(s) < 0]
    partes = [frase_propensao(propensao)]
    if aumenta:
        partes.append("Indícios que aumentam a propensão: " + "; ".join(aumenta) + ".")
    if reduz:
        partes.append("Indícios que reduzem a propensão: " + "; ".join(reduz) + ".")
    if extra:
        partes.append(extra.strip())
    partes.append(AVISO_NAO_VEREDITO)
    return " ".join(partes)


def perguntas_guia() -> List[str]:
    return list(_PERGUNTAS_GUIA)


def gerar_header(propensao: str, sinais: List[SinalAnalise], n_fontes: int = 0,
                 n_corpo_lido: int = 0, n_contestam: int = 0, n_confirmam: int = 0) -> Dict[str, str]:
    """Header legível em segundos (check #1). Neutro, sem binário."""
    emoji = EMOJI_PROPENSAO.get(propensao, "⚪")
    header = f"{emoji} {TITULO_PROPENSAO.get(propensao, TITULO_PROPENSAO['indeterminada'])}"
    if not n_fontes:
        why = ("Não encontramos notícias ou checagens sobre o assunto. "
               "Isso não confirma nem descarta o conteúdo: procure a fonte original.")
    else:
        partes = []
        if n_contestam:
            partes.append(f"{n_contestam} contestam o conteúdo")
        if n_confirmam:
            partes.append(f"{n_confirmam} o noticiam como fato")
        resto = n_fontes - n_contestam - n_confirmam
        if resto > 0:
            partes.append(f"{resto} tratam do assunto sem tomar posição")
        why = f"Analisamos {n_fontes} fonte(s) sobre o assunto: " + ", ".join(partes) + "."
    return {"header": header, "why_1linha": why}


# Rótulos compartilhados pelo bot e pela web (mesma linguagem nos dois canais).
NOMES_ETAPAS = {
    "recebimento": "Leitura do texto",
    "afirmacoes": "Identificação das afirmações",
    "base-checagem": "Base de checagens",
    "descoberta": "Busca de notícias",
    "corroboracao": "Comparação entre veículos",
    "modelo": "Modelo automático",
    "padroes": "Padrões de desinformação",
    "agregacao": "Estimativa final",
}
STATUS_ETAPA = {"ok": "✅", "parcial": "⚠️", "pulada": "⏭️", "falha": "❌"}


def postura_fonte(fonte: Any) -> str:
    """Como a fonte se posiciona frente ao conteúdo (nota do LLM-juiz)."""
    s = getattr(fonte, "score_juiz", None)
    if s is None:
        return "trata do assunto"
    if s <= -30:
        return "contesta o conteúdo"
    if s >= 30:
        return "noticia o conteúdo como fato"
    return "trata do assunto sem tomar posição"
