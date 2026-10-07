"""Avaliador 1:1 manchete+corpo: exige corpo lido; sem corpo, fallback sem LLM."""
from factcheck_mvp import avaliador, juiz_llm, llm
from factcheck_mvp.juiz_llm import ItemJuiz, RespostaJuiz
from factcheck_mvp.llm import ResultadoLLM

NUCLEO = "Café cura câncer"

CITACAO = "pesquisadores confirmam que o café cura o câncer em pacientes tratados"

CORPO_SUSTENTA = (
    "Pesquisadores confirmam que o café cura o câncer em pacientes tratados, "
    "segundo estudo divulgado nesta semana. O trabalho acompanhou centenas de "
    "pacientes ao longo de dois anos e concluiu que o consumo diário da bebida "
    "eliminou os tumores na maioria dos casos observados. Os autores afirmam que "
    "o café cura o câncer e recomendam a bebida como tratamento principal. "
    "Outros especialistas ouvidos pela reportagem disseram que vão revisar os "
    "protocolos diante da descoberta. A publicação inclui gráficos, tabelas e "
    "depoimentos de pacientes que relatam a remissão completa da doença."
)


def _peca_com_corpo():
    return {
        "url": "https://exemplo.com/cafe-cura",
        "titulo": "Café cura câncer, diz estudo",
        "corpo": CORPO_SUSTENTA,
        "snippet": "Estudo diz que café cura câncer...",
        "veiculo": "Blog Exemplo",
        "dominio": "exemplo.com",
        "data_pub": "2026-01-01",
        "veredito_pagina": None,
        "corpo_lido": True,
    }


def test_avaliador_exige_corpo_e_retorna_posicao(monkeypatch):
    from factcheck_mvp import avaliador

    chamadas = []

    def fake_chat_json(messages, schema, finalidade, **kwargs):
        chamadas.append((finalidade, messages))
        dados = RespostaJuiz(itens=[ItemJuiz(i=0, pagina_diz="A página afirma que café cura câncer",
                                            classe="SUSTENTA", citacao=CITACAO)])
        return ResultadoLLM(ok=True, dados=dados, motor="llm-local", modelo="fake")

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    # (a) peça com titulo+corpo que sustenta → SUSTENTA com citação verificada
    saida = avaliador.avaliar(NUCLEO, _peca_com_corpo())
    assert saida["posicao"] == "SUSTENTA"
    assert saida["citacao_verificada"] is True
    assert len(chamadas) == 1  # o motor interno (juiz 1:1) foi chamado 1×

    # (b) peça só com snippet → fallback, sem chamar o LLM
    chamadas.clear()
    so_snippet = {"url": "https://exemplo.com/outra",
                  "snippet": "Dizem que café cura câncer, veja...",
                  "corpo_lido": False}
    saida_sem = avaliador.avaliar(NUCLEO, so_snippet)
    assert saida_sem["posicao"] is None
    assert saida_sem["motor"] == "fallback-sem-corpo"
    assert chamadas == []
    assert juiz_llm.postura_para_relevante(saida_sem["posicao"]) is None


def test_avaliador_encaminha_veredito_pagina_ao_juiz(monkeypatch):
    """Peça selada (veredito/selo_original/afirmacao_checada, sem veredito_pagina)
    tem o item do juiz com veredito_pagina {selo, alegacao_checada} (contrato Task 2)."""
    capturados = []

    def fake_julgar_lote(afirmacao, itens):
        capturados.append((afirmacao, itens))
        return [{"classe": "REFUTA", "citacao": "", "citacao_score": None,
                 "citacao_verificada": None, "pagina_diz": "", "motor": "fake", "erro": None}]

    monkeypatch.setattr(juiz_llm, "julgar_lote", fake_julgar_lote)

    peca = _peca_com_corpo()
    peca.pop("veredito_pagina", None)
    peca.update(veredito="FALSO", selo_original="FALSO",
                afirmacao_checada="Café cura câncer")
    saida = avaliador.avaliar(NUCLEO, peca)
    assert saida["posicao"] == "REFUTA"
    assert len(capturados) == 1
    item = capturados[0][1][0]
    assert item["veredito_pagina"] == {"selo": "FALSO",
                                      "alegacao_checada": "Café cura câncer"}
