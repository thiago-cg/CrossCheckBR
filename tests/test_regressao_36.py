"""Regressão nos 36 artigos coletados (17 checagem + 19 geral). Offline: sem rede/LLM."""
import asyncio
import json
from pathlib import Path

from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta

BASE = Path(__file__).resolve().parent.parent / "factcheck_mvp" / "data"


def _carregar():
    chec = json.loads((BASE / "amostra_checagem.json").read_text(encoding="utf-8"))
    geral = json.loads((BASE / "amostra_geral.json").read_text(encoding="utf-8"))
    return chec, geral


def test_regressao_36_sem_crash_e_com_recibo():
    chec, geral = _carregar()
    assert len(chec) + len(geral) == 36, f"esperava 36, achou {len(chec)+len(geral)}"
    idx_ver = Indice()
    idx_not = Indice()
    for a in chec + geral:
        (idx_ver if a.get("tipo_conteudo") == "checagem" else idx_not).adicionar(a)
    pipe = Pipeline(Catalogo.carregar(), idx_ver, idx_not,
                    serpapi=camada.SerpAPIClient(api_key=""))
    n_ok = 0
    for doc in (chec + geral):
        texto = f"{doc.get('titulo','')} {(doc.get('corpo_texto') or '')[:1500]}".strip()[:2000]
        if len(texto) < 10:
            continue
        rel = asyncio.run(pipe.executar(
            EntradaConsulta(tipo="texto", conteudo=texto), usar_llm=False))
        assert rel.propensao in ("baixa", "media", "alta", "indeterminada")
        nomes = {e.nome for e in rel.etapas}
        assert {"base-checagem", "descoberta", "agregacao"} <= nomes
        assert any(e.nome == "descoberta" and e.status == "pulada" for e in rel.etapas)
        assert rel.header and rel.why_1linha  # clareza loop 1
        assert rel.versao.startswith("mvp-")
        n_ok += 1
    assert n_ok >= 30


def test_rumor_vago_indeterminada_e_limitacao():
    pipe = Pipeline(Catalogo.carregar(), Indice(), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="titulo",
                        conteudo="Ouvi dizer que vão fechar as escolas amanhã, não tenho certeza nem fonte"),
        usar_llm=False))
    assert rel.propensao == "indeterminada"
    assert any("segunda-mão" in (l or "") for l in rel.limitacoes)


def test_opiniao_nao_vira_veredito():
    pipe = Pipeline(Catalogo.carregar(), Indice(), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="texto",
                        conteudo="Na minha opinião o governo deveria mudar tudo, acho que é um absurdo. " * 5),
        usar_llm=False))
    assert rel.propensao in ("indeterminada", "baixa")
    assert any("opinião" in (l or "").lower() for l in rel.limitacoes)
