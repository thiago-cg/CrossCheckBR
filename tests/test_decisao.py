"""Comportamento de `decisao.decidir` (função pura): direção, clusters, polaridade, selos,
faixas simétricas, indeterminação por falta de evidência e coerência do texto com o nível."""
import pytest

from factcheck_mvp import decisao
from factcheck_mvp.agregador import FRASES_BINARIAS, verificar_neutralidade, titulo_propensao
from factcheck_mvp.decisao import AfirmacaoDecisao, Evidencias, ItemEvidencia, decidir

JUIZ = "llm-juiz:llm-local"


def _it(url, classe, cluster=None, curada=True, corpo=True, veredito=None, motor=JUIZ, af=0,
        citacao=True, veiculo=""):
    return ItemEvidencia(url=url, afirmacao=af, cluster=cluster or url, classe=classe, motor=motor,
                         citacao_verificada=(citacao if (classe != "NAO_TRATA" or citacao is False) else None), curada=curada,
                         corpo_lido=corpo, veredito=veredito,
                         origem_veredito="pagina" if veredito else None, veiculo=veiculo)


def _ev(itens, pol="afirma", **kw):
    return Evidencias(afirmacoes=[AfirmacaoDecisao(texto="x", nucleo="x", polaridade=pol)], itens=itens,
                      n_lidas=len(itens), n_consultadas=len(itens), **kw)


def _tres(classe, **kw):
    return [_it(f"https://{d}/a", classe, cluster=d, **kw) for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]


# ------------------------------------------------------------------ casos centrais
def test_tres_clusters_refutam_alta():
    d = decidir(_ev(_tres("REFUTA")))
    assert d.nivel == "alta" and d.log_odds > decisao.TAU
    assert len(d.votos) == 3 and all(v.direcao == 1 for v in d.votos)


def test_tres_clusters_sustentam_baixa():
    d = decidir(_ev(_tres("SUSTENTA")))
    assert d.nivel == "baixa" and d.log_odds < -decisao.TAU


def test_so_relatos_indeterminada():
    d = decidir(_ev(_tres("RELATA_SEM_ENDOSSO")))
    assert d.nivel == "indeterminada" and not d.votos
    assert "só relatos" in d.motivo


def test_fora_do_tema_indeterminada():
    assert decidir(_ev(_tres("NAO_TRATA"))).nivel == "indeterminada"


def test_sem_juiz_indeterminada_e_fallback_ignorado():
    itens = [_it(f"https://s{i}.com/a", None, motor="fallback-sem-juiz") for i in range(3)]
    d = decidir(_ev(itens, juiz_disponivel=False))
    assert d.nivel == "indeterminada" and "sem julgamento" in d.motivo
    # mesmo que um fallback carregue uma classe, o motor fallback-* é ignorado
    itens = [_it(f"https://s{i}.com/a", "REFUTA", motor="fallback-lexico") for i in range(3)]
    assert decidir(_ev(itens)).nivel == "indeterminada"


def test_sem_nenhuma_fonte_indeterminada():
    d = decidir(_ev([]))
    assert d.nivel == "indeterminada" and d.log_odds == 0


def test_usuario_nega_e_fontes_refutam_o_boato_baixa():
    """'É falso que X': núcleo X refutado por 3 fontes => o que o usuário diz se confirma => baixa."""
    d = decidir(_ev(_tres("REFUTA"), pol="nega"))
    assert d.nivel == "baixa"


def test_usuario_nega_e_fontes_sustentam_o_boato_alta():
    assert decidir(_ev(_tres("SUSTENTA"), pol="nega")).nivel == "alta"


# ------------------------------------------------------------------ selos
def test_selo_verdadeiro_do_fato_ou_fake_baixa():
    """Direção pelo enum, nunca pelo nome do portal ('Fato ou Fake' tem 'fake')."""
    it = _it("https://g1.globo.com/fato-ou-fake/noticia/2024/x.ghtml", "RELATA_SEM_ENDOSSO",
             veredito="VERDADEIRO", veiculo="Fato ou Fake")
    d = decidir(_ev([it]))
    assert d.nivel == "baixa" and d.vereditos_aplicados[0]["veredito"] == "VERDADEIRO"


def test_selo_falso_aplicavel_alta():
    it = _it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO", veiculo="Lupa")
    assert decidir(_ev([it])).nivel == "alta"


def test_selo_de_pagina_fora_do_tema_nao_conta():
    it = _it("https://valorinveste.globo.com/golpes", "NAO_TRATA", veredito="VERDADEIRO")
    d = decidir(_ev([it]))
    assert d.nivel == "indeterminada" and not d.vereditos_aplicados
    assert d.vereditos_ignorados and "não trata" in d.vereditos_ignorados[0]["motivo"]


def test_selo_sem_direcao_satira_nao_vota():
    it = _it("https://boatos.org/x", "RELATA_SEM_ENDOSSO", veredito="SATIRA")
    assert decidir(_ev([it])).nivel == "indeterminada"


def test_selo_e_postura_em_conflito_nao_votam():
    it = _it("https://aosfatos.org/x", "SUSTENTA", veredito="FALSO")
    d = decidir(_ev([it]))
    assert d.nivel == "indeterminada" and d.conflitos


# ------------------------------------------------------------------ independência / pesos
def test_mesmo_cluster_conta_um_voto():
    itens = [_it(f"https://site{i}.com/a", "REFUTA", cluster="agencia:estadao-conteudo") for i in range(4)]
    d = decidir(_ev(itens))
    assert len(d.votos) == 1 and d.nivel == "media"


def test_sinal_isolado_nao_satura():
    d = decidir(_ev([_it("https://g1.globo.com/a", "REFUTA")]))
    assert d.nivel == "media" and 0.5 < d.prob < 0.75


def test_fontes_divididas_media():
    d = decidir(_ev([_it("https://a.com/x", "REFUTA"), _it("https://b.com/x", "SUSTENTA")]))
    assert d.nivel == "media" and d.log_odds == 0


def test_nao_curada_vota_com_peso_menor():
    cur = decidir(_ev([_it("https://a.com/x", "REFUTA", curada=True)]))
    nao = decidir(_ev([_it("https://a.com/x", "REFUTA", curada=False)]))
    assert 0 < nao.log_odds < cur.log_odds
    assert nao.nivel == "media"  # ainda vota


def test_faixas_simetricas():
    for n in range(1, 5):
        for curada in (True, False):
            up = decidir(_ev([_it(f"https://s{i}.com/x", "REFUTA", curada=curada) for i in range(n)]))
            dn = decidir(_ev([_it(f"https://s{i}.com/x", "SUSTENTA", curada=curada) for i in range(n)]))
            assert up.log_odds == -dn.log_odds
            espelho = {"alta": "baixa", "baixa": "alta", "media": "media"}
            assert espelho[up.nivel] == dn.nivel


# ------------------------------------------------------------------ travas incorporadas
def test_vago_indeterminada_mesmo_com_votos():
    assert decidir(_ev(_tres("REFUTA"), vago=True)).nivel == "indeterminada"


def test_opiniao_sem_selo_indeterminada_com_selo_decide():
    assert decidir(_ev(_tres("REFUTA"), opiniao=True)).nivel == "indeterminada"
    it = _it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO")
    assert decidir(_ev([it], opiniao=True)).nivel == "alta"


# ------------------------------------------------------------------ texto do mesmo objeto
@pytest.mark.parametrize("classe,pol", [("REFUTA", "afirma"), ("SUSTENTA", "afirma"),
                                        ("RELATA_SEM_ENDOSSO", "afirma"), ("REFUTA", "nega")])
def test_texto_coerente_com_nivel_e_neutro(classe, pol):
    d = decidir(_ev(_tres(classe), pol=pol))
    j = d.justificativa()
    assert j.startswith(titulo_propensao(d.nivel) + ".")
    assert d.header().endswith(titulo_propensao(d.nivel))
    assert "propensão de ser fake news" in d.header().lower()
    assert not any(f in j.lower() for f in FRASES_BINARIAS)
    assert verificar_neutralidade(j + " " + d.why_1linha()) == []


def test_recibo_conta_fontes_lidas_e_fora_do_tema():
    itens = _tres("NAO_TRATA") + [_it("https://x.com/a", "NAO_TRATA", citacao=False)]
    d = decidir(_ev(itens))
    assert d.contagem["fora_do_tema"] == 4 and d.contagem["citacao_invalida"] == 1
    assert "4 fora do tema" in d.justificativa() and "Lidas 4" in d.justificativa()


# ------------------------------------------------------------------ mutação
CASOS = [
    (lambda: _ev(_tres("REFUTA")), "alta"),
    (lambda: _ev(_tres("SUSTENTA")), "baixa"),
    (lambda: _ev(_tres("REFUTA"), pol="nega"), "baixa"),
    (lambda: _ev([_it("https://g1.globo.com/f", "RELATA_SEM_ENDOSSO", veredito="VERDADEIRO")]), "baixa"),
    (lambda: _ev([_it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO")]), "alta"),
]


def test_mutacao_inverter_direcao_quebra_as_expectativas(monkeypatch):
    """Se a direção em decidir for invertida, os casos centrais têm que falhar."""
    assert all(decidir(f()).nivel == esp for f, esp in CASOS)
    original = decisao._contribuicoes

    def invertida(it, s, dec):
        return [(-v, d) for v, d in original(it, s, dec)]
    monkeypatch.setattr(decisao, "_contribuicoes", invertida)
    assert all(decidir(f()).nivel != esp for f, esp in CASOS)


def test_mutacao_ignorar_polaridade_quebra():
    ev = _ev(_tres("REFUTA"), pol="nega")
    ev.afirmacoes[0].polaridade = "afirma"
    assert decidir(ev).nivel == "alta"  # sem a polaridade o caso de negação vira erro grave
