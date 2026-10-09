"""Gate de aplicabilidade da base (E1): decide se um match da base pode ser usado.

Módulo puro: sem I/O, sem LLM, sem rede.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from factcheck_mvp.selos import direcao

# Classes do juiz que indicam que a checagem trata do fato alegado.
TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")

# Marcador normalizado (minúsculo, sem acento) -> janela em dias (da data atual UTC).
_JANELAS = {
    "hoje": 2,
    "agora": 2,
    "acaba de": 2,
    "acabou de": 2,
    "ontem": 3,
    "nesta semana": 8,
    "neste semana": 8,
}

_JANELA_PADROES = tuple(
    (re.compile(r"\b" + re.escape(marcador) + r"\b"), janela)
    for marcador, janela in _JANELAS.items()
)


def _normalizar(texto: str | None) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def janela_temporal(texto_usuario: str) -> int | None:
    """Menor janela (dias) entre os marcadores temporais do texto; None se não há marcador."""
    janela = None
    texto = _normalizar(texto_usuario)
    for padrao, dias in _JANELA_PADROES:
        if padrao.search(texto):
            janela = dias if janela is None else min(janela, dias)
    return janela


def dias_excedentes(texto_usuario: str, data_pub: str | None,
                    referencia: str | None = None) -> tuple[int, int] | None:
    """(janela, dias além da janela) da fonte frente ao "hoje/ontem/..." do texto.

    None quando não dá para medir (sem marcador, sem data ou data ilegível): nesse caso
    a data não informa nada e a fonte segue sem desconto. `referencia` (YYYY-MM-DD) é o
    "hoje" do texto; vazio = data atual UTC.
    """
    janela = janela_temporal(texto_usuario)
    if janela is None or not data_pub:
        return None
    try:
        data = datetime.strptime(data_pub[:10], "%Y-%m-%d").date()
        ref = (datetime.strptime(referencia[:10], "%Y-%m-%d").date() if referencia
               else datetime.now(timezone.utc).date())
    except (ValueError, TypeError):
        return None
    return janela, max(0, (ref - data).days - janela)


def data_compativel(texto_usuario: str, data_pub: str | None) -> bool:
    """True se a data de publicação é compatível com os marcadores temporais do texto."""
    medida = dias_excedentes(texto_usuario, data_pub)
    return medida is None or medida[1] == 0


def e_aplicavel(
    classe: str | None,
    citacao_verificada: bool | None,
    corpo_lido: bool,
    veredito: str | None,
    texto_usuario: str,
    data_pub: str | None,
) -> tuple[bool, str]:
    """Aplica as 5 regras em ordem; o primeiro False vence."""
    if corpo_lido is False:
        return (False, "corpo não lido")
    if classe not in TRATA:
        return (False, "juiz não trata do fato")
    if citacao_verificada is not True:
        return (False, "sem citação verificada")
    if direcao(veredito) == 0.0:
        return (False, "sem selo com direção")
    if data_compativel(texto_usuario, data_pub) is False:
        return (False, "data incompatível")
    return (True, "aplicável")
