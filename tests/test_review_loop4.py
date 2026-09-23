"""Fixes do review loop 4: _eh_generica gating, teto lexical, motor honesto."""
from factcheck_mvp.implicacao import _fallback_lexico
from factcheck_mvp.pipeline import _eh_generica
from factcheck_mvp.padroes_llm import analisar_padroes


def test_generica_nao_nullifica_corpo_lido():
    # URL comum de artigo (sem data/.htm) com corpo real lido: NÃO é genérica
    assert _eh_generica(
        "https://www.poder360.com.br/brasil/senado-aprova-mp-do-salario-minimo/",
        "Senado aprova MP do salário mínimo", "corpo real lido " * 30) is False
    # Mesma URL sem corpo (só manchete): genérica (homepage/seção)
    assert _eh_generica(
        "https://www.poder360.com.br/brasil/senado-aprova-mp-do-salario-minimo/",
        "Senado aprova MP do salário mínimo", "") is True


def test_generica_homepage_secao_continua():
    for u in ["https://portal.com/", "https://portal.com/fato-ou-fake/",
              "https://portal.com/sobre/", "https://portal.com/estadao-verifica/"]:
        assert _eh_generica(u, "Sobre nós", "corpo " * 100) is True


def test_lexico_nunca_cruza_0_6():
    # overlap alto + marcador de refutação: ref teto 0.55 (< barreira 0.6)
    fonte = ("Falso: aumento das bets foi aprovado no senado e bets ficam "
             "proibidas no Brasil segundo deputado federal do congresso nacional")
    afirmacao = "aumento das bets foi aprovado no senado"
    saida = _fallback_lexico(fonte, afirmacao)
    assert saida["sustenta"] < 0.6 and saida["refuta"] < 0.6
    if saida["relevante"]:
        assert max(saida["sustenta"], saida["refuta"]) >= 0.30


def test_padroes_motor_honesto(monkeypatch):
    # Sem OpenRouter e sem local: _via_llm falha -> regex determinístico
    import factcheck_mvp.config as cfg
    monkeypatch.setattr(cfg, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(cfg, "UNSLOTH_MODEL_NAME", "")
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    out = analisar_padroes("URGENTE compartilhe agora antes que apaguem!!!" * 10, usar_llm=True)
    assert out["motor"] == "regex-deterministico"
    assert out["padroes"], "fallback regex deve achar apelo_urgencia"
