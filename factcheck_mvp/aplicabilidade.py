"""Gate de aplicabilidade da base (E1): decide se um match da base pode ser usado.

Módulo puro: sem I/O, sem LLM, sem rede.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from factcheck_mvp import config as _config
from factcheck_mvp.selos import direcao

# Classes do juiz que indicam que a checagem trata do fato alegado.
TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")

# Dia da semana normalizado (sem acento); "-feira"/" feira" opcionais. O lookahead exclui o
# ordinal ("esta segunda parte", "a segunda vez"), que não é dia da semana.
_SEMANA_DIA = (r"(?:domingo|segunda(?:-feira| feira)?|terca(?:-feira| feira)?|"
               r"quarta(?:-feira| feira)?|quinta(?:-feira| feira)?|"
               r"sexta(?:-feira| feira)?|sabado)"
               r"(?!\s+(?:vez|parte|etapa|fase|tentativa|edicao|rodada|turno|onda|dose|chance|metade|"
               r"temporada|versao|geracao)\b)")
_DETERMINANTE_SEMANA = r"(?:neste|nesta|nesse|nessa|este|esta|esse|essa)"

# Marcadores relativos do TEXTO (E4): (padrão sobre o texto normalizado — minúsculo, sem acento —,
# variável config.E4_JANELA_*, default em dias). Vale a menor janela achada. Cada um diz QUANDO o
# fato ocorreu em relação a quem escreve.
_MARCADORES = tuple(
    (re.compile(r"\b(?:" + padrao + r")\b"), variavel, default)
    for padrao, variavel, default in (
        (r"hoje|agora|acaba de|acabou de|ha pouco|ha instantes|ha poucas horas|agora ha pouco|"
         r"(?:esta|nesta) (?:manha|tarde|noite|madrugada)", "E4_JANELA_HOJE", 2),
        (r"ontem", "E4_JANELA_ONTEM", 3),
        (r"anteontem", "E4_JANELA_ANTEONTEM", 4),
        # semana corrente, fim de semana e dia da semana com determinante ("este domingo",
        # "esta segunda-feira"); "na última <dia>" é a última ocorrência desse dia (mesma janela)
        (_DETERMINANTE_SEMANA + r" semana|(?:neste|nesse|este|esse) (?:fim|final) de semana|"
         r"(?:na|no) ultim[oa] " + _SEMANA_DIA + r"|" + _DETERMINANTE_SEMANA + r" " + _SEMANA_DIA,
         "E4_JANELA_SEMANA", 8),
        # "na última semana" = "semana passada": o mesmo período (a semana anterior)
        (r"na ultima semana|semana passada", "E4_JANELA_SEMANA_PASSADA", 15),
        (r"(?:neste|nesse|este|esse) mes", "E4_JANELA_MES", 32),
    )
)
# Padrão -> default, por variável (o valor efetivo vem de config em `janelas_efetivas`).
_DEFAULTS_JANELA = {variavel: default for _, variavel, default in _MARCADORES}
# Limite (dias) do ano inferido de uma data sem ano (config.E4_MARCO_MAX_DIAS; ver marco_do_evento).
_MARCO_MAX_DIAS_PADRAO = 60

# Trechos que NÃO são marcador de momento ("hoje em dia", "até agora", "há pouco mais de dez
# anos" = quantidade, "nos dias de hoje" = mesma classe de "hoje em dia"): apagados antes da
# busca para o "hoje"/"agora"/"há pouco" dentro deles não contar.
_EXCLUSOES = tuple(re.compile(padrao) for padrao in (
    r"\bhoje\s+em\s+dia\b",
    r"\bnos\s+dias\s+de\s+hoje\b",
    r"\bate\s+hoje\b",
    r"\bate\s+agora\b",
    r"\ba\s+partir\s+de\s+agora\b",
    r"\bde\s+agora\s+em\s+diante\b",
    r"\bde\s+hoje\s+em\s+diante\b",
    r"\bha\s+pouco\s+(?:mais|menos)\s+(?:de|que)\b",
))

_MESES_PT = (r"(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|"
             r"outubro|novembro|dezembro)")
# "agora em <mês>" (também "agora, em <mês>") é marcador de mês (E4_JANELA_MES), não de "agora".
_AGORA_EM_MES = re.compile(r"\bagora,?\s+em\s+" + _MESES_PT + r"\b")


def _normalizar(texto: str | None) -> str:
    """Minúsculo, sem acento e só ASCII. Caractere que não decompõe em ASCII (travessão, meia-risca,
    aspas) vira espaço: "ontem—o" são duas palavras e não pode virar "ontemo" (I-5). Marca combinante
    sai; o resto decomposto (º→o, ç→c, … → ...) segue como antes."""
    partes = []
    for ch in unicodedata.normalize("NFKD", texto or ""):
        if unicodedata.combining(ch):
            continue
        partes.append(ch if ord(ch) < 128 else " ")
    return re.sub(r"\s+", " ", "".join(partes).lower()).strip()


def _cfg_janela(variavel: str, default: int) -> int:
    """Janela efetiva (dias): config.E4_JANELA_* com o default linguístico de fallback."""
    try:
        return int(getattr(_config, variavel, default))
    except (TypeError, ValueError):
        return default


def _nucleo_temporal(texto_usuario: str) -> tuple[str, int] | None:
    """Núcleo único do E4: (trecho normalizado do marcador, janela em dias) do marcador de MENOR
    janela do texto, ou None se não há marcador. `janela_temporal` e `marcador_temporal` derivam
    daqui, para que nunca divirjam. Empate de janela: vale o primeiro na ordem de busca."""
    melhor: tuple[str, int] | None = None
    texto = _normalizar(texto_usuario)
    m_mes = _AGORA_EM_MES.search(texto)
    if m_mes:
        melhor = (m_mes.group(0), _cfg_janela("E4_JANELA_MES", 32))
        texto = _AGORA_EM_MES.sub(" ", texto)
    for exclusao in _EXCLUSOES:
        texto = exclusao.sub(" ", texto)
    for padrao, variavel, default in _MARCADORES:
        m = padrao.search(texto)
        if m:
            dias = _cfg_janela(variavel, default)
            if melhor is None or dias < melhor[1]:
                melhor = (m.group(0), dias)
    return melhor


def janela_temporal(texto_usuario: str) -> int | None:
    """Menor janela (dias) entre os marcadores temporais do texto; None se não há marcador."""
    achado = _nucleo_temporal(texto_usuario)
    return None if achado is None else achado[1]


def janelas_efetivas() -> dict[str, int]:
    """Janela (dias) efetiva de cada variável config.E4_JANELA_* nesta execução (já validada), mais o
    limite do ano inferido (E4_MARCO_MAX_DIAS, M-9: é parâmetro da medida tanto quanto as janelas).
    Vai para `parametros["e4"]["janelas"]` da decisão: o que valeu de fato, não os literais."""
    efetivas = {variavel: _cfg_janela(variavel, default) for variavel, default in _DEFAULTS_JANELA.items()}
    efetivas["E4_MARCO_MAX_DIAS"] = _cfg_janela("E4_MARCO_MAX_DIAS", _MARCO_MAX_DIAS_PADRAO)
    return efetivas


def janela_da_afirmacao(af_texto: str, texto_usuario: str, n_afirmacoes: int,
                        referencia: str | None = None) -> int | None:
    """Janela temporal de UMA afirmação: marcador na própria afirmação; senão, marcador do texto
    inteiro só quando há 1 afirmação (com 2+, o 'hoje' de uma frase não vaza para a outra).

    Data explícita vence o marcador relativo (decisão da usuária, 09/10): se a afirmação fixa o
    próprio tempo com data que não é a da `referencia` ("O deputado disse hoje que a ponte caiu em
    2019"), o "hoje" é do ato de dizer, não do fato, e a janela é None (quem mede é o marco). Com 1
    afirmação, o texto conta também. Ano igual ao da referência NÃO desliga o marcador: "hoje, em
    2026, ..." ainda fala do dia de hoje; o ano só desliga quando aponta outro tempo. Limitação
    conhecida: "Hoje, 2026 começa com..." (ano como sujeito de um fato de janeiro) mantém a janela.
    """
    datada = _data_explicita_fora(af_texto, referencia)
    if n_afirmacoes == 1:
        datada = datada or _data_explicita_fora(texto_usuario, referencia)
    if datada:
        return None
    janela = janela_temporal(af_texto)
    if janela is not None:
        return janela
    return janela_temporal(texto_usuario) if n_afirmacoes == 1 else None


#: Sentinela de `janela` em `e_aplicavel`: deriva a janela do texto recebido (compat E1).
#: Distinta de None, que significa "a afirmação não tem marcador" (nunca herdar o do texto).
JANELA_DO_TEXTO: object = object()


def dias_excedentes(texto_usuario: str, data_pub: str | None,
                    referencia: str | None = None,
                    marco: tuple[str, int] | None = None) -> tuple[int, int] | None:
    """(janela, dias além da janela) da fonte frente ao "hoje/ontem/..." do texto.

    None quando não dá para medir (sem marcador, sem data ou data ilegível): nesse caso
    a data não informa nada e a fonte segue sem desconto. `referencia` (YYYY-MM-DD) é o
    "hoje" do texto; None (desconhecida) = não mede. Nenhuma medida lê o relógio (M6).
    `marco` = (data_evento ISO, folga) de `marco_do_evento` (Task 4b): ancora o EVENTO
    em D e o excedente passa a `max(0, (D − folga) − data_pub)` — fonte posterior a D
    nunca é descontada. None (default) = comportamento anterior, sem data explícita.
    """
    if marco is not None:
        return dias_excedentes_do_marco(marco, data_pub)
    return dias_excedentes_da_janela(janela_temporal(texto_usuario), data_pub, referencia)


def dias_excedentes_da_janela(janela: int | None, data_pub: str | None,
                              referencia: str | None = None) -> tuple[int, int] | None:
    """(janela, dias além da janela) para UMA janela já resolvida (E4: a da afirmação).

    None quando não dá para medir: sem janela (sem marcador), sem data ou data ilegível,
    ou referência desconhecida (None). É o núcleo das medidas de data: `dias_excedentes` e o gate
    `e_aplicavel` passam por aqui.
    """
    if janela is None or not data_pub:
        return None
    if referencia is None:
        return None
    try:
        data = datetime.strptime(data_pub[:10], "%Y-%m-%d").date()
        ref = datetime.strptime(referencia[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return janela, max(0, (ref - data).days - janela)


def data_compativel(texto_usuario: str, data_pub: str | None,
                    referencia: str | None,
                    marco: tuple[str, int] | None = None) -> bool:
    """True se a data de publicação é compatível com os marcadores temporais do texto. `referencia`
    é obrigatória (M6): sem ela não há medida por data, e o relógio não entra."""
    medida = dias_excedentes(texto_usuario, data_pub, referencia, marco)
    return medida is None or medida[1] == 0


def e_aplicavel(
    classe: str | None,
    citacao_verificada: bool | None,
    corpo_lido: bool,
    veredito: str | None,
    texto_usuario: str,
    data_pub: str | None,
    referencia: str | None,
    janela: object = JANELA_DO_TEXTO,
    marco: tuple[str, int] | None = None,
) -> tuple[bool, str]:
    """Aplica as 5 regras em ordem; o primeiro False vence.

    Regra de data (E1, binária): barra se a fonte está além da janela (excedente > 0,
    isto é, relevancia_temporal < 1; sem limiar). Usa as MESMAS entradas da decisão
    (E4): `janela` da afirmação (`janela_da_afirmacao`; None = a afirmação não tem
    marcador), `marco` da afirmação (`marco_da_afirmacao`; tem precedência sobre a janela),
    `referencia` já resolvida (entrada → relógio → None) e `data_pub` normalizada.
    `referencia=None` (desconhecida) não barra por data. Sem `janela`, mantém o E1 anterior:
    a janela é a do próprio `texto_usuario`.
    """
    if corpo_lido is False:
        return (False, "corpo não lido")
    if classe not in TRATA:
        return (False, "juiz não trata do fato")
    if citacao_verificada is not True:
        return (False, "sem citação verificada")
    if direcao(veredito) == 0.0:
        return (False, "sem selo com direção")
    j = janela_temporal(texto_usuario) if janela is JANELA_DO_TEXTO else janela
    medida = medida_da_afirmacao(j, marco, data_pub, referencia)  # type: ignore[arg-type]
    if medida is not None and medida[1] > 0:
        return (False, "data incompatível")
    return (True, "aplicável")


# ------------------------------------------------------------------ Task 2 (E4): normalização de data_pub (pura, sem I/O)
_BRT = timezone(timedelta(hours=-3))

# Mês PT-BR normalizado (minúsculo, sem acento nem ponto) -> número.
_MESES_DATA = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3,
    "abril": 4, "abr": 4, "maio": 5, "mai": 5, "junho": 6, "jun": 6,
    "julho": 7, "jul": 7, "agosto": 8, "ago": 8, "setembro": 9, "set": 9,
    "outubro": 10, "out": 10, "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
}
# Mês em inglês ("06 Sep 2022", "Mar 3, 2024"), mesma convenção de chave de _MESES_DATA.
_MESES_EN = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Placeholders do extrator para "data desconhecida": nunca são data real. Valem só SEM hora.
_SENTINELAS_DATA = frozenset({"1970-01-01", "1900-01-01", "2000-01-01"})
# Mesmas sentinelas como INSTANTE UTC à meia-noite (I-6): placeholder exibido em outro fuso continua sendo.
_EPOCAS_SENTINELA = frozenset(datetime(ano, 1, 1, tzinfo=timezone.utc) for ano in (1900, 1970, 2000))
# Data de publicação antes disso é ruído de extrator (ou N relativo absurdo): não é data.
_ANO_MINIMO = 1900
# Teto de N em "há N unidades": acima disso é absurdo (e evita estouro de calendário).
_MAX_RELATIVA_N = 100_000

# Unidade relativa normalizada (sem acento) -> (dias por unidade, precisao).
_UNIDADES_RELATIVAS = {
    "minuto": (0, "dia"), "minutos": (0, "dia"),
    "hora": (0, "dia"), "horas": (0, "dia"),
    "dia": (1, "dia"), "dias": (1, "dia"),
    "semana": (7, "dia"), "semanas": (7, "dia"),
    "mes": (30, "ano"), "meses": (30, "ano"),
    "ano": (365, "ano"), "anos": (365, "ano"),
    "minute": (0, "dia"), "minutes": (0, "dia"),
    "hour": (0, "dia"), "hours": (0, "dia"),
    "day": (1, "dia"), "days": (1, "dia"),
    "week": (7, "dia"), "weeks": (7, "dia"),
    "month": (30, "ano"), "months": (30, "ano"),
    "year": (365, "ano"), "years": (365, "ano"),
}
_UNIDADES_ALTERNANCIA = "|".join(sorted(_UNIDADES_RELATIVAS, key=len, reverse=True))
# Quantidade em algarismos ou por extenso ("há um dia", "há uma semana") — normalizada, sem acento.
_NUMEROS_EXTENSO = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5,
                    "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10}
_QUANTIDADE = r"(\d{1,6}|" + "|".join(_NUMEROS_EXTENSO) + r")"
_RELATIVA_HA_RE = re.compile(r"^ha\s+" + _QUANTIDADE + r"\s+(" + _UNIDADES_ALTERNANCIA + r")\b")
_RELATIVA_ATRAS_RE = re.compile(r"^" + _QUANTIDADE + r"\s+(" + _UNIDADES_ALTERNANCIA + r")\s+atras$")
_RELATIVA_AGO_RE = re.compile(r"^" + _QUANTIDADE + r"\s+(" + _UNIDADES_ALTERNANCIA + r")\s+ago$")

# ISO 8601 com as variações das fontes: "YYYY-MM" (mês), "YYYY-MM-DD", espaço no lugar do T,
# fração de segundo, "Z"/"UTC" ou offset com/sem dois-pontos.
_ISO_RE = re.compile(
    r"(\d{4})-(\d{2})(?:-(\d{2}))?"
    r"(?:[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,]\d+)?)?)?"
    r"\s*(Z|UTC|[+-]\d{2}(?::?\d{2})?)?",
    re.IGNORECASE,
)
# RFC 2822 ("Tue, 06 Sep 2022 00:00:00 GMT"), o formato de feeds e de Last-Modified.
_RFC2822_RE = re.compile(
    r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,\s*)?\d{1,2}\s+[a-z]{3}\s+\d{4}\s+\d{2}:\d{2}(?::\d{2})?"
    r"\s+(?:gmt|utc|ut|z|[+-]\d{4}|[a-z]{3})",
    re.IGNORECASE,
)
# PT-BR por extenso, com hora opcional ("26 de jan. de 2012 às 10:00"; "às" vira "as" ao normalizar).
_PT_EXTENSO_RE = re.compile(r"(\d{1,2})o?\s+de\s+([a-z.]+)\s+de\s+(\d{4})"
                            r"(?:\s+(?:as\s+)?\d{1,2}[:h]\d{2}(?::\d{2})?)?")
_EN_DIA_MES_ANO_RE = re.compile(r"(\d{1,2})\s+([a-z]{3,9})\.?\s+(\d{4})")
_EN_MES_DIA_ANO_RE = re.compile(r"([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")
# Numérica dia-primeiro ("08/10/2026", "31/12/2025 10:00", "08-10-2026") e ano-primeiro ("2026/10/08").
_NUM_DIA_PRIMEIRO_RE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?:\s+\d{1,2}[:h]\d{2}(?::\d{2})?)?")
_NUM_ANO_PRIMEIRO_RE = re.compile(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})")
_SO_ANO_RE = re.compile(r"[12]\d{3}")


def _mes(nome: str) -> int | None:
    """Mês (1-12) de um nome PT ou EN, normalizado (sem acento, sem ponto)."""
    chave = nome.replace(".", "")
    return _MESES_DATA.get(chave) or _MESES_EN.get(chave)


def _relativa(norm: str) -> tuple[int, str] | None:
    """(quantidade, unidade) de uma data relativa normalizada, ou None."""
    m = _RELATIVA_HA_RE.match(norm) or _RELATIVA_ATRAS_RE.match(norm) or _RELATIVA_AGO_RE.match(norm)
    if m is None:
        return None
    qtd = _NUMEROS_EXTENSO.get(m.group(1)) or int(m.group(1))
    return qtd, m.group(2)


def e_relativa(valor: str | None) -> bool:
    """True se o valor é uma data relativa ("há 3 dias", "2 dias atrás"): precisa de âncora."""
    return isinstance(valor, str) and _relativa(_normalizar(valor)) is not None


def motivo_data_ilegivel(valor: str | None) -> str:
    """Motivo do fallback `data_pub` para um valor não-vazio que normalizar_data não leu."""
    return "sem âncora" if e_relativa(valor) else "formato não reconhecido"


def _offset(tz: str) -> timedelta:
    """Offset de um sufixo de fuso ISO: "Z"/"UTC" = 0; "-03", "-0300", "-03:00", "+05:30"."""
    if tz.upper() in ("Z", "UTC"):
        return timedelta(0)
    sinal = -1 if tz[0] == "-" else 1
    digitos = tz[1:].replace(":", "")
    return sinal * timedelta(hours=int(digitos[:2]), minutes=int(digitos[2:4] or 0))


def _dt_iso(ano: str, mes: str, dia: str, hh: str | None, mi: str | None,
            ss: str | None, tz: str | None) -> datetime:
    """Componentes ISO -> datetime (naive se sem fuso). ValueError se a data não existe."""
    h, minuto, seg = (int(hh), int(mi), int(ss or 0)) if hh is not None else (0, 0, 0)
    tzinfo = timezone(_offset(tz)) if tz is not None else None
    return datetime(int(ano), int(mes), int(dia), h, minuto, seg, tzinfo=tzinfo)


def _dia_de_datetime(dt: datetime) -> tuple[date, bool]:
    """(dia de publicação, sem_hora) de um datetime.

    Sem fuso ou meia-noite LOCAL exata (qualquer offset): a data escrita (é "só data" serializada;
    converter p/ BRT recuaria 1 dia, o erro para o lado de DESCONTAR mais). Outros casos com
    fuso: convertidos p/ BRT (UTC−3, A2). `sem_hora` = meia-noite ou só data.
    """
    meia_noite = (dt.hour, dt.minute, dt.second) == (0, 0, 0)
    if dt.tzinfo is None or meia_noite:
        return dt.date(), meia_noite
    return dt.astimezone(_BRT).date(), False


def _eh_sentinela_instante(dt: datetime) -> bool:
    """True se o INSTANTE é um placeholder à meia-noite UTC (epoch 1970, 1900, 2000), mesmo exibido em
    outro fuso: "1970-01-01T03:00:00+03:00" é o epoch (I-6). Naive não tem instante: False."""
    return dt.tzinfo is not None and dt.astimezone(timezone.utc) in _EPOCAS_SENTINELA


def _data_de_ancora(ancora: str | None) -> date | None:
    """Âncora (YYYY-MM-DD ou ISO com hora/fuso, p.ex. created_at da SerpAPI) -> dia em BRT."""
    if not isinstance(ancora, str):
        return None
    m = _ISO_RE.fullmatch(ancora.strip())
    if m is None or m.group(3) is None:
        return None
    return _dia_de_datetime(_dt_iso(*m.groups()))[0]


def _fechar(dia: date, precisao: str = "dia") -> tuple[str, str] | None:
    """Data calculada sem placeholder: só o piso de ano (None se implausível)."""
    if dia.year < _ANO_MINIMO:
        return None
    return dia.isoformat(), precisao


def _fechar_absoluta(dia: date, sem_hora: bool) -> tuple[str, str] | None:
    """Data absoluta ISO/RFC. Sentinelas e -01-01 são placeholders do extrator: valem só sem hora.

    -01-01 sem hora vira "fim do ano, precisão ano" (A1: placeholder de ano). Hora real
    (ex.: 10h UTC) não é placeholder e segue como dia.
    """
    if sem_hora:
        if dia.isoformat() in _SENTINELAS_DATA:
            return None
        if dia.month == 1 and dia.day == 1:
            return _fechar(date(dia.year, 12, 31), "ano")
    return _fechar(dia, "dia")


def _data_textual(nome: str, dia: str, ano: str) -> tuple[str, str] | None:
    """Data por extenso (mês por nome, PT ou EN): dia de publicação ou None se o mês não existe."""
    mes = _mes(nome)
    if mes is None:
        return None
    return _fechar(date(int(ano), mes, int(dia)), "dia")


def _normalizar_data(valor: str | None, ancora: str | None) -> tuple[str, str] | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    s = valor.strip()
    norm = _normalizar(s)

    # 1. Relativas ("há 3 dias", "2 dias atrás", "há um dia", "3 days ago"): exigem âncora.
    rel = _relativa(norm)
    if rel is not None:
        qtd, unidade = rel
        base = _data_de_ancora(ancora)
        if base is None or qtd > _MAX_RELATIVA_N:
            return None
        mult, precisao = _UNIDADES_RELATIVAS[unidade]
        return _fechar(base - timedelta(days=qtd * mult), precisao)

    # 2. ISO ("YYYY-MM-DD[ T]hh:mm[:ss][.f][Z|±hh:mm]", "YYYY-MM" = fim do mês) e RFC 2822.
    m = _ISO_RE.fullmatch(s)
    if m is not None:
        ano, mes, dia, hh, mi, ss, tz = m.groups()
        if dia is None:  # "YYYY-MM": só o mês; fim do mês, precisão ano
            if hh is not None or tz is not None:
                return None
            ano_i, mes_i = int(ano), int(mes)
            return _fechar(date(ano_i, mes_i, calendar.monthrange(ano_i, mes_i)[1]), "ano")
        dt = _dt_iso(ano, mes, dia, hh, mi, ss, tz)
        if _eh_sentinela_instante(dt):
            return None
        dia_pub, sem_hora = _dia_de_datetime(dt)
        return _fechar_absoluta(dia_pub, sem_hora)
    if _RFC2822_RE.fullmatch(s) is not None:
        dt = parsedate_to_datetime(s)
        if _eh_sentinela_instante(dt):
            return None
        dia_pub, sem_hora = _dia_de_datetime(dt)
        return _fechar_absoluta(dia_pub, sem_hora)

    # 3. Textual: PT-BR ("26 de jan. de 2012", "1º de março de 2020") e EN ("06 Sep 2022", "Mar 3, 2024").
    m = _PT_EXTENSO_RE.fullmatch(norm)
    if m is not None:
        return _data_textual(m.group(2), m.group(1), m.group(3))
    m = _EN_DIA_MES_ANO_RE.fullmatch(norm)
    if m is not None:
        return _data_textual(m.group(2), m.group(1), m.group(3))
    m = _EN_MES_DIA_ANO_RE.fullmatch(norm)
    if m is not None:
        return _data_textual(m.group(1), m.group(2), m.group(3))

    # 4. Numérica: dia-primeiro (padrão BR) ou ano-primeiro com barra/ponto.
    m = _NUM_DIA_PRIMEIRO_RE.fullmatch(norm)
    if m is not None:
        return _fechar(date(int(m.group(3)), int(m.group(2)), int(m.group(1))), "dia")
    m = _NUM_ANO_PRIMEIRO_RE.fullmatch(norm)
    if m is not None:
        return _fechar(date(int(m.group(1)), int(m.group(2)), int(m.group(3))), "dia")

    # 5. Só ano ("2021", ano do Scholar) -> fim do ano.
    if _SO_ANO_RE.fullmatch(norm) is not None:
        return _fechar(date(int(norm), 12, 31), "ano")

    return None


def normalizar_data(valor: str | None, ancora: str | None = None) -> tuple[str, str] | None:
    """Data de publicação em qualquer formato real -> (fim_iso YYYY-MM-DD, precisao dia|ano).

    Pura (sem I/O, sem fallback) e TOTAL: nunca levanta. Vazio/ilegível/implausível -> None.
    Usa o FIM do intervalo ("2021" -> 2021-12-31; "2021-05" -> 2021-05-31; "há 3 dias" ancorado
    na data da busca), nunca descontando mais do que a data permite afirmar. Relativas exigem
    `ancora` (sem âncora -> None). Fuso: ver `_dia_de_datetime`. Sentinelas e -01-01 sem hora
    são placeholders (None e "ano"). Futuro em relação à âncora é mantido. Ilegível não-vazio
    vira fallback `telemetria.fallback("data_pub", motivo_data_ilegivel(v))` CHAMADO PELO
    PIPELINE, não daqui.
    """
    try:
        return _normalizar_data(valor, ancora)
    except (ValueError, OverflowError, TypeError, IndexError, OSError):
        return None  # dado da web é entrada hostil: calendário estourado, formato torto etc.


def referencia_de_pagina(data_bruta: str | None) -> str | None:
    """"Hoje" de uma página lida (entrada por link): dia ISO, ou None.

    Só com precisão de DIA. "Só ano", sentinela ou placeholder -01-01 não dizem o dia da
    matéria; usar o fim do ano deslocaria o desconto do E4 para o lado errado. None = E4 off.
    """
    norm = normalizar_data(data_bruta, None)
    if norm is None or norm[1] != "dia":
        return None
    return norm[0]


def marcador_temporal(texto_usuario: str) -> str | None:
    """Literal (normalizado) do marcador que define `janela_temporal(texto)`: o de menor janela.

    Devolve o trecho casado pelo mesmo núcleo (`_nucleo_temporal`) que calcula a janela, então
    os dois nunca divergem. Usado na etapa de recebimento do pipeline.
    """
    achado = _nucleo_temporal(texto_usuario)
    return None if achado is None else achado[0]


# ------------------------------------------------------------------ Task 4b (E4): data explícita do evento (pura, sem I/O)
#: Variável config da folga do marco (reusa a do "hoje"; ver `marco_do_evento`).
_MARCO_FOLGA_VAR = "E4_JANELA_HOJE"

_MARCO_MESES = "|".join(sorted(_MESES_DATA, key=len, reverse=True))
# Ano que fixa outro tempo: só com preposição antes ("em 2019", "desde 2019", "até 2030") ou em data
# completa (checada à parte, em `_ocorrencias`). "Hoje, 2020 pessoas morreram" não é data (M-3).
_ANO_PREP_RE = re.compile(r"\b(?:em|de|desde|ate|no ano de|do ano de|a partir de)\s+((?:19|20)\d{2})\b")
# Dia/mês com contexto de data ("dia 08/10", "em 08/10", "no 08/10", "desde", "até"), com ou sem
# ano. Sem contexto, "2/3", "3/1" e "3/4" são frações e placares: não ancoram nada. Bordas
# excluem versão/hora/URL ("v1.08/10", "08/10:00"); ponto final de frase ("em 08/10.") é permitido.
_MARCO_DD_MM_CTX_RE = re.compile(
    r"\b(?:dia|em|no|na|desde|ate)\s+(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?![/.:]\d)(?!\w)")
# "08/10/2026" sem contexto: com ano, a barra já é data.
_MARCO_DD_MM_ANO_RE = re.compile(r"(?<![\w/.:])(\d{1,2})/(\d{1,2})/(\d{4})(?![/.:]\d)(?!\w)")
# dd/mm sem contexto e sem ano ("Hoje, 08/10, o ministro caiu"): só conta como data explícita do
# "hoje" (M-4); não ancora o marco (sem contexto, "2/3" e placares são frações).
_MARCO_DD_MM_NU_RE = re.compile(r"(?<![\w/.:,])(\d{1,2})/(\d{1,2})(?![/.:]\d)(?!\w)")
# ISO ("2026-10-08", "2026-10-08 10:00", "2026-10-08t10:00"): data completa, com ano.
_MARCO_ISO_RE = re.compile(r"(?<![\w/.:-])((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})"
                           r"(?:[t ]\d{1,2}:\d{2}(?::\d{2})?)?(?![\w/-]|[.:]\d)")
# O que vem DEPOIS de dd/mm sem ano e o torna fração: palavra de quantidade ("8/10 dos casos",
# "1/2 hora", "1/2 tempo"). "de" conta só quando não abre um ano ("08/10 de 2026" é data).
_FRACAO_DEPOIS_RE = re.compile(r"\s+(?:dos?|das|da|cada|horas?|h|min|minutos|tempo|parte|tanque)\b"
                               r"|\s+de\b(?!\s+(?:19|20)\d{2}\b)")
# "8 de outubro" ou "8 de outubro de 2023".
_MARCO_DIA_MES_RE = re.compile(r"\b(\d{1,2})o?\s+de\s+(" + _MARCO_MESES + r")(?:\s+de\s+(\d{4}))?\b")
# "dia 8" ou "dia 8 de outubro (de 2023)"; "dia 8h" é hora, não data; "dia 8 de 2026" (sem mês) não é data.
_MARCO_DIA_RE = re.compile(r"\bdia\s+(\d{1,2})o?(?!\s*h\b)(?!\s+de\s+\d{4}\b)"
                           r"(?:\s+de\s+(" + _MARCO_MESES + r")(?:\s+de\s+(\d{4}))?)?\b")
# "de 2 a 8 de outubro" / "de 30 de setembro a 2 de outubro": o INÍCIO do intervalo é o marco
# (fonte publicada depois do início não é de outro episódio; ver `_sem_intervalos`).
_MARCO_INTERVALO_RE = re.compile(r"\b(?:de|entre|dias)\s+(\d{1,2})(?:\s+de\s+(" + _MARCO_MESES + r"))?\s+(?:a|ate|e)\s+"
                                 r"(\d{1,2})\s+de\s+(" + _MARCO_MESES + r")\b")

# Data citada (prazo, aniversário, agenda futura...), não data do fato -> não ancora.
# Conservador de propósito: prefere não descontar a descontar pelo episódio errado.
# "vence/vencem" exatos: o prefixo casaria "venceu" (vencer = ganhar), que é o fato, não prazo.
_MARCO_GUARDAS = tuple(re.compile(r"\b" + g) for g in (
    r"prazo", r"vence(?:m)?\b", r"venciment", r"inscric", r"pagament", r"pagar", r"boleto",
    r"aniversario", r"comemor", r"parabens", r"completa",
    r"previsto", r"prevista", r"adiado", r"agendad", r"marcad", r"proxim", r"que vem\b",
    r"nasceu", r"nascid", r"nascimento",
))
# Verbo no futuro ("será", "vai", "irá"): a data é do que vai acontecer, não do fato.
_MARCO_FUTURO_RE = re.compile(r"\b(?:sera|serao|vai|vao|ira|irao)\b")
# Data comemorativa ("Dia 8 de março é o Dia da Mulher", "é o dia da mulher"): não é data do fato.
# A forma com maiúscula usa o texto original (o normalizado não tem caixa).
_MARCO_COMEMORATIVA_RE = re.compile(r"\bDia\s+d[aeo]\s+[A-ZÀ-Ý]")
_MARCO_COMEMORATIVA_NORM_RE = re.compile(r"\b(?:e|eh) o dia d[aeo]\b")
_MARCO_ATE_DIA_RE = re.compile(r"\bate\s+(o\s+)?dia\b")
_MARCO_NOTA_RE = re.compile(r"\bnota\s+\d")


def _marco_mais_recente(mes: int, dia: int, ref: date) -> date | None:
    """Ocorrência mais recente de (mês, dia) ≤ ref (ano inferido); None se impossível."""
    for ano in (ref.year, ref.year - 1):
        try:
            cand = date(ano, mes, dia)
        except ValueError:
            continue
        if cand <= ref:
            return cand
    return None


def _marco_dia_bare(dia: int, ref: date) -> date | None:
    """Ocorrência mais recente do dia do mês ≤ ref (p/ "dia 8" sem mês: volta meses)."""
    for k in range(12):
        mes = ref.month - k
        ano = ref.year
        while mes < 1:
            mes += 12
            ano -= 1
        try:
            cand = date(ano, mes, dia)
        except ValueError:
            continue
        if cand <= ref:
            return cand
    return None


def _ref_date(referencia: str | None) -> date | None:
    """Referência (YYYY-MM-DD) como date; None se desconhecida ou ilegível (nunca o relógio)."""
    if referencia is None:
        return None
    try:
        return datetime.strptime(referencia[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _sem_intervalos(texto_n: str) -> str:
    """'de 2 a 8 de outubro' -> '2 de outubro': o início do intervalo é a data do fato."""
    return _MARCO_INTERVALO_RE.sub(lambda m: f"{m.group(1)} de {m.group(2) or m.group(4)}", texto_n)


def _fracao_depois(texto_n: str, pos: int) -> bool:
    """True se logo após `pos` vem palavra de fração/quantidade (ver `_FRACAO_DEPOIS_RE`)."""
    return _FRACAO_DEPOIS_RE.match(texto_n, pos) is not None


# I-3: recorrência ("todo dia 5", "cada dia 5", "dia 5 do mês", "dia 5 de cada mês", "mensal") e
# data comemorativa ("Dia das Mães", "Dia Internacional da Mulher", "Dia 8 de março é o Dia da Mulher")
# não são data do fato. A comemorativa vale na MESMA frase (sem depender de maiúscula).
_RECORRENTE_ANTES_RE = re.compile(r"\b(?:todo|todos\s+os|todas\s+as|cada)\s+$")
_RECORRENTE_DEPOIS_RE = re.compile(r"\s+(?:do\s+mes|por\s+mes|de\s+cada\s+mes|mensal)\b")
_COMEMORATIVA_RE = re.compile(r"\bdia\s+(?:de|da|do|das|dos|internacional|nacional|mundial)\b")
_FRASE_FIM_RE = re.compile(r"[.!?;\n]")


def _recorrente_ou_comemorativa(texto_n: str, ini: int, fim: int) -> bool:
    """True se a data em texto_n[ini:fim] é recorrente ou comemorativa (I-3), e não data do fato."""
    if _RECORRENTE_ANTES_RE.search(texto_n[max(0, ini - 16):ini]) or _RECORRENTE_DEPOIS_RE.match(texto_n, fim):
        return True
    ini_frase = max((m.end() for m in _FRASE_FIM_RE.finditer(texto_n, 0, ini)), default=0)
    m_fim = _FRASE_FIM_RE.search(texto_n, fim)
    fim_frase = m_fim.start() if m_fim else len(texto_n)
    return _COMEMORATIVA_RE.search(texto_n[ini_frase:fim_frase]) is not None


def _ocorrencias(texto_n: str, bare: bool) -> list[tuple[int | None, int, str | None, bool]]:
    """Ocorrências de data (mes|None, dia, ano|None, dia_solto) do texto normalizado, antes de inferir
    mês/ano. Descarta a FRAÇÃO (dd/mm sem ano seguido de palavra de quantidade, I-2) e a recorrente ou
    comemorativa (I-3). `bare` inclui dd/mm sem contexto nem ano, que é data explícita do "hoje" (M-4)
    mas não ancora o marco. `dia_solto` = "dia 8" sem mês (ano inferido por `_marco_dia_bare`)."""
    brutas: list[tuple[int, int, int | None, int | None, str | None, bool]] = []  # (ini, fim, mes, dia, ano, solto)
    for m in _MARCO_DD_MM_CTX_RE.finditer(texto_n):
        if m.group(3) is None and _fracao_depois(texto_n, m.end()):
            continue
        brutas.append((m.start(), m.end(), int(m.group(2)), int(m.group(1)), m.group(3), False))
    for m in _MARCO_DD_MM_ANO_RE.finditer(texto_n):
        brutas.append((m.start(), m.end(), int(m.group(2)), int(m.group(1)), m.group(3), False))
    if bare:
        for m in _MARCO_DD_MM_NU_RE.finditer(texto_n):
            if not _fracao_depois(texto_n, m.end()):
                brutas.append((m.start(), m.end(), int(m.group(2)), int(m.group(1)), None, False))
    for m in _MARCO_ISO_RE.finditer(texto_n):
        brutas.append((m.start(), m.end(), int(m.group(2)), int(m.group(3)), m.group(1), False))
    for m in _MARCO_DIA_MES_RE.finditer(texto_n):
        brutas.append((m.start(), m.end(), _MESES_DATA.get(m.group(2)), int(m.group(1)), m.group(3), False))
    for m in _MARCO_DIA_RE.finditer(texto_n):
        mes_nome = m.group(2)
        brutas.append((m.start(), m.end(), _MESES_DATA.get(mes_nome) if mes_nome else None,
                       int(m.group(1)), m.group(3), True))
    return [(mes, dia, ano, solto) for ini, fim, mes, dia, ano, solto in brutas
            if not _recorrente_ou_comemorativa(texto_n, ini, fim)]


def _datas_citadas(texto_n: str, ref: date, bare: bool = False) -> list[tuple[date, bool]]:
    """Datas de dia/mês citadas no texto normalizado, como (data, ano_inferido). Sem filtro de
    contexto, futuro ou distância: quem chama decide. Ano sem mês não é data. `bare`: ver `_ocorrencias`."""
    achadas: list[tuple[date, bool]] = []

    def _add(mes: int | None, dia: int, ano: str | None, bare: bool) -> None:
        if not 1 <= dia <= 31 or (mes is not None and not 1 <= mes <= 12):
            return
        if ano is not None:
            try:
                achadas.append((date(int(ano), mes or 0, dia), False))
            except (ValueError, TypeError):
                pass
            return
        if mes is not None:
            d = _marco_mais_recente(mes, dia, ref)
        elif bare:
            d = _marco_dia_bare(dia, ref)
        else:
            return
        if d is not None:
            achadas.append((d, True))

    for mes, dia, ano, dia_solto in _ocorrencias(texto_n, bare):
        _add(mes, dia, ano, dia_solto)
    return achadas


def _tem_data_explicita(texto_n: str) -> bool:
    """O texto normalizado cita alguma data (dia/mês, com ou sem ano, dd/mm sem contexto, ISO) ou um
    ano explícito? Frações ("8/10 dos casos") não contam (ver `_ocorrencias`)."""
    return bool(_ANO_PREP_RE.search(texto_n)) or bool(_ocorrencias(texto_n, bare=True))


def _data_explicita_fora(texto: str | None, referencia: str | None) -> bool:
    """O texto fixa o próprio tempo com data explícita que NÃO é a da referência: ano diferente do
    ano da referência, ou dia/mês (com ou sem ano) que cai em outro dia. Ano igual ao da referência
    não conta (ver `janela_da_afirmacao`). Sem referência conhecida, qualquer data explícita conta."""
    texto_n = _sem_intervalos(_normalizar(texto))
    if not texto_n:
        return False
    ref = _ref_date(referencia)
    if ref is None:
        return _tem_data_explicita(texto_n)
    if any(int(a) != ref.year for a in _ANO_PREP_RE.findall(texto_n)):
        return True
    return any(d != ref for d, _ in _datas_citadas(texto_n, ref, bare=True))


def marco_do_evento(texto: str | None, referencia: str | None = None) -> tuple[str, int] | None:
    """(data_evento ISO, folga dias) da data explícita do EVENTO ou None.

    "dia 8" / "8 de outubro" / "08/10" (com contexto de data: "dia", "em", "no", "na",
    "desde", "até", ou com ano) ancoram o evento em D = ocorrência mais recente ≤
    `referencia`. Com ano explícito ("8 de outubro de 2023") D é a data exata. Ano inferido
    com D a mais de `E4_MARCO_MAX_DIAS` (60) da referência não ancora: a data citada tende a
    não ser a do fato. Em "de 2 a 8 de outubro" o início (2 de outubro) é o marco. Não ancora:
    referência desconhecida (None); data citada e não do fato (prazo,
    aniversário, agenda, comemorativa "Dia 8 de março é o Dia da Mulher"); frações e placares
    ("2/3", "3/1") sem contexto de data; verbo no futuro ("será", "vai"); 2+ datas distintas
    (ambíguo); D > referência (evento futuro). Pura (sem I/O, sem fallback).

    Folga = `E4_JANELA_HOJE` (default 2 dias). Justificativa linguística: a data
    explícita ("dia 8" dito dia 9) tem a semântica de recência do "ontem" — o
    leitor espera cobertura do episódio nos dias ao redor de D, e a granularidade
    dia + atraso de publicação/fuso (BRT vs UTC) toleram ~2 dias. Folga maior
    fundiria episódios vizinhos (jogos em dias consecutivos); folga 0 ignoraria o
    atraso de publicação. Reusar a janela do "hoje" evita knob novo e mantém a
    agressividade do desconto idêntica à do marcador relativo correspondente.
    """
    ref = _ref_date(referencia)
    if ref is None:
        return None
    texto_n = _normalizar(texto)
    if not texto_n:
        return None
    if any(g.search(texto_n) for g in _MARCO_GUARDAS):
        return None
    if (_MARCO_ATE_DIA_RE.search(texto_n) or _MARCO_NOTA_RE.search(texto_n)
            or _MARCO_FUTURO_RE.search(texto_n) or _MARCO_COMEMORATIVA_NORM_RE.search(texto_n)
            or _MARCO_COMEMORATIVA_RE.search(texto or "")):
        return None
    achadas = _datas_citadas(_sem_intervalos(texto_n), ref)
    datas = {d for d, _ in achadas}
    if len(datas) != 1:
        return None  # nenhuma data, ou 2+ datas distintas (não dá para saber qual é a do fato)
    d = datas.pop()
    if d > ref:
        return None  # evento futuro: fonte antiga pode ser preparativo, não outro episódio
    so_ano_inferido = not any(not inferido for _, inferido in achadas)
    if so_ano_inferido and (ref - d).days > _cfg_janela("E4_MARCO_MAX_DIAS", _MARCO_MAX_DIAS_PADRAO):
        return None  # ano inferido de uma ocorrência distante: a data citada não é a do fato
    return d.isoformat(), _cfg_janela(_MARCO_FOLGA_VAR, 2)


def marco_da_afirmacao(af_texto: str, texto_usuario: str, n_afirmacoes: int,
                       referencia: str | None = None) -> tuple[str, int] | None:
    """Marco de UMA afirmação, com a mesma regra da janela: o da própria afirmação; senão, o do
    texto inteiro só quando há 1 afirmação e nenhuma das duas fixa outro tempo por ano ou data
    (com 2+ afirmações, a data de uma frase não ancora a outra)."""
    marco = marco_do_evento(af_texto, referencia)
    if marco is not None or n_afirmacoes != 1:
        return marco
    if _tem_data_explicita(_sem_intervalos(_normalizar(af_texto))) or _ANO_PREP_RE.search(_normalizar(texto_usuario)):
        return None
    return marco_do_evento(texto_usuario, referencia)


def marcas_da_afirmacao(af_texto: str, texto_usuario: str, n_afirmacoes: int,
                        referencia: str | None = None) -> tuple[int | None, tuple[str, int] | None]:
    """(janela, marco) de UMA afirmação: a ÚNICA montagem usada pelo pipeline (decisão e gate E1).
    Assim o gate e a decisão medem a fonte com as mesmas entradas (I-4: nunca divergem). None em
    `janela` ou `marco` é resultado calculado ("a afirmação não tem marcador de fato"), não ausência."""
    return (janela_da_afirmacao(af_texto, texto_usuario, n_afirmacoes, referencia),
            marco_da_afirmacao(af_texto, texto_usuario, n_afirmacoes, referencia))


def dias_excedentes_do_marco(marco: tuple[str, int] | None,
                             data_pub: str | None) -> tuple[int, int] | None:
    """(folga, dias além do evento) para um marco (D, folga): `max(0, (D − folga) − data_pub)`.
    Fonte publicada a partir de D − folga nunca é descontada. None sem marco ou data ilegível."""
    if marco is None or not data_pub:
        return None
    try:
        folga = int(marco[1])
        evento = datetime.strptime(marco[0][:10], "%Y-%m-%d").date()
        data = datetime.strptime(data_pub[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError, IndexError, AttributeError):
        return None
    return folga, max(0, (evento - data).days - folga)


def medida_da_afirmacao(janela: int | None, marco: tuple[str, int] | None,
                        data_pub: str | None, referencia: str | None) -> tuple[int, int] | None:
    """(janela ou folga, dias além) da fonte frente à AFIRMAÇÃO. O marco (data explícita do evento)
    tem precedência sobre a janela relativa. É a medida única da decisão e do gate E1."""
    if marco is not None:
        return dias_excedentes_do_marco(marco, data_pub)
    return dias_excedentes_da_janela(janela, data_pub, referencia)
