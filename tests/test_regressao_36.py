"""Invariantes nos 36 artigos coletados (17 checagem + 19 geral). Offline: sem rede/LLM.

Antes era só smoke test (propensão ∈ enum). Agora verifica comportamento: sem busca e sem
juiz NÃO existe evidência de conteúdo, então nenhum texto pode sair com nível direcional —
nem pelo estilo (caixa alta, apelos), nem pelo modelo mock, nem pelos padrões.
"""
import asyncio
import json
from pathlib import Path

from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.modelo_fake import MockDetector
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta

BASE = Path(__file__).resolve().parent.parent / "factcheck_mvp" / "data"


def _pipe():
    return Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""), detector=MockDetector())


def test_regressao_36_sem_evidencia_nunca_tem_nivel_direcional():
    chec = json.loads((BASE / "amostra_checagem.json").read_text(encoding="utf-8"))
    geral = json.loads((BASE / "amostra_geral.json").read_text(encoding="utf-8"))
    assert len(chec) + len(geral) == 36
    pipe = _pipe()
    n_ok = 0
    for doc in chec + geral:
        texto = f"{doc.get('titulo','')} {(doc.get('corpo_texto') or '')[:1500]}".strip()[:2000]
        if len(texto) < 10:
            continue
        rel = asyncio.run(pipe.executar(EntradaConsulta(tipo="texto", conteudo=texto), usar_llm=False))
        assert rel.propensao == "indeterminada", (texto[:80], rel.justificativa)
        assert rel.decisao and rel.decisao["votos"] == []
        nomes = {e.nome for e in rel.etapas}
        assert {"base-checagem", "descoberta", "agregacao"} <= nomes
        assert rel.header.startswith("⚪") and rel.why_1linha
        n_ok += 1
    assert n_ok >= 30


def test_rumor_vago_indeterminada_e_limitacao():
    rel = asyncio.run(_pipe().executar(
        EntradaConsulta(tipo="titulo",
                        conteudo="Ouvi dizer que vão fechar as escolas amanhã, não tenho certeza nem fonte"),
        usar_llm=False))
    assert rel.propensao == "indeterminada"
    assert any("segunda-mão" in (l or "") for l in rel.limitacoes)


def test_opiniao_nao_vira_veredito():
    rel = asyncio.run(_pipe().executar(
        EntradaConsulta(tipo="texto",
                        conteudo="Na minha opinião o governo deveria mudar tudo, acho que é um absurdo. " * 5),
        usar_llm=False))
    assert rel.propensao == "indeterminada"
    assert any("opinião" in (l or "").lower() for l in rel.limitacoes)
