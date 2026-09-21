"""Contratos + neutralidade da redação (RF12/RNF02)."""
import pytest

from factcheck_mvp.agregador import FRASES_BINARIAS, agregar
from factcheck_mvp.schemas import EntradaConsulta, RelatorioChecagem
from factcheck_mvp.schemas import SinalAnalise


def test_entrada_rejeita_vazia():
    with pytest.raises(Exception):
        EntradaConsulta(tipo="texto", conteudo="ok")


def test_entrada_tipos():
    for tipo in ("texto", "titulo", "link"):
        assert EntradaConsulta(tipo=tipo, conteudo="conteúdo válido aqui").tipo == tipo


def _sinais(*tuplas):
    return [SinalAnalise(motor=m, rotulo=r, valor=v, confianca=c) for m, r, v, c in tuplas]


def test_sem_sinais_indeterminada():
    out = agregar([])
    assert out["propensao"] == "indeterminada"


@pytest.mark.parametrize("sinais,esperada", [
    ([("veredito-existente", "v", "FALSO", 0.9)], "alta"),
    ([("veredito-existente", "v", "VERDADEIRO", 0.9)], "baixa"),
    ([("modelo-fake", "m", "0.85", 0.8)], "alta"),
    ([("modelo-fake", "m", "0.10", 0.8)], "baixa"),
])
def test_agregador_extremos(sinais, esperada):
    assert agregar(_sinais(*sinais))["propensao"] == esperada


def test_justificativa_nunca_binaria():
    combos = [
        [("veredito-existente", "v", "FALSO", 0.9)],
        [("veredito-existente", "v", "VERDADEIRO", 0.9)],
        [("corroboracao", "c", "cobertura ampla", 0.8), ("modelo-fake", "m", "0.9", 0.7)],
        [("llm-padroes", "p", "encontrados: apelo_urgencia", 0.6)],
    ]
    for c in combos:
        texto = agregar(_sinais(*c))["justificativa"].lower()
        assert not any(f in texto for f in FRASES_BINARIAS), texto


def test_relatorio_serializa():
    rel = RelatorioChecagem(propensao="media", justificativa="j",
                            consulta=EntradaConsulta(tipo="texto", conteudo="algum texto aqui"))
    assert rel.model_dump()["versao"].startswith("mvp-")
