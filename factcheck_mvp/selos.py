"""Selo literal da agência -> veredito normalizado (enum) -> direção numérica.

A direção sai SEMPRE do enum, nunca de texto livre (achado D3 da revisão:
"Fato ou Fake" + VERDADEIRO virava ALTA por substring de "fake" no nome
do portal). A tabela é editorial e mora em data/selos.json, com
`revisao_humana: "pendente"` até alguém validar.

Contrato (consumido pela fase de decisão — não renomear):
    VEREDITOS, normalizar(selo, agencia=None), direcao(veredito)
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

VEREDITOS = ("FALSO", "ENGANOSO", "SEM_EVIDENCIA", "VERDADEIRO", "SATIRA")

# + = propenso a desinformação; - = afirmação confirmada.
_DIRECAO = {
    "FALSO": 1.0,
    "ENGANOSO": 0.7,
    "SEM_EVIDENCIA": 0.4,
    "VERDADEIRO": -1.0,
    "SATIRA": 0.0,
}

CAMINHO_TABELA = Path(__file__).resolve().parent / "data" / "selos.json"


def chave(selo: Optional[str]) -> str:
    """Normaliza o selo para busca na tabela: sem acento/caixa/'#', pontuação vira espaço."""
    texto = unicodedata.normalize("NFKD", selo or "").encode("ascii", "ignore").decode().lower()
    texto = texto.replace("#", " ")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


@lru_cache(maxsize=4)
def _tabela(caminho: str = str(CAMINHO_TABELA)) -> Dict:
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    geral = {chave(k): v for k, v in (dados.get("geral") or {}).items()}
    por_agencia = {
        (ag or "").strip().lower(): {chave(k): v for k, v in (tab or {}).items()}
        for ag, tab in (dados.get("por_agencia") or {}).items()
    }
    frases = {chave(k): v for k, v in (dados.get("frases_de_titulo") or {}).items()}
    return {"geral": geral, "por_agencia": por_agencia, "frases": frases,
            "revisao_humana": dados.get("revisao_humana")}


def normalizar(selo: Optional[str], agencia: Optional[str] = None) -> Optional[str]:
    """Selo literal ("Falso", "#FAKE", "Distorcido"…) -> enum de VEREDITOS, ou None.

    Override por agência tem prioridade (inclusive override para null, que
    desliga um selo naquela agência). Selo desconhecido -> None: nunca
    adivinhar por substring."""
    k = chave(selo)
    if not k:
        return None
    tab = _tabela()
    ag = (agencia or "").strip().lower()
    if ag and ag in tab["por_agencia"] and k in tab["por_agencia"][ag]:
        v = tab["por_agencia"][ag][k]
    elif k in tab["geral"]:
        v = tab["geral"][k]
    else:
        # convenções de manchete ("não disse", "é antigo"): só lookup exato
        v = tab["frases"].get(k)
    return v if v in VEREDITOS else None


def direcao(veredito: Optional[str]) -> float:
    """Enum -> direção em [-1, 1]. None/desconhecido -> 0.0 (sem sinal)."""
    return _DIRECAO.get(veredito or "", 0.0)


def conhecido(selo: Optional[str], agencia: Optional[str] = None) -> bool:
    """True se o selo está na tabela (mesmo que mapeado para null/sem direção)."""
    k = chave(selo)
    tab = _tabela()
    ag = (agencia or "").strip().lower()
    return bool(k) and (k in tab["geral"] or k in tab["frases"] or k in tab["por_agencia"].get(ag, {}))
