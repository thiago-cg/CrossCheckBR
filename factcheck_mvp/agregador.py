"""RF12/RNF02: redação neutra do relatório (escala, nunca binário).

Desde a fase 2 a DECISÃO mora em `decisao.decidir` (uma função pura; log-odds
simétrico; um voto por cluster). Este módulo guarda só o que é de linguagem:
frases proibidas, emoji por nível, perguntas-guia e o header. O antigo
`agregar` (soma ponderada de sinais heterogêneos, "cobertura ampla" = −0,5,
direção de selo por substring) foi removido.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

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


# E5 (opção b): selo sempre atribuído à agência ("Selo da Lupa: FALSO");
# a NOSSA conclusão continua em escala de propensão, nunca binária.
_SELO_ATRIBUIDO_RE = re.compile(
    r"selo\s+d[ao]\s+(?P<agencia>.+?)\s*:\s*(?P<selo>(?:(?!\bselo\s+d[ao]\b)[^.,;:])+)",
    re.IGNORECASE,
)

def formatar_selo(agencia: str, selo: str, artigo: str = "da") -> str:
    """Selo sempre atribuído à agência: "Selo da Lupa: FALSO"."""
    if artigo not in ("da", "do"):
        raise ValueError(f"artigo deve ser 'da' ou 'do', recebido: {artigo!r}")
    return f"Selo {artigo} {(agencia or '').strip()}: {(selo or '').strip()}"


def _agencias_checagem() -> frozenset:
    """Nomes normalizados das agências de checagem do catálogo (leitura fresca)."""
    nomes = set()
    try:
        from .catalogo import Catalogo, normalizar_nome
        for p in Catalogo.carregar().checagem():
            for n in [p.get("nome"), p.get("id")] + list(p.get("aliases_nome") or []):
                k = normalizar_nome(n)
                if k:
                    nomes.add(k)
    except Exception as e:
        from . import telemetria
        telemetria.fallback("agregador", f"catalogo indisponivel p/ selo atribuido: {e}")
    return frozenset(nomes)


def verificar_neutralidade(texto: str) -> List[str]:
    """Retorna expressões proibidas encontradas (vazio = neutro).

    E5: o selo atribuído ("Selo da Lupa: FALSO", com agência do catálogo) é
    citação, não veredito nosso — é ignorado. Selo em outro formato (agência
    não cadastrada) é sinalizado com o trecho correspondente.
    """
    base = texto or ""
    registradas = _agencias_checagem()

    def _corta(m: re.Match) -> str:
        from .catalogo import normalizar_nome
        if normalizar_nome(m.group(1)) in registradas:
            return " "
        return m.group(0)

    resto = _SELO_ATRIBUIDO_RE.sub(_corta, base)
    achadas = [e for e in EXPRESSOES_PROIBIDAS if e in resto.lower()]
    for m in _SELO_ATRIBUIDO_RE.finditer(resto):
        achadas.append(m.group(0).strip())
    return achadas


def perguntas_guia(priorizar_data: bool = False) -> List[str]:
    if not priorizar_data:
        return list(_PERGUNTAS_GUIA)
    # E4 Task 6: com desconto temporal, a pergunta de data vai para o topo.
    data_q = next((q for q in _PERGUNTAS_GUIA if q.startswith("Compare a data")), None)
    resto = [q for q in _PERGUNTAS_GUIA if q != data_q]
    return ([data_q] if data_q else []) + resto


def titulo_propensao(propensao: str) -> str:
    return TITULO_PROPENSAO.get(propensao, TITULO_PROPENSAO["indeterminada"])


def gerar_header(propensao: str, why: str = "") -> Dict[str, str]:
    """Header legível em segundos (check #1). O `why` vem de `Decisao.why_1linha()`."""
    emoji = EMOJI_PROPENSAO.get(propensao, "⚪")
    return {"header": f"{emoji} {titulo_propensao(propensao)}", "why_1linha": why}


# ------------------------------------------------- rótulos do bot e da web (mesma linguagem)
NOMES_ETAPAS = {
    "recebimento": "Leitura do texto",
    "afirmacoes": "Identificação das afirmações",
    "base-checagem": "Base de checagens",
    "descoberta-agente": "Planejamento da busca",
    "descoberta": "Busca de notícias",
    "deep-crawl": "Leitura das notícias",
    "juiz": "Análise das fontes",
    "corroboracao": "Comparação entre veículos",
    "agregacao": "Estimativa final",
    "agente-critico": "Revisão da busca",
    "descoberta-catalogo": "Novos sites no catálogo",
    "modelo": "Modelo automático",
    "padroes": "Padrões de desinformação",
}
STATUS_ETAPA = {"ok": "✅", "parcial": "⚠️", "pulada": "⏭️", "falha": "❌"}
_POSTURA_SEM_VOTO = {
    "RELATA_SEM_ENDOSSO": "relata o assunto sem tomar posição",
    "NAO_TRATA": "não trata do assunto",
    "SUSTENTA": "trata do assunto",   # votos anulados (conflito com selo/cluster)
    "REFUTA": "trata do assunto",
}


def direcoes_por_url(decisao: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """url -> direção do voto (+1 contesta o que o texto afirma, -1 confirma).

    Vem dos votos da decisão, que já aplicam a polaridade do usuário: se ele
    NEGA o núcleo, uma página que REFUTA o núcleo está do lado dele."""
    saida: Dict[str, float] = {}
    for v in (decisao or {}).get("votos", []) or []:
        for u in v.get("urls", []) or []:
            saida[u] = v.get("direcao", 0.0)
    return saida


def postura_legivel(fonte: Any, direcoes: Dict[str, float]) -> str:
    """O que a fonte faz frente ao que o usuário enviou, em linguagem simples."""
    d = direcoes.get(getattr(fonte, "url", ""), 0.0)
    if d > 0:
        return "contesta o que o texto afirma"
    if d < 0:
        return "confirma o que o texto afirma"
    return _POSTURA_SEM_VOTO.get(getattr(fonte, "postura", None) or "", "não avaliada")
