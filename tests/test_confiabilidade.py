"""Confiabilidade da fonte: catálogo > rede social > instituição > tráfego (Tranco)."""
import pytest

from factcheck_mvp import confiabilidade as c
from factcheck_mvp.decisao import Evidencias, AfirmacaoDecisao, ItemEvidencia, decidir

JUIZ = "llm-juiz:teste"


@pytest.mark.parametrize("url,curada,esperado", [
    ("https://www.agencialupa.org/x", True, c.CURADA),          # pouco tráfego global, mas curada
    ("https://www.agencialupa.org/x", False, c.BAIXO_TRAFEGO),  # mesma URL sem curadoria
    ("https://www.instagram.com/p/1", False, c.PLATAFORMA),     # top 20 mundial, conteúdo de usuário
    ("https://www.instagram.com/p/1", True, c.CURADA),          # curadoria sempre vence
    ("https://fulano.blogspot.com/post", False, c.PLATAFORMA),
    ("https://translate.google.com/?x", False, c.PLATAFORMA),
    ("https://www.gov.br/saude/x", False, c.INSTITUCIONAL),
    ("https://saude.sp.gov.br/x", False, c.INSTITUCIONAL),
    ("https://www12.senado.leg.br/noticias/x", False, c.INSTITUCIONAL),
    ("https://www.terra.com.br/noticias/x", False, c.ALTO_TRAFEGO),
    ("https://um-blog-que-ninguem-acessa-123.com.br/x", False, c.BAIXO_TRAFEGO),
])
def test_classificar(url, curada, esperado):
    assert c.classificar(url, curada) == esperado


def test_corte_de_trafego_configuravel(monkeypatch):
    pos = c.posicao("https://www.terra.com.br/x")
    assert pos is not None
    monkeypatch.setattr(c.config, "TRAFEGO_RANK_MAX", pos - 1)
    assert c.classificar("https://www.terra.com.br/x") == c.BAIXO_TRAFEGO


def test_metadados_citam_a_lista_tranco():
    m = c.metadados()
    assert m.get("lista") and m.get("dominios", 0) > 0


def test_fatores_preservam_o_que_ja_valia():
    # curada/instituição/muito acessada: os mesmos pesos de antes da mudança
    assert c.FATOR_POSTURA[c.CURADA] == 1.0
    assert c.FATOR_POSTURA[c.INSTITUCIONAL] == c.FATOR_POSTURA[c.ALTO_TRAFEGO] == 0.6
    assert c.FATOR_POSTURA[c.PLATAFORMA] < 0.6 and c.FATOR_POSTURA[c.BAIXO_TRAFEGO] < 0.6


def _ev(itens):
    return Evidencias(afirmacoes=[AfirmacaoDecisao(texto="x", nucleo="x")], itens=itens)


def _it(url, classe="SUSTENTA", nivel=None):
    return ItemEvidencia(url=url, cluster=url, classe=classe, motor=JUIZ, citacao_verificada=True,
                         corpo_lido=True, confiabilidade=nivel)


def test_redes_sociais_sozinhas_nao_cravam_o_nivel():
    # Caso real (Chico César): 3 posts de redes sociais "confirmando" um boato davam BAIXA.
    posts = [_it(f"https://www.instagram.com/p/{i}", nivel=c.PLATAFORMA) for i in range(5)]
    d = decidir(_ev(posts))
    assert d.nivel == "media" and d.travas["sem_fonte_confiavel"]  # muitos posts: ainda média
    assert "redes sociais ou sites pouco acessados" in d.justificativa()
    # basta 1 fonte confiável no mesmo sentido para o nível poder subir
    d2 = decidir(_ev(posts + [_it("https://www.terra.com.br/x", nivel=c.ALTO_TRAFEGO)]))
    assert d2.nivel == "baixa" and not d2.travas["sem_fonte_confiavel"]
    veiculos = [_it(f"https://v{i}.com/x", nivel=c.ALTO_TRAFEGO) for i in range(3)]
    assert decidir(_ev(veiculos)).nivel == "baixa"


def test_nivel_ausente_e_calculado_pela_url():
    it = ItemEvidencia(url="https://www.tiktok.com/v/1", classe="REFUTA", motor=JUIZ, corpo_lido=True)
    assert it.nivel_confiabilidade() == c.PLATAFORMA


def test_selecao_do_juiz_prioriza_fonte_confiavel():
    from factcheck_mvp.pipeline import Pipeline
    from factcheck_mvp.schemas import Afirmacao

    pecas = [{"url": "https://www.instagram.com/p/1", "titulo": "vacina causa autismo", "afs": {0},
              "confiabilidade": c.PLATAFORMA},
             {"url": "https://www.terra.com.br/x", "titulo": "vacina causa autismo", "afs": {0},
              "confiabilidade": c.ALTO_TRAFEGO}]
    pipe = Pipeline.__new__(Pipeline)
    sel = pipe._selecionar([Afirmacao(texto="vacina causa autismo")], pecas)
    assert [pecas[i]["url"] for i, _ in sel][0] == "https://www.terra.com.br/x"
    assert len(sel) == 2  # nada é descartado: só a ordem muda
