"""Regressões dos achados do code review (laya singleton, idx_not, fallback)."""
import asyncio

from factcheck_mvp import implicacao, serpapi_layer as camada
from factcheck_mvp.afirmacoes import extrair_afirmacoes
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta


def test_router_singleton_chamavel_duas_vezes(monkeypatch):
    """Bug crítico: _router() se sobrescrevia; 2ª chamada quebrava."""
    chamadas = []

    class FakeRouter:
        def predict(self, state, questions, model=None):
            chamadas.append(1)
            return {"answers": {
                "sustenta": {"noul": 0.9}, "refuta": {"noul": 0.05}}, "routing": {}}

    monkeypatch.setattr(implicacao, "_ROUTER_INST", None)
    monkeypatch.setattr(implicacao, "_laya", type("M", (), {"Router": lambda **k: FakeRouter()}))
    implicacao.LAYA_OK = True
    r1 = implicacao.implicacao("texto fonte com mais de trinta caracteres aqui", "afirmação com tamanho suficiente")
    r2 = implicacao.implicacao("outro texto fonte com tamanho suficiente aqui", "outra afirmação suficiente aqui")
    assert len(chamadas) == 2
    assert r1["relevante"] is True and r2["sustenta"] == 0.9


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
    afs = extrair_afirmacoes("Bom dia! Compartilhe urgente com todos!!!", usar_llm=False)
    assert afs == []
