"""Pipeline offline ponta a ponta: determinístico (sem LLM, sem SerpAPI, sem rede)."""
import asyncio

from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta


def _doc(titulo, corpo, veredito):
    return {"url": "https://lupa.test/" + str(abs(hash(titulo)) % 9999), "titulo": titulo,
            "corpo_texto": corpo, "fonte": {"id": "lupa", "nome": "Lupa"},
            "tipo_conteudo": "checagem", "veredito": veredito}


def test_pipeline_offline_com_veredito():
    idx_ver = Indice.de_artigos([
        _doc("É falso que vídeo mostra invasão na Amazônia", "imagens são de 2019, outro país", "FALSO"),
    ])
    pipe = Pipeline(Catalogo.carregar(), idx_ver, Indice(), serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="texto", conteudo="Vídeo mostra invasão de homens armados na Amazônia, COMPARTILHE URGENTE!!!"),
        usar_llm=False))
    assert rel.propensao in ("baixa", "media", "alta", "indeterminada")
    assert rel.propensao == "alta"  # veredito FALSO + padrões + cobertura isolada
    assert any(e.nome == "base-checagem" and e.status == "ok" for e in rel.etapas)
    assert any("serpapi" in (e.detalhe + e.nome).lower() or e.nome == "descoberta" for e in rel.etapas)
    assert rel.fontes and rel.fontes[0].url.startswith("https://")
    assert len(rel.perguntas_guia) == 3


def test_pipeline_sem_nada_indeterminada_ou_baixa():
    pipe = Pipeline(Catalogo.carregar(), Indice(), Indice(), serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="titulo", conteudo="Receita de bolo de cenoura com cobertura"),
        usar_llm=False))
    assert rel.propensao in ("baixa", "indeterminada")
    assert any(e.nome == "descoberta" and e.status == "pulada" for e in rel.etapas)
