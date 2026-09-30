"""Selos: tabela explícita -> enum -> direção. Nunca por substring do nome do portal."""
import json

import pytest

from factcheck_mvp import selos


@pytest.mark.parametrize("selo,agencia,esperado", [
    ("Falso", None, "FALSO"), ("#FAKE", "fato-ou-fake", "FALSO"), ("É #FAKE", None, "FALSO"),
    ("Enganoso", None, "ENGANOSO"), ("Distorcido", "aos-fatos", "ENGANOSO"), ("Exagerado", None, "ENGANOSO"),
    ("Insustentável", None, "SEM_EVIDENCIA"), ("Sem contexto", None, "ENGANOSO"), ("Farsa", None, "FALSO"),
    ("Boato", "boatos", "FALSO"), ("Verdadeiro", None, "VERDADEIRO"), ("#FATO", "fato-ou-fake", "VERDADEIRO"),
    ("Fato", None, "VERDADEIRO"), ("Verdadeiro, mas…", "lupa", "VERDADEIRO"), ("Sátira", None, "SATIRA"),
    ("não_é_bem_assim", "aos-fatos", "ENGANOSO"), ("Comprovado", "comprova", "VERDADEIRO"),
    ("Falta contexto", "comprova", "ENGANOSO"),
])
def test_normalizar_tabela(selo, agencia, esperado):
    assert selos.normalizar(selo, agencia) == esperado


def test_desconhecido_e_nome_de_portal_viram_none():
    # D3: "Fato ou Fake" é NOME de portal, não selo; substring não conta.
    for s in ("Fato ou Fake", "Fato ou Boato (TSE)", "blá", "", None, "falsificação"):
        assert selos.normalizar(s) is None


def test_override_por_agencia_inclusive_null():
    assert selos.normalizar("Humor") == "SATIRA"
    assert selos.normalizar("Humor", "e-farsas") is None  # categoria de tema no E-farsas


def test_direcao():
    assert selos.direcao("FALSO") == 1.0 and selos.direcao("ENGANOSO") == 0.7
    assert selos.direcao("SEM_EVIDENCIA") == 0.4 and selos.direcao("VERDADEIRO") == -1.0
    assert selos.direcao("SATIRA") == 0.0 and selos.direcao(None) == 0.0 and selos.direcao("xyz") == 0.0
    assert selos.VEREDITOS == ("FALSO", "ENGANOSO", "SEM_EVIDENCIA", "VERDADEIRO", "SATIRA")


def test_tabela_marcada_para_revisao_humana_e_valores_validos():
    dados = json.loads(selos.CAMINHO_TABELA.read_text(encoding="utf-8"))
    assert dados["revisao_humana"] == "pendente" and "editorial" in dados["nota"].lower()
    for secao in ("geral", "frases_de_titulo"):
        for v in dados[secao].values():
            assert v is None or v in selos.VEREDITOS
    for tab in dados["por_agencia"].values():
        for v in tab.values():
            assert v is None or v in selos.VEREDITOS
