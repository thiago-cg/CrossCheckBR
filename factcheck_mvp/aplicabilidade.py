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

# Marcador literal normalizado (minúsculo, sem acento) -> (variável config.E4_JANELA_*, default em dias).
_MARCADORES_JANELA = (
    ("hoje", "E4_JANELA_HOJE", 2),
    ("agora", "E4_JANELA_HOJE", 2),
    ("acaba de", "E4_JANELA_HOJE", 2),
    ("acabou de", "E4_JANELA_HOJE", 2),
    ("ha pouco", "E4_JANELA_HOJE", 2),
    ("ha instantes", "E4_JANELA_HOJE", 2),
    ("ha poucas horas", "E4_JANELA_HOJE", 2),
    ("agora ha pouco", "E4_JANELA_HOJE", 2),
    ("ontem", "E4_JANELA_ONTEM", 3),
    ("anteontem", "E4_JANELA_ANTEONTEM", 4),
    ("nesta semana", "E4_JANELA_SEMANA", 8),
    ("neste semana", "E4_JANELA_SEMANA", 8),
    ("neste fim de semana", "E4_JANELA_SEMANA", 8),
    ("semana passada", "E4_JANELA_SEMANA_PASSADA", 15),
    ("neste mes", "E4_JANELA_MES", 32),
    ("este mes", "E4_JANELA_MES", 32),
)

# Marcador normalizado (minúsculo, sem acento) -> janela em dias (da data atual UTC).
# Compat: defaults literais (o valor efetivo vem de config.E4_JANELA_* em janela_temporal).
_JANELAS = {marcador: default for marcador, _, default in _MARCADORES_JANELA}

_JANELA_PADROES = tuple(
    (re.compile(r"\b" + re.escape(marcador) + r"\b"), janela)
    for marcador, janela in _JANELAS.items()
)

# Dia da semana normalizado (sem acento); "-feira"/" feira" opcionais.
_SEMANA_DIA = (r"(?:domingo|segunda(?:-feira| feira)?|terca(?:-feira| feira)?|"
               r"quarta(?:-feira| feira)?|quinta(?:-feira| feira)?|"
               r"sexta(?:-feira| feira)?|sabado)")

# Padrões sobre o texto normalizado -> (variável config.E4_JANELA_*, default em dias).
_JANELA_REGEX = tuple(
    (re.compile(padrao), variavel, default)
    for padrao, variavel, default in (
        (r"\b(esta|nesta)\s+(manha|tarde|noite|madrugada)\b", "E4_JANELA_HOJE", 2),
        (r"\b(neste|nesta)\s+" + _SEMANA_DIA + r"\b", "E4_JANELA_SEMANA", 8),
        (r"\b(na\s+ultima|no\s+ultimo)\s+" + _SEMANA_DIA + r"\b", "E4_JANELA_SEMANA", 8),
    )
)

# Trechos que NÃO são marcador de momento ("hoje em dia", "até agora", ...):
# apagados antes da busca p/ o "hoje"/"agora" dentro deles não contar.
_EXCLUSOES = tuple(re.compile(padrao) for padrao in (
    r"\bhoje\s+em\s+dia\b",
    r"\bate\s+hoje\b",
    r"\bate\s+agora\b",
    r"\ba\s+partir\s+de\s+agora\b",
    r"\bde\s+agora\s+em\s+diante\b",
))

_MESES_PT = (r"(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|"
             r"outubro|novembro|dezembro)")
# "agora em <mês>" é marcador de mês (janela E4_JANELA_MES), não de "agora".
_AGORA_EM_MES = re.compile(r"\bagora\s+em\s+" + _MESES_PT + r"\b")


def _normalizar(texto: str | None) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def _cfg_janela(variavel: str, default: int) -> int:
    """Janela efetiva (dias): config.E4_JANELA_* com o default linguístico de fallback."""
    try:
        return int(getattr(_config, variavel, default))
    except (TypeError, ValueError):
        return default


def janela_temporal(texto_usuario: str) -> int | None:
    """Menor janela (dias) entre os marcadores temporais do texto; None se não há marcador."""
    janela = None
    texto = _normalizar(texto_usuario)
    if _AGORA_EM_MES.search(texto):
        janela = _cfg_janela("E4_JANELA_MES", 32)
        texto = _AGORA_EM_MES.sub(" ", texto)
    for exclusao in _EXCLUSOES:
        texto = exclusao.sub(" ", texto)
    for marcador, variavel, default in _MARCADORES_JANELA:
        if re.search(r"\b" + re.escape(marcador) + r"\b", texto):
            dias = _cfg_janela(variavel, default)
            janela = dias if janela is None else min(janela, dias)
    for padrao, variavel, default in _JANELA_REGEX:
        if padrao.search(texto):
            dias = _cfg_janela(variavel, default)
            janela = dias if janela is None else min(janela, dias)
    return janela


def janela_da_afirmacao(af_texto: str, texto_usuario: str, n_afirmacoes: int) -> int | None:
    """Janela temporal de UMA afirmação: marcador na própria afirmação; senão, marcador
    do texto inteiro só quando há 1 afirmação (com 2+, o 'hoje' de uma frase não vaza
    para a outra)."""
    janela = janela_temporal(af_texto)
    if janela is not None:
        return janela
    if n_afirmacoes == 1:
        return janela_temporal(texto_usuario)
    return None


def hoje_brt() -> str:
    """Data atual em UTC−3 (BRT, sem zoneinfo: UTC menos 3 h) como YYYY-MM-DD."""
    return (datetime.now(timezone.utc) - timedelta(hours=3)).date().isoformat()


#: Sentinela de `referencia` = "agora" (resolve para `hoje_brt()` na chamada).
AGORA: object = object()
#: Sentinela de `janela` em `e_aplicavel`: deriva a janela do texto recebido (compat E1).
#: Distinta de None, que significa "a afirmação não tem marcador" (nunca herdar o do texto).
JANELA_DO_TEXTO: object = object()


def dias_excedentes(texto_usuario: str, data_pub: str | None,
                    referencia: str | None = None,
                    marco: tuple[str, int] | None = None) -> tuple[int, int] | None:
    """(janela, dias além da janela) da fonte frente ao "hoje/ontem/..." do texto.

    None quando não dá para medir (sem marcador, sem data ou data ilegível): nesse caso
    a data não informa nada e a fonte segue sem desconto. `referencia` (YYYY-MM-DD) é o
    "hoje" do texto; None (desconhecida) = não mede; a sentinela `AGORA` = data atual BRT.
    `marco` = (data_evento ISO, folga) de `marco_do_evento` (Task 4b): ancora o EVENTO
    em D e o excedente passa a `max(0, (D − folga) − data_pub)` — fonte posterior a D
    nunca é descontada. None (default) = comportamento anterior, sem data explícita.
    """
    if marco is not None:
        if not data_pub:
            return None
        try:
            folga = int(marco[1])
            evento = datetime.strptime(marco[0][:10], "%Y-%m-%d").date()
            data = datetime.strptime(data_pub[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError, IndexError, AttributeError):
            return None
        return folga, max(0, (evento - data).days - folga)
    return dias_excedentes_da_janela(janela_temporal(texto_usuario), data_pub, referencia)


def dias_excedentes_da_janela(janela: int | None, data_pub: str | None,
                              referencia: str | None = None) -> tuple[int, int] | None:
    """(janela, dias além da janela) para UMA janela já resolvida (E4: a da afirmação).

    None quando não dá para medir: sem janela (sem marcador), sem data ou data ilegível,
    ou referência desconhecida (None; a sentinela `AGORA` = hoje BRT). É o núcleo das
    medidas de data: `dias_excedentes` e o gate `e_aplicavel` passam por aqui.
    """
    if janela is None or not data_pub:
        return None
    if referencia is None:
        return None
    if referencia is AGORA:
        referencia = hoje_brt()
    try:
        data = datetime.strptime(data_pub[:10], "%Y-%m-%d").date()
        ref = datetime.strptime(referencia[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return janela, max(0, (ref - data).days - janela)


def data_compativel(texto_usuario: str, data_pub: str | None,
                    referencia: str | None = AGORA,  # type: ignore[assignment]
                    marco: tuple[str, int] | None = None) -> bool:
    """True se a data de publicação é compatível com os marcadores temporais do texto."""
    medida = dias_excedentes(texto_usuario, data_pub, referencia, marco)  # type: ignore[arg-type]
    return medida is None or medida[1] == 0


def e_aplicavel(
    classe: str | None,
    citacao_verificada: bool | None,
    corpo_lido: bool,
    veredito: str | None,
    texto_usuario: str,
    data_pub: str | None,
    referencia: str | None = AGORA,  # type: ignore[assignment]
    janela: object = JANELA_DO_TEXTO,
) -> tuple[bool, str]:
    """Aplica as 5 regras em ordem; o primeiro False vence.

    Regra de data (E1, binária): barra se a fonte está além da janela (excedente > 0,
    isto é, relevancia_temporal < 1; sem limiar). Usa as MESMAS entradas da decisão
    (E4): `janela` da afirmação (`janela_da_afirmacao`; None = a afirmação não tem
    marcador), `referencia` já resolvida (entrada → relógio → None) e `data_pub`
    normalizada. `referencia=None` (desconhecida) não barra por data. Sem `janela`,
    mantém o E1 anterior: a janela é a do próprio `texto_usuario`.
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
    medida = dias_excedentes_da_janela(j, data_pub, referencia)  # type: ignore[arg-type]
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

    Sem fuso: a data escrita. Meia-noite UTC exata: a data literal (é "só data" serializada;
    converter p/ BRT recuaria 1 dia, o erro para o lado de DESCONTAR mais). Outros casos com
    fuso: convertidos p/ BRT (UTC−3, A2). `sem_hora` = meia-noite ou só data.
    """
    meia_noite = (dt.hour, dt.minute, dt.second) == (0, 0, 0)
    if dt.tzinfo is None:
        return dt.date(), meia_noite
    if dt.utcoffset() == timedelta(0) and meia_noite:
        return dt.date(), True
    return dt.astimezone(_BRT).date(), meia_noite


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
        dia_pub, sem_hora = _dia_de_datetime(_dt_iso(ano, mes, dia, hh, mi, ss, tz))
        return _fechar_absoluta(dia_pub, sem_hora)
    if _RFC2822_RE.fullmatch(s) is not None:
        dia_pub, sem_hora = _dia_de_datetime(parsedate_to_datetime(s))
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

    Mesma busca de `janela_temporal` (exclusões, "agora em <mês>", _MARCADORES_JANELA e
    _JANELA_REGEX), mas devolve o trecho casado, não a janela. Usado só para a etapa de
    recebimento; test_marcador_temporal_acompanha_janela_temporal_em_todos_os_casos guarda
    a equivalência com `janela_temporal`.
    """
    melhor: tuple[int, str] | None = None
    texto = _normalizar(texto_usuario)

    def _candidato(dias: int, trecho: str) -> None:
        nonlocal melhor
        if melhor is None or dias < melhor[0]:
            melhor = (dias, trecho)

    m_mes = _AGORA_EM_MES.search(texto)
    if m_mes:
        _candidato(_cfg_janela("E4_JANELA_MES", 32), m_mes.group(0))
        texto = _AGORA_EM_MES.sub(" ", texto)
    for exclusao in _EXCLUSOES:
        texto = exclusao.sub(" ", texto)
    for marcador, variavel, default in _MARCADORES_JANELA:
        if re.search(r"\b" + re.escape(marcador) + r"\b", texto):
            _candidato(_cfg_janela(variavel, default), marcador)
    for padrao, variavel, default in _JANELA_REGEX:
        m = padrao.search(texto)
        if m:
            _candidato(_cfg_janela(variavel, default), m.group(0))
    return melhor[1] if melhor is not None else None


# ------------------------------------------------------------------ Task 4b (E4): data explícita do evento (pura, sem I/O)
#: Variável config da folga do marco (reusa a do "hoje"; ver `marco_do_evento`).
_MARCO_FOLGA_VAR = "E4_JANELA_HOJE"

_MARCO_MESES = "|".join(sorted(_MESES_DATA, key=len, reverse=True))
# "08/10" ou "08/10/2026" (dia-primeiro, como normalizar_data). Ano com 2 dígitos
# não suportado. Bordas excluem versão/hora/URL ("v1.08/10", "08/10:00"); ponto
# final de frase ("em 08/10.") é permitido.
_MARCO_DD_MM_RE = re.compile(r"(?<![\w/.:])(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?![/.:]\d)(?!\w)")
# "8 de outubro" ou "8 de outubro de 2023".
_MARCO_DIA_MES_RE = re.compile(r"\b(\d{1,2})\s+de\s+(" + _MARCO_MESES + r")(?:\s+de\s+(\d{4}))?\b")
# "dia 8" ou "dia 8 de outubro (de 2023)"; "dia 8h" é hora, não data.
_MARCO_DIA_RE = re.compile(r"\bdia\s+(\d{1,2})(?!\s*h\b)(?:\s+de\s+(" + _MARCO_MESES
                           + r")(?:\s+de\s+(\d{4}))?)?\b")

# Data citada (prazo, aniversário, agenda futura...), não data do fato -> não ancora.
# Conservador de propósito: prefere não descontar a descontar pelo episódio errado.
_MARCO_GUARDAS = (
    "prazo", "vence", "venciment", "inscric", "pagament", "pagar", "boleto",
    "aniversario", "comemor", "parabens", "completa",
    "previsto", "adiado", "agendado", "proxim", "que vem",
    "nasceu", "nascido", "nascimento",
)
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


def marco_do_evento(texto: str | None, referencia: str | None = None) -> tuple[str, int] | None:
    """(data_evento ISO, folga dias) da data explícita do EVENTO ou None.

    "dia 8" / "8 de outubro" / "08/10" ancoram o evento em D = ocorrência mais
    recente ≤ `referencia` (ano inferido); com ano explícito ("8 de outubro de
    2023", "08/10/2023") D é a data exata. `referencia=None` (desconhecida) ou
    ilegível -> None; a sentinela `AGORA` resolve para `hoje_brt()`. Não ancora
    quando a data é citada e não do fato (prazo, aniversário, agenda futura),
    quando há 2+ datas distintas (ambíguo: não dá para saber qual é do fato) ou
    quando D > referência (evento futuro: fonte antiga pode ser preparativo, não
    outro episódio). Pura (sem I/O, sem fallback).

    Folga = `E4_JANELA_HOJE` (default 2 dias). Justificativa linguística: a data
    explícita ("dia 8" dito dia 9) tem a semântica de recência do "ontem" — o
    leitor espera cobertura do episódio nos dias ao redor de D, e a granularidade
    dia + atraso de publicação/fuso (BRT vs UTC) toleram ~2 dias. Folga maior
    fundiria episódios vizinhos (jogos em dias consecutivos); folga 0 ignoraria o
    atraso de publicação. Reusar a janela do "hoje" evita knob novo e mantém a
    agressividade do desconto idêntica à do marcador relativo correspondente.
    """
    if referencia is None:
        return None
    if referencia is AGORA:
        referencia = hoje_brt()
    try:
        ref = datetime.strptime(referencia[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    texto_n = _normalizar(texto)
    if not texto_n:
        return None
    if any(re.search(r"\b" + g + r"\w*", texto_n) for g in _MARCO_GUARDAS):
        return None
    if _MARCO_ATE_DIA_RE.search(texto_n) or _MARCO_NOTA_RE.search(texto_n):
        return None

    datas: set[date] = set()

    def _add(mes: int | None, dia: int, ano: str | None, bare: bool) -> None:
        if not 1 <= dia <= 31 or (mes is not None and not 1 <= mes <= 12):
            return
        if ano is not None:
            try:
                d = date(int(ano), mes or 0, dia)
            except (ValueError, TypeError):
                return
        elif mes is not None:
            d = _marco_mais_recente(mes, dia, ref)
        elif bare:
            d = _marco_dia_bare(dia, ref)
        else:
            return
        if d is not None and d <= ref:
            datas.add(d)

    for m in _MARCO_DD_MM_RE.finditer(texto_n):
        _add(int(m.group(2)), int(m.group(1)), m.group(3), False)
    for m in _MARCO_DIA_MES_RE.finditer(texto_n):
        _add(_MESES_DATA.get(m.group(2)), int(m.group(1)), m.group(3), False)
    for m in _MARCO_DIA_RE.finditer(texto_n):
        mes_nome, ano = m.group(2), m.group(3)
        if mes_nome is None and ano is not None:
            continue  # "dia 8 de 2026" (sem mês): ignora, conservador
        _add(_MESES_DATA.get(mes_nome) if mes_nome else None, int(m.group(1)), ano, True)

    if len(datas) != 1:
        return None
    return sorted(datas)[0].isoformat(), _cfg_janela(_MARCO_FOLGA_VAR, 2)
