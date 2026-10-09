"""RF12/RNF02: redação neutra do relatório (escala, nunca binário).

Desde a fase 2 a DECISÃO mora em `decisao.decidir` (uma função pura; log-odds
simétrico; um voto por cluster). Este módulo guarda só o que é de linguagem:
frases proibidas, emoji por nível, perguntas-guia e o header. O antigo
`agregar` (soma ponderada de sinais heterogêneos, "cobertura ampla" = −0,5,
direção de selo por substring) foi removido.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from . import telemetria

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


# B1a/C1: raciocínio do avaliador por fonte. É texto livre do LLM, então sai SEMPRE atribuído ao
# avaliador automático, numa linha só. A varredura NÃO isenta essa linha: quem a protege é o render.
# Se o raciocínio traz expressão proibida (ou selo em formato atribuído), a linha é OMITIDA
# (`linha_raciocinio`): bot e web seguem a mesma regra que a varredura.
ROTULO_RACIOCINIO = "Avaliação automática:"


def texto_de_linha(texto: Any) -> str:
    """Achata em UMA linha: quebras (\\n, \\r, U+2028/2029, NEL) e espaços repetidos viram um espaço.
    Título, citação e raciocínio vindos de página entram assim, para não forjar linha própria."""
    return " ".join(str(texto or "").split())


def _normalizar_texto(texto: str) -> str:
    """Forma comparável pela varredura (I2): NFKC; qualquer espaço (NBSP, U+2028/2029, quebras)
    vira espaço; formatação e controle (zero-width, soft hyphen, BOM, bidi) somem; espaços se
    colapsam; minúsculas. Os acentos são mantidos (ver `_sem_acento`)."""
    s = unicodedata.normalize("NFKC", texto or "")
    s = re.sub(r"\s", " ", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cf", "Cc"))
    return re.sub(r"\s+", " ", s).strip().casefold()


def _sem_acento(texto: str) -> str:
    """Sem diacríticos: decompõe (NFD) e descarta as marcas combinantes."""
    return "".join(ch for ch in unicodedata.normalize("NFD", texto) if unicodedata.category(ch) != "Mn")


def _padrao_sem_acento(expressao: str) -> "re.Pattern[str]":
    """Expressão como palavras inteiras, sem acento: "e falso" casa "é falso" e "E falso", mas não
    "acidente falso" nem "que falso"."""
    palavras = [re.escape(_sem_acento(_normalizar_texto(p))) for p in expressao.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(palavras) + r"(?!\w)")


_PADROES_SEM_ACENTO = {e: _padrao_sem_acento(e) for e in EXPRESSOES_PROIBIDAS}


def _expressoes_achadas(base: str) -> List[str]:
    """Expressões de EXPRESSOES_PROIBIDAS em `base` (já normalizado). Com acento: como trecho (como
    antes). Sem acento: como palavras inteiras. Vale a união: nenhuma das duas formas escapa."""
    sem = _sem_acento(base)
    return [e for e in EXPRESSOES_PROIBIDAS
            if _normalizar_texto(e) in base or _PADROES_SEM_ACENTO[e].search(sem)]


def linha_raciocinio(raciocinio: Optional[str], limite: Optional[int] = None) -> str:
    """Linha atribuída: "🧠 Avaliação automática: <raciocinio>" ("" se vazio ou omitido).

    C1: se o raciocínio (normalizado, I2) traz expressão proibida ou selo em formato atribuído, a
    linha é OMITIDA e registra-se `telemetria.fallback("raciocinio", ...)`. Quebras viram espaço,
    para a linha continuar sendo uma só. `limite` corta só a EXIBIÇÃO, com "…"; o dado em `Fonte`
    nunca muda."""
    texto = texto_de_linha(raciocinio)
    if not texto:
        return ""
    base = _normalizar_texto(texto)
    achadas = _expressoes_achadas(base)
    if achadas or _SELO_ATRIBUIDO_RE.search(base):
        binaria = any(e in FRASES_BINARIAS for e in achadas)
        telemetria.fallback("raciocinio", "expressao binaria omitida" if binaria
                            else "expressao proibida omitida")
        return ""
    if limite and len(texto) > limite:
        texto = texto[: limite - 1].rstrip() + "…"
    return f"🧠 {ROTULO_RACIOCINIO} {texto}"


_DATA_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def data_publicacao_legivel(data_pub: Optional[str], precisao: Optional[str] = None) -> str:
    """Data de publicação para o usuário (bot e web), a partir da data NORMALIZADA (UTC−3, a
    mesma que a decisão usou). Precisão "ano" (placeholder de ano do extrator ou "há N meses")
    mostra só o ano; as demais, DD/MM/AAAA. Sem data ISO reconhecível devolve "": nunca o texto
    bruto (um "há 3 dias" não vira "publicada em há 3 dias")."""
    m = _DATA_ISO_RE.match(str(data_pub or "").strip())
    if not m:
        return ""
    ano, mes, dia = m.groups()
    return ano if precisao == "ano" else f"{dia}/{mes}/{ano}"


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

    E5: o selo atribuído ("Selo da Lupa: FALSO", com agência do catálogo) é citação, não veredito
    nosso, e é ignorado. Selo em outro formato (agência não cadastrada) é sinalizado com o trecho.
    C1: a linha do raciocínio NÃO é isenta; `linha_raciocinio` a omite quando traz expressão proibida.
    I2: o texto é normalizado (Unicode e sem acento) antes da busca (`_normalizar_texto`).
    """
    base = _normalizar_texto(texto)
    registradas = _agencias_checagem()

    def _corta(m: re.Match) -> str:
        from .catalogo import normalizar_nome
        if normalizar_nome(m.group(1)) in registradas:
            return " "
        return m.group(0)

    resto = _SELO_ATRIBUIDO_RE.sub(_corta, base)
    achadas = _expressoes_achadas(resto)
    achadas += [m.group(0).strip() for m in _SELO_ATRIBUIDO_RE.finditer(resto)]
    return list(dict.fromkeys(achadas))


def perguntas_guia() -> List[str]:
    """Perguntas para o usuário avaliar sozinho. A de data ("Compare a data…") é sempre a
    primeira: com desconto temporal ela já está no topo, então não há parâmetro para isso."""
    return list(_PERGUNTAS_GUIA)


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
