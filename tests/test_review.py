"""Regressões dos achados do code review: sem fallback léxico de postura, afirmações nunca vazias."""
import json

from factcheck_mvp import implicacao, llm
from factcheck_mvp.afirmacoes import extrair_afirmacoes


def test_implicacao_usa_o_juiz_de_4_classes(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", lambda m, mt, ts, fin: (json.dumps(
        {"itens": [{"i": 0, "classe": "REFUTA", "citacao": "não há evidência de que o café cure câncer"}]}), "m"))
    r = implicacao.implicacao("Segundo o INCA, não há evidência de que o café cure câncer.", "Café cura câncer",
                              titulo="Café e câncer")
    assert r["classe"] == "REFUTA" and r["relevante"] is True and r["citacao_verificada"] is True


def test_sem_llm_nao_ha_fallback_lexico_de_postura(monkeypatch):
    """C3: sobreposição de palavras não pode virar 'sustenta'/'relevante'."""
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")

    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(llm, "_local", boom)
    r = implicacao.implicacao("Aumento das bets foi aprovado no senado e bets ficam proibidas no Brasil",
                              "aumento das bets foi aprovado no senado")
    assert r["classe"] is None and r["relevante"] is None and r["motor"].startswith("fallback")
    assert not hasattr(implicacao, "_fallback_lexico")


def test_fallback_sem_afirmacao_indeterminada():
    afs, motor = extrair_afirmacoes("Bom dia! Compartilhe urgente com todos!!!", usar_llm=False)
    assert afs == [] and motor == "fallback-regex"


def test_vazio_do_llm_nao_mata_extracao(monkeypatch):
    """Round 10: LLM devolvia VAZIO/[] p/ perguntas curtas legítimas -> SerpAPI nunca consultada."""
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", lambda m, mt, ts, fin: ("[]", "m"))
    afs, motor = extrair_afirmacoes("É verdade que o SUS vai acabar?", usar_llm=True)
    assert len(afs) >= 1 and motor == "fallback-regex"
    assert "SUS" in afs[0].texto
