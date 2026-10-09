"""Contratos + neutralidade da redação (RF12/RNF02)."""
import pytest

from factcheck_mvp.agregador import EXPRESSOES_PROIBIDAS, FRASES_BINARIAS, verificar_neutralidade
from factcheck_mvp.decisao import AfirmacaoDecisao, Evidencias, ItemEvidencia, decidir
from factcheck_mvp.schemas import Afirmacao, EntradaConsulta, FonteEvidencia, RelatorioChecagem


def test_entrada_rejeita_vazia():
    with pytest.raises(Exception):
        EntradaConsulta(tipo="texto", conteudo="ok")


def test_entrada_tipos():
    for tipo in ("texto", "titulo", "link"):
        assert EntradaConsulta(tipo=tipo, conteudo="conteúdo válido aqui").tipo == tipo


def test_afirmacao_compat_e_alvo():
    a = Afirmacao(texto="Café não cura câncer", nucleo="Café cura câncer", polaridade="nega")
    assert a.alvo() == "Café cura câncer"
    assert Afirmacao(texto="X acontece").alvo() == "X acontece" and Afirmacao(texto="y").polaridade == "afirma"


def _dec(classe, n, veredito=None, pol="afirma"):
    itens = [ItemEvidencia(url=f"https://s{i}.com/x", cluster=f"s{i}", classe=classe, motor="llm-juiz:x",
                           curada=True, corpo_lido=True, veredito=veredito, veiculo="Fato ou Fake")
             for i in range(n)]
    return decidir(Evidencias(afirmacoes=[AfirmacaoDecisao(texto="x", polaridade=pol)], itens=itens))


@pytest.mark.parametrize("dec", [_dec("REFUTA", 3), _dec("SUSTENTA", 3), _dec("REFUTA", 1, "FALSO"),
                                 _dec("RELATA_SEM_ENDOSSO", 2, "VERDADEIRO"), _dec("NAO_TRATA", 2),
                                 _dec("REFUTA", 2, pol="nega")])
def test_justificativa_nunca_binaria_nem_opinativa(dec):
    texto = f"{dec.justificativa()} {dec.why_1linha()} {dec.header()}".lower()
    assert not any(f in texto for f in FRASES_BINARIAS), texto
    assert verificar_neutralidade(texto) == [] and not any(e in texto for e in EXPRESSOES_PROIBIDAS)


def test_relatorio_serializa_com_decisao_e_postura():
    dec = _dec("REFUTA", 3)
    rel = RelatorioChecagem(propensao=dec.nivel, justificativa=dec.justificativa(),
                            consulta=EntradaConsulta(tipo="texto", conteudo="algum texto aqui"),
                            fontes=[FonteEvidencia(url="https://a", postura="REFUTA", citacao="x",
                                                   citacao_verificada=True, relevante=True)],
                            decisao=dec.to_dict())
    d = rel.model_dump()
    assert d["versao"].startswith("mvp-") and d["decisao"]["nivel"] == "alta"
    assert d["fontes"][0]["postura"] == "REFUTA"


# --- E4 revisão (itens 6 e 9): data_referencia estrita; link sem data = referência explicitamente ausente ---
@pytest.mark.parametrize("valor", ["2026-1-9", "2026-10-09xyz", "2026-02-30", "09/10/2026", "2026-10-9",
                                   " 2026-10-09", "2026-10-09T00:00:00"])
def test_data_referencia_rejeita_o_que_nao_e_yyyy_mm_dd(valor):
    with pytest.raises(ValueError):  # pydantic.ValidationError é subclasse de ValueError
        EntradaConsulta(tipo="texto", conteudo="fato de hoje", data_referencia=valor)


def test_data_referencia_aceita_yyyy_mm_dd():
    assert EntradaConsulta(tipo="texto", conteudo="fato de hoje", data_referencia="2026-10-09").data_referencia == "2026-10-09"


def test_sem_referencia_temporal_e_exclusivo_com_data_e_padrao_false():
    e = EntradaConsulta(tipo="texto", conteudo="texto de página sem data")
    assert e.sem_referencia_temporal is False and e.data_referencia is None
    e2 = EntradaConsulta(tipo="texto", conteudo="texto de página sem data", sem_referencia_temporal=True)
    assert e2.sem_referencia_temporal is True and e2.data_referencia is None
    with pytest.raises(ValueError):
        EntradaConsulta(tipo="texto", conteudo="texto", data_referencia="2026-10-09", sem_referencia_temporal=True)
