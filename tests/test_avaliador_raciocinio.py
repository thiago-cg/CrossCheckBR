"""T4/B1a: avaliador retorna categoria + `raciocinio`; ele chega a `rel.fontes[i]` sem corte.

O limite de ~240 chars vive SO no prompt — nunca truncar no codigo.
Caminho todo com LLM fake (sem rede, sem Unsloth, sem record).
"""
from types import SimpleNamespace
import asyncio

from factcheck_mvp import avaliador, juiz_llm
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.modelo_fake import MockDetector
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import Afirmacao
from factcheck_mvp.serpapi_layer import SerpAPIClient

NUCLEO = "Café cura câncer"

CITACAO = "pesquisadores confirmam que o café cura o câncer em pacientes tratados"

# Propositalmente MAIOR que 240 chars: o codigo nunca pode cortar.
RACIOCINIO_LONGO = (
    "A pagina afirma com a propria voz que o cafe cura o cancer em pacientes tratados, "
    "trazendo estudo com centenas de pacientes acompanhados por dois anos e depoimentos "
    "de remissao completa, o que confirma diretamente o nucleo da afirmacao verificada."
)

CORPO = (
    "Pesquisadores confirmam que o café cura o câncer em pacientes tratados, "
    "segundo estudo divulgado nesta semana. O trabalho acompanhou centenas de "
    "pacientes ao longo de dois anos e concluiu que o consumo diário da bebida "
    "eliminou os tumores na maioria dos casos observados. Os autores afirmam que "
    "o café cura o câncer e recomendam a bebida como tratamento principal. "
    "Outros especialistas ouvidos pela reportagem disseram que vão revisar os "
    "protocolos diante da descoberta. A publicação inclui gráficos, tabelas e "
    "depoimentos de pacientes que relatam a remissão completa da doença."
)

assert len(RACIOCINIO_LONGO) > 240
assert len(CORPO) >= 500


def _peca():
    return {
        "url": "https://exemplo.com/cafe-cura",
        "titulo": "Café cura câncer, diz estudo",
        "corpo": CORPO,
        "snippet": "Estudo diz que café cura câncer...",
        "veiculo": "Blog Exemplo",
        "dominio": "exemplo.com",
        "data_pub": "2026-01-01",
        "corpo_lido": True,
    }


def _fake_julgar_lote(afirmacao, itens):
    return [{"classe": "SUSTENTA", "citacao": CITACAO, "citacao_score": 1.0,
             "citacao_verificada": True, "pagina_diz": "A página afirma que café cura câncer",
             "raciocinio": RACIOCINIO_LONGO, "motor": "fake", "modelo": "fake",
             "erro": None}]


def _pipe():
    """Instância mínima real (sem rede: SerpAPI desligado, detector mock)."""
    return Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=SerpAPIClient(api_key=""), detector=MockDetector())


def _afs():
    return [Afirmacao(texto=NUCLEO, nucleo=NUCLEO)]


def _pecas():
    return [{"url": "https://exemplo.com/cafe-cura", "titulo": "Café cura câncer, diz estudo",
             "snippet": "Estudo diz que café cura câncer...", "veiculo": "Blog Exemplo",
             "dominio": "exemplo.com", "corpo": CORPO, "corpo_lido": True,
             "data_pub": "2026-01-01", "cluster": "https://exemplo.com/cafe-cura",
             "curada": False, "confiabilidade": "baixo_trafego"}]


def _fake_avaliar_com_raciocinio(alvo, peca):
    return {"posicao": "SUSTENTA", "citacao": CITACAO, "citacao_score": 1.0,
            "citacao_verificada": True, "pagina_diz": "A página afirma que café cura câncer",
            "raciocinio": RACIOCINIO_LONGO, "motor": "fake", "erro": None,
            "corpo_lido": True}


def _fake_avaliar_sem_raciocinio(alvo, peca):
    saida = _fake_avaliar_com_raciocinio(alvo, peca)
    del saida["raciocinio"]
    return saida


def test_prompt_pede_categoria_e_raciocinio():
    for cat in ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO", "NAO_TRATA"):
        assert cat in juiz_llm.INSTRUCAO
    assert "raciocinio" in juiz_llm.INSTRUCAO
    assert "240" in juiz_llm.INSTRUCAO


def test_avaliar_retorna_raciocinio_sem_corte(monkeypatch):
    monkeypatch.setattr(juiz_llm, "julgar_lote", _fake_julgar_lote)
    saida = avaliador.avaliar(NUCLEO, _peca())
    assert saida["posicao"] == "SUSTENTA"
    assert saida["raciocinio"] == RACIOCINIO_LONGO
    assert len(saida["raciocinio"]) > 240


def test_raciocinio_chega_a_fonte_sem_corte(monkeypatch):
    """Caminho real: avaliar (fake) → _julgar/_normalizar → _fontes, em instância real."""
    monkeypatch.setattr(avaliador, "avaliar", _fake_avaliar_com_raciocinio)
    pipe = _pipe()
    julg, juiz_ok = asyncio.run(pipe._julgar(_afs(), _pecas(), [(0, 0)], usar_llm=True))
    assert juiz_ok is True
    assert julg[(0, 0)]["classe"] == "SUSTENTA"
    assert julg[(0, 0)]["raciocinio"] == RACIOCINIO_LONGO  # _normalizar preservou
    fontes = pipe._fontes(_afs(), _pecas(), julg, SimpleNamespace(votos=[]))
    assert len(fontes) == 1
    assert fontes[0].raciocinio == RACIOCINIO_LONGO
    assert len(fontes[0].raciocinio) > 240


def test_normalizar_sem_raciocinio_vira_vazio(monkeypatch):
    """Perna ausente de _normalizar: sem a chave, vira "" e a Fonte fica None."""
    monkeypatch.setattr(avaliador, "avaliar", _fake_avaliar_sem_raciocinio)
    pipe = _pipe()
    julg, juiz_ok = asyncio.run(pipe._julgar(_afs(), _pecas(), [(0, 0)], usar_llm=True))
    assert juiz_ok is True
    assert julg[(0, 0)]["raciocinio"] == ""
    fontes = pipe._fontes(_afs(), _pecas(), julg, SimpleNamespace(votos=[]))
    assert fontes[0].raciocinio is None
