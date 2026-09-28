"""Cache de relatórios: a mesma notícia mandada por várias pessoas não refaz
busca/LLM (economiza o teto diário). Offline e determinístico."""
import asyncio

from factcheck_mvp import config
from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta

TEXTO = "Governo anuncia novo calendário de pagamento do Bolsa Família para outubro de 2026."


class _DetectorContador:
    """Conta quantas vezes o pipeline rodou de fato."""
    nome = "contador"

    def __init__(self):
        self.chamadas = 0

    def analisar(self, texto):
        self.chamadas += 1
        return {"prob_fake": 0.5, "modelo": self.nome, "mock": False}


def _pipe(det):
    return Pipeline(Catalogo.carregar(), Indice(), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""), detector=det)


def test_cache_reaproveita_relatorio():
    det = _DetectorContador()
    pipe = _pipe(det)
    e = EntradaConsulta(tipo="texto", conteudo=TEXTO)
    r1 = asyncio.run(pipe.executar_com_cache(e, usar_llm=False))
    r2 = asyncio.run(pipe.executar_com_cache(
        EntradaConsulta(tipo="texto", conteudo="  " + TEXTO.upper() + " "), usar_llm=False))
    assert det.chamadas == 1  # mesmo texto normalizado: não reprocessa
    assert r1.propensao == r2.propensao
    r2.limitacoes.append("mutado")  # cópia: mexer no retorno não suja o cache
    r3 = asyncio.run(pipe.executar_com_cache(e, usar_llm=False))
    assert "mutado" not in r3.limitacoes


def test_cache_desligado(monkeypatch):
    monkeypatch.setattr(config, "RESULT_CACHE_TTL", 0)
    det = _DetectorContador()
    pipe = _pipe(det)
    e = EntradaConsulta(tipo="texto", conteudo=TEXTO)
    asyncio.run(pipe.executar_com_cache(e, usar_llm=False))
    asyncio.run(pipe.executar_com_cache(e, usar_llm=False))
    assert det.chamadas == 2
