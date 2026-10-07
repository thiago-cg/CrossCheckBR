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


def data_compativel(texto_usuario: str, data_pub: str | None) -> bool:
    """True se a data de publicação é compatível com os marcadores temporais do texto."""
    janela = None
    texto = _normalizar(texto_usuario)
    for padrao, dias in _JANELA_PADROES:
        if padrao.search(texto):
            janela = dias if janela is None else min(janela, dias)
    if janela is None:
        return True
    if not data_pub:
        return True
    try:
        data = datetime.strptime(data_pub[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).date()
    except (ValueError, TypeError):
        return True
    hoje = datetime.now(timezone.utc).date()
    return (hoje - data).days <= janela


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
