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


def test_claim_vago_contido_e_pede_metrica():
    pipe = Pipeline(Catalogo.carregar(), Indice(), Indice(), serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="texto", conteudo="A economia do Brasil só piorou no governo Lula"),
        usar_llm=False))
    assert rel.propensao == "indeterminada"
    assert any("vaga" in lim.lower() for lim in rel.limitacoes), rel.limitacoes


def test_claim_com_indicador_nao_e_vago():
    pipe = Pipeline(Catalogo.carregar(), Indice(), Indice(), serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="texto", conteudo="O desemprego piorou e chegou a 9,5% no trimestre passado"),
        usar_llm=False))
    assert not any("vaga" in lim.lower() for lim in rel.limitacoes), rel.limitacoes


def test_dominio_base_agrupa_grupo_economico():
    from factcheck_mvp.corroboracao import dominio_base
    assert dominio_base("https://www.uol.com.br/x") == "uol.com.br"
    assert dominio_base("https://acervo.folha.uol.com.br/y") == "uol.com.br"
    assert dominio_base("https://g1.globo.com/z") == "globo.com"
    assert dominio_base("https://www.bbc.com/portuguese/a") == "bbc.com"


def test_mesmo_grupo_conta_1x():
    from factcheck_mvp.corroboracao import contar_independentes
    pecas = [
        {"url": "https://noticias.uol.com.br/a", "titulo": "Economia cresce, diz estudo",
         "corpo_texto": "texto completamente diferente um"},
        {"url": "https://www1.folha.uol.com.br/b", "titulo": "Outro ângulo da economia",
         "corpo_texto": "texto completamente diferente dois"},
        {"url": "https://www.bbc.com/portuguese/c", "titulo": "Terceira visão",
         "corpo_texto": "texto completamente diferente tres"},
    ]
    assert contar_independentes(pecas)["n"] == 2  # UOL+Folha = 1 grupo
