"""T3 (E5): selo sempre atribuído à agência; neutralidade só aceita esse formato.

Opção (b): selos aparecem atribuídos ("Selo da Lupa: FALSO") enquanto a NOSSA
conclusão continua em escala de propensão. `verificar_neutralidade` ignora só
o formato atribuído e só com agência cadastrada no catálogo.
"""
from factcheck_mvp.agregador import formatar_selo, verificar_neutralidade


def test_aceita_selo_atribuido_a_agencia_cadastrada():
    texto = formatar_selo("Lupa", "FALSO")
    assert texto == "Selo da Lupa: FALSO"
    assert verificar_neutralidade(texto) == []


def test_reprova_conclusao_binaria_nossa():
    assert verificar_neutralidade("Nossa conclusão: o texto é falso.") != []


def test_reprova_selo_com_agencia_nao_cadastrada():
    texto = formatar_selo("Blog X", "FALSO", artigo="do")
    assert texto == "Selo do Blog X: FALSO"
    assert verificar_neutralidade(texto) != []
