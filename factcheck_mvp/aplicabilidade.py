"""Gate de aplicabilidade da base (E1): decide se um match da base pode ser usado.

Módulo puro: sem I/O, sem LLM, sem rede.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

from factcheck_mvp import config as _config
from factcheck_mvp.selos import direcao

# Classes do juiz que indicam que a checagem trata do fato alegado.
TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")

# Dia da semana normalizado (sem acento); "-feira"/" feira" opcionais. O lookahead exclui o
# ordinal ("esta segunda parte", "a segunda vez"), que não é dia da semana.
_SEMANA_DIA = (r"(?:domingo|segunda(?:-feira| feira)?|terca(?:-feira| feira)?|"
               r"quarta(?:-feira| feira)?|quinta(?:-feira| feira)?|"
               r"sexta(?:-feira| feira)?|sabado)"
               r"(?!\s+(?:vez|parte|etapa|fase|tentativa|edicao|rodada|turno|onda)\b)")
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
    r"\bha\s+pouco\s+(?:mais|menos)\s+de\b",
))

_MESES_PT = (r"(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|"
             r"outubro|novembro|dezembro)")
# "agora em <mês>" (também "agora, em <mês>") é marcador de mês (E4_JANELA_MES), não de "agora".
_AGORA_EM_MES = re.compile(r"\bagora,?\s+em\s+" + _MESES_PT + r"\b")


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
    for padrao, variavel, default in _MARCADORES:
        if padrao.search(texto):
            dias = _cfg_janela(variavel, default)
            janela = dias if janela is None else min(janela, dias)
    return janela


def janelas_efetivas() -> dict[str, int]:
    """Janela (dias) efetiva de cada variável config.E4_JANELA_* nesta execução (já validada).
    Vai para `parametros["e4"]["janelas"]` da decisão: o que valeu de fato, não os literais."""
    return {variavel: _cfg_janela(variavel, default) for variavel, default in _DEFAULTS_JANELA.items()}


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

# Placeholders do extrator para "data desconhecida": nunca são data real.
_SENTINELAS_DATA = frozenset({"1970-01-01", "1900-01-01", "2000-01-01"})

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
_RELATIVA_HA_RE = re.compile(r"^ha\s+(\d+)\s+(" + _UNIDADES_ALTERNANCIA + r")\b")
_RELATIVA_ATRAS_RE = re.compile(r"^(\d+)\s+(" + _UNIDADES_ALTERNANCIA + r")\s+atras$")
_RELATIVA_AGO_RE = re.compile(r"^(\d+)\s+(" + _UNIDADES_ALTERNANCIA + r")\s+ago$")


def _data_iso_para_dia(texto: str | None) -> object | None:
    """Âncora/ISO (YYYY-MM-DD, ISO com/sem fuso, 'YYYY-MM-DD HH:MM:SS [UTC]') -> date em UTC−3.

    None se não parseia. Só ancora datas absolutas (nunca relativas).
    """
    if not isinstance(texto, str) or not texto.strip():
        return None
    txt = texto.strip()
    if txt[-1:] in ("Z", "z") and ("T" in txt or " " in txt):
        txt = txt[:-1] + "+00:00"
    txt = re.sub(r"\s+utc$", "+00:00", txt, flags=re.I)
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.astimezone(_BRT).date()
        return dt.date()
    return dt


def normalizar_data(valor: str | None, ancora: str | None = None) -> tuple[str, str] | None:
    """Data de publicação em qualquer formato real -> (fim_iso YYYY-MM-DD, precisao dia|ano).

    Pura (sem I/O, sem fallback): vazio/ilegível -> None. Usa o FIM do intervalo
    ("2021" -> 2021-12-31; "há 3 dias" ancorado na data da busca), nunca descontando
    mais do que a data permite afirmar. Relativas exigem `ancora` (sem âncora ->
    None). Futuro em relação à âncora é mantido. Ilegível não-vazio vira fallback
    `telemetria.fallback("data_pub", ...)` CHAMADO PELO PIPELINE, não daqui.
    """
    if not isinstance(valor, str) or not valor.strip():
        return None
    s = valor.strip()

    # 1. Relativas ("há 3 dias", "2 dias atrás", "3 days ago"): exigem âncora.
    norm = _normalizar(s)
    m = (_RELATIVA_HA_RE.match(norm) or _RELATIVA_ATRAS_RE.match(norm)
         or _RELATIVA_AGO_RE.match(norm))
    if m:
        base = _data_iso_para_dia(ancora)
        if base is None:
            return None
        mult, prec = _UNIDADES_RELATIVAS[m.group(2)]
        fim = base - timedelta(days=int(m.group(1)) * mult)
        return fim.isoformat(), prec

    # 2. ISO / "YYYY-MM-DD HH:MM:SS [UTC]": com fuso converte p/ UTC−3 antes da data.
    tem_hora = bool(re.search(r"[T ]\d{1,2}:\d{2}", s))
    dia = _data_iso_para_dia(s)
    if dia is not None and re.match(r"^\d{4}-\d{2}-\d{2}", s):
        try:
            dt = datetime.fromisoformat(
                re.sub(r"\s+utc$", "+00:00", s[:-1] + "+00:00"
                       if s[-1:] in ("Z", "z") and ("T" in s or " " in s) else s,
                       flags=re.I))
            meia_noite = (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0)
        except ValueError:
            meia_noite = True
        sem_hora = (not tem_hora) or meia_noite
        iso = dia.isoformat()
        if iso in _SENTINELAS_DATA and sem_hora:
            return None
        if sem_hora and dia.month == 1 and dia.day == 1 and iso not in _SENTINELAS_DATA:
            return f"{dia.year:04d}-12-31", "ano"  # placeholder de ano do extrator
        return iso, "dia"

    # 3. PT-BR textual ("26 de jan. de 2012", "3 de julho de 2026").
    m = re.match(r"^(\d{1,2})\s+de\s+([a-zà-ÿ.]+)\s+de\s+(\d{4})$", s, re.I)
    if m:
        mes = _MESES_DATA.get(_normalizar(m.group(2)).replace(".", "").replace(" ", ""))
        try:
            dia = datetime(int(m.group(3)), mes or 0, int(m.group(1))).date() if mes else None
        except ValueError:
            dia = None
        if dia is not None:
            return dia.isoformat(), "dia"

    # 4. Numérica dia-primeiro ("08/10/2026") ou ano-primeiro com barra ("2026/10/08").
    m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$", s)
    if m:
        try:
            dia = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
        except ValueError:
            dia = None
        if dia is not None:
            return dia.isoformat(), "dia"
    m = re.match(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$", s)
    if m:
        try:
            dia = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            dia = None
        if dia is not None:
            return dia.isoformat(), "dia"

    # 5. Só ano ("2021", ano do Scholar) -> fim do ano.
    m = re.match(r"^([12]\d{3})$", s)
    if m:
        return f"{m.group(1)}-12-31", "ano"

    return None


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
