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


def test_justificativa_sem_expressoes_proibidas():
    from factcheck_mvp.agregador import EXPRESSOES_PROIBIDAS, agregar, verificar_neutralidade
    out = agregar(_sinais(("veredito-existente", "v", "FALSO", 0.9),
                          ("corroboracao", "c", "cobertura ampla", 0.8)))
    assert verificar_neutralidade(out["justificativa"]) == []
    assert not any(e in out["justificativa"].lower() for e in EXPRESSOES_PROIBIDAS)


def test_cobertura_ampla_pesa_mesmo_com_valor_neutro():
    # Bug real (resposta Lula/economia): rotulo "cobertura ampla" + valor neutro
    # pesava 0 — o 0.75 exibido não contava. Agora direção lê rótulo+valor.
    from factcheck_mvp.agregador import _direcao, agregar
    s = _sinais(("corroboracao", "cobertura ampla",
                 "informação presente em 3+ veículos independentes", 0.75))[0]
    assert _direcao(s) == -0.5
    out = agregar([s])
    assert out["score"] < 0, out


def test_relatorio_serializa():
    rel = RelatorioChecagem(propensao="media", justificativa="j",
                            consulta=EntradaConsulta(tipo="texto", conteudo="algum texto aqui"))
    assert rel.model_dump()["versao"].startswith("mvp-")
