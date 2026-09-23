"""Regressões dos achados do code review (singleton jev, idx_not, fallback)."""
import asyncio

from factcheck_mvp import implicacao, serpapi_layer as camada
from factcheck_mvp.afirmacoes import extrair_afirmacoes
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta


def test_llm_juiz_resumo_e_termometro(monkeypatch):
    """Round 12: julgamento via LLM (resumo + juiz-termômetro); 2 peças em
    sequência não podem quebrar e o motor deve ser honesto."""
    chamadas = []

    def fake_chat(messages, max_tokens=0, timeout_s=0, temperature=0.0):
        prompt = (messages[0].get("content", "") if messages else "")
        chamadas.append("juiz" if "termômetro" in prompt else "resumo")
        if "termômetro" in prompt:
            return ('{"0": {"score": 80, "relevante": true}}', "deepseek/deepseek-v4-flash")
        return ("A notícia afirma X segundo o veículo.", "deepseek/deepseek-v4-flash")

    monkeypatch.setattr(implicacao.juiz_llm.llm_openrouter, "chat", fake_chat)
    r1 = implicacao.implicacao("texto fonte com mais de trinta caracteres aqui", "afirmação com tamanho suficiente")
    r2 = implicacao.implicacao("outro texto fonte com tamanho suficiente aqui", "outra afirmação suficiente aqui")
    assert chamadas.count("resumo") == 2 and chamadas.count("juiz") == 2
    assert r1["relevante"] is True and r1["sustenta"] == 0.8 and r1["refuta"] == 0.0
    assert r1["score"] == 80 and r1["motor"] == "llm-juiz" and r2["motor"] == "llm-juiz"
    assert "X segundo o veículo" in (r1["resumo"] or "")


def test_llm_juiz_sem_llm_cai_no_lexico(monkeypatch):
    """Sem LLM (cap/rede): fallback lexical honesto, nunca >= 0.6."""
    def _boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(implicacao.juiz_llm.llm_openrouter, "chat", _boom)
    r = implicacao.implicacao("Falso: aumento das bets foi aprovado no senado e bets ficam "
                              "proibidas no Brasil segundo deputado federal do congresso nacional",
                              "aumento das bets foi aprovado no senado")
    assert r["motor"] == "lexico-fallback"
    assert r["sustenta"] < 0.6 and r["refuta"] < 0.6
    assert r["score"] is None  # lexical não finge termômetro


def test_idx_not_consultado():
    """RF05: índice de notícias gerais participa (estava morto)."""
    idx_not = Indice.de_artigos([{
        "url": "https://bbc.test/x", "titulo": "Bets seguem reguladas e legais no Brasil",
        "corpo_texto": "apostas online regulamentadas", "fonte": {"id": "bbc-brasil", "nome": "BBC"},
        "tipo_conteudo": "noticia", "veredito": None}])
    pipe = Pipeline(Catalogo.carregar(), Indice(), idx_not,
                    serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="titulo", conteudo="Bets seguem reguladas e legais no Brasil"),
        usar_llm=False))
    assert any(f.tipo_fonte == "corroboracao" and "bbc.test" in f.url for f in rel.fontes)


def test_fallback_sem_afirmacao_indeterminada():
    """Só saudação/apelo -> [] -> caminho indeterminada (era inalcançável)."""
    afs, motor = extrair_afirmacoes("Bom dia! Compartilhe urgente com todos!!!", usar_llm=False)
    assert afs == [] and motor == "regex-deterministico"


def test_vazio_openrouter_nao_mata_extracao(monkeypatch):
    """Round 10: deepseek devia VAZIO p/ perguntas/afirmações curtas legítimas;
    confiar cegamente no VAZIO pulava SerpAPI (recibo '0 fontes')."""
    from factcheck_mvp import afirmacoes as mod

    monkeypatch.setattr(mod.llm_openrouter if hasattr(mod, "llm_openrouter") else mod,
                        "llm_openrouter", None, raising=False)

    def _fake_via_llm(texto, max_n):
        return [], "openrouter:deepseek/deepseek-v4-flash"

    monkeypatch.setattr(mod, "_via_llm", _fake_via_llm)
    afs, motor = extrair_afirmacoes("É verdade que o SUS vai acabar?", usar_llm=True)
    assert len(afs) >= 1
    assert motor == "regex-fallback-apos-llm-vazio"
    assert "SUS" in afs[0].texto
