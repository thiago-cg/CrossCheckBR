"""_eh_generica (homepage/seção) e padrões (sinal de estilo) com motor honesto."""
from factcheck_mvp import llm
from factcheck_mvp.padroes_llm import analisar_padroes
from factcheck_mvp.pipeline import _eh_generica, _eh_homepage


def test_generica_nao_nullifica_corpo_lido():
    assert _eh_generica(
        "https://www.poder360.com.br/brasil/senado-aprova-mp-do-salario-minimo/",
        "Senado aprova MP do salário mínimo", "corpo real lido " * 30) is False
    assert _eh_generica(
        "https://www.poder360.com.br/brasil/senado-aprova-mp-do-salario-minimo/",
        "Senado aprova MP do salário mínimo", "") is True


def test_generica_homepage_secao_continua():
    for u in ["https://portal.com/", "https://portal.com/fato-ou-fake/",
              "https://portal.com/sobre/", "https://portal.com/estadao-verifica/"]:
        assert _eh_generica(u, "Sobre nós", "corpo " * 100) is True


def test_homepage_antes_da_leitura_so_o_inequivoco():
    for u in ["https://boatos.org/", "https://www.mozillafoundation.org/en/", "https://g1.globo.com/fato-ou-fake/"]:
        assert _eh_homepage(u) is True
    assert _eh_homepage("https://www.poder360.com.br/brasil/senado-aprova-mp/") is False


def test_padroes_motor_honesto_sem_llm(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")

    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(llm, "_local", boom)
    out = analisar_padroes("URGENTE compartilhe agora antes que apaguem!!!" * 10, usar_llm=True)
    assert out["motor"] == "fallback-regex" and out["padroes"]


def test_padroes_nenhum_e_lista_vazia_valida(monkeypatch):
    """C5: o ramo local fazia `return []` (ValueError no unpack) e caía no regex em 7/12 casos."""
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", lambda m, mt, ts, fin: ("NENHUM.", "m-local"))
    out = analisar_padroes("A dengue é transmitida pelo mosquito Aedes aegypti.", usar_llm=True)
    assert out == {"padroes": [], "motor": "llm-local:m-local"}
