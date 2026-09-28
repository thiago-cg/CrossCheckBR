"""Modelo de detecção: só opina em texto longo e entra com peso baixo.

No teste do FakenewsBR v6, abaixo de ~80 palavras o BERTimbau acerta só
65–74% das notícias verdadeiras (erra por formato: curto → fake).
Offline e determinístico (sem LLM, sem SerpAPI, sem rede)."""
import asyncio

from factcheck_mvp import config
from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta

LONGO = ("O Comitê de Política Monetária do Banco Central decidiu nesta quarta-feira, por "
         "unanimidade, manter a taxa básica de juros em 10,5% ao ano. Em comunicado, o "
         "colegiado afirmou que o cenário externo segue adverso e que a desancoragem das "
         "expectativas de inflação exige cautela, segundo o boletim divulgado em Brasília. "
         "A decisão era esperada pela maioria dos analistas consultados pelo boletim Focus, "
         "que projetam a manutenção dos juros até o fim do ano. O próximo encontro do comitê "
         "está marcado para daqui a seis semanas, quando serão divulgados novos dados de "
         "atividade econômica e de emprego.")


class _DetectorFixo:
    """Detector de teste com o mesmo piso de palavras do real."""
    nome = "fixo"

    def analisar(self, texto):
        from factcheck_mvp.modelo_fake import _curto_demais, _nao_aplicavel
        if _curto_demais(texto):
            return _nao_aplicavel(self.nome)
        return {"prob_fake": 0.9, "modelo": self.nome, "mock": False}


def _pipe():
    return Pipeline(Catalogo.carregar(), Indice(), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""), detector=_DetectorFixo())


def test_texto_longo_passa_do_piso():
    assert len(LONGO.split()) >= config.FAKE_MODEL_MIN_PALAVRAS


def test_modelo_nao_opina_em_manchete():
    rel = asyncio.run(_pipe().executar(
        EntradaConsulta(tipo="titulo", conteudo="Banco Central mantém a Selic em 10,5% ao ano"),
        usar_llm=False))
    assert not any(s.motor == "modelo-fake" for s in rel.sinais)
    assert any(e.nome == "modelo" and e.status == "pulada" for e in rel.etapas)
    assert any("texto curto" in l for l in rel.limitacoes)


def test_modelo_opina_em_texto_longo_com_peso_baixo():
    rel = asyncio.run(_pipe().executar(EntradaConsulta(tipo="texto", conteudo=LONGO),
                                       usar_llm=False))
    sinal = [s for s in rel.sinais if s.motor == "modelo-fake"]
    assert sinal and sinal[0].valor == "0.9"
    assert sinal[0].confianca == config.FAKE_MODEL_CONFIANCA


def test_sem_caminho_usa_mock(monkeypatch):
    from factcheck_mvp.modelo_fake import MockDetector, carregar_detector
    monkeypatch.setattr(config, "FAKE_MODEL_PATH", "")
    assert isinstance(carregar_detector(), MockDetector)
