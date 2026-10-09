"""Comportamento de `decisao.decidir` (função pura): direção, clusters, polaridade, selos,
faixas simétricas, indeterminação por falta de evidência e coerência do texto com o nível."""
import math

import pytest

from factcheck_mvp import confiabilidade, decisao
from factcheck_mvp.agregador import FRASES_BINARIAS, verificar_neutralidade, titulo_propensao
from factcheck_mvp.decisao import AfirmacaoDecisao, Evidencias, ItemEvidencia, decidir

JUIZ = "llm-juiz:llm-local"


def _it(url, classe, cluster=None, curada=True, corpo=True, veredito=None, motor=JUIZ, af=0,
        citacao=True, veiculo="", origem=None, pf=None):
    return ItemEvidencia(url=url, afirmacao=af, cluster=cluster or url, classe=classe, motor=motor,
                         citacao_verificada=(citacao if (classe != "NAO_TRATA" or citacao is False) else None), curada=curada,
                         corpo_lido=corpo, veredito=veredito,
                         origem_veredito=origem or ("pagina" if veredito else None), veiculo=veiculo,
                         prob_fake_pagina=pf)


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
    """Direção pelo enum, nunca pelo nome do portal ('Fato ou Fake' tem 'fake').

    T6: só o selo do ÍNDICE vota (origem_veredito="indice", via E1/aplicabilidade);
    selo de página (template/JSON-LD do próprio portal) não vota.
    """
    it = _it("https://g1.globo.com/fato-ou-fake/noticia/2024/x.ghtml", "RELATA_SEM_ENDOSSO",
             veredito="VERDADEIRO", veiculo="Fato ou Fake", origem="indice")
    d = decidir(_ev([it]))
    assert d.nivel == "baixa" and d.vereditos_aplicados[0]["veredito"] == "VERDADEIRO"


def test_selo_falso_aplicavel_alta():
    it = _it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO", veiculo="Lupa", origem="indice")
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
    it = _it("https://aosfatos.org/x", "SUSTENTA", veredito="FALSO", origem="indice")
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
    it = _it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO", origem="indice")
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


# ------------------------------------------------------------------ juiz-agregador (Task 4)
def test_juiz_agrega_avaliacoes():
    """Juiz-agregador sobre outputs do avaliador 1:1 (manchete+corpo).

    Replica o wiring do pipeline (`ItemEvidencia.classe = julg posicao`,
    `corpo_lido` da peça, `citacao_verificada` do avaliador): 2 clusters
    REFUTA curados com citação verificada e corpo lido → alta; só
    RELATA/NAO_TRATA (0 posturas) → indeterminada ("nenhuma fonte ...
    confirma ou contesta").
    """
    refutas = [_it(f"https://{d}/a", "REFUTA", cluster=d, curada=True, corpo=True, citacao=True)
               for d in ("g1.globo.com", "estadao.com.br")]
    d = decidir(_ev(refutas))
    assert d.nivel == "alta" and len(d.votos) == 2
    assert all(v.direcao == 1 for v in d.votos)

    so_relatos = [_it("https://g1.globo.com/b", "RELATA_SEM_ENDOSSO", corpo=True),
                  _it("https://estadao.com.br/c", "NAO_TRATA", corpo=True)]
    d2 = decidir(_ev(so_relatos))
    assert d2.nivel == "indeterminada" and not d2.votos
    assert "nenhuma fonte" in d2.motivo and "confirma ou contesta" in d2.motivo


# ------------------------------------------------------------------ mutação
CASOS = [
    (lambda: _ev(_tres("REFUTA")), "alta"),
    (lambda: _ev(_tres("SUSTENTA")), "baixa"),
    (lambda: _ev(_tres("REFUTA"), pol="nega"), "baixa"),
    (lambda: _ev([_it("https://g1.globo.com/f", "RELATA_SEM_ENDOSSO", veredito="VERDADEIRO",
                       origem="indice")]), "baixa"),
    (lambda: _ev([_it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO",
                       origem="indice")]), "alta"),
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


# ------------------------------------------------------------------ E3: título não vota
def test_e3_so_titulo_nao_vota_nem_com_selo():
    itens = [_it(f"https://{d}/a", "REFUTA", cluster=d, corpo=False, veredito="FALSO")
             for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    d = decidir(_ev(itens))
    assert d.nivel == "indeterminada" and not d.votos
    assert len(d.nao_analisadas) == 3 and not d.vereditos_aplicados
    assert "lidas integralmente" in d.motivo


def test_e3_lida_vota_e_so_titulo_fica_listada():
    lida = _it("https://g1.globo.com/a", "REFUTA", cluster="g1")
    titulo = _it("https://bbc.com/a", "REFUTA", cluster="bbc", corpo=False)
    d = decidir(_ev([lida, titulo]))
    assert [v.urls for v in d.votos] == [["https://g1.globo.com/a"]]
    assert [n["url"] for n in d.nao_analisadas] == ["https://bbc.com/a"]


# ------------------------------------------------------------------ E4: relevância temporal
def _ev_hoje(itens, texto="Bolsonaro recebeu alta do hospital hoje"):
    return Evidencias(afirmacoes=[AfirmacaoDecisao(texto=texto, nucleo=texto)], itens=itens,
                      texto_usuario=texto, data_referencia="2026-10-09")


def test_e4_relevancia_temporal_contrato():
    assert decisao.relevancia_temporal(0, 2) == pytest.approx(1.0)
    rs = [decisao.relevancia_temporal(e, 2) for e in (1, 5, 30, 2000)]
    assert all(0.0 <= r <= 1.0 for r in rs) and rs == sorted(rs, reverse=True)
    assert rs[-1] < 0.05  # anos depois: praticamente outro episódio


def test_e4_fonte_antiga_que_confirma_nao_crava_baixa():
    """Regressão do caso Bolsonaro: checagens antigas confirmando 'recebeu alta' (de outra
    internação) não podem cravar baixa para um fato apresentado como de hoje."""
    itens = [
        ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="SUSTENTA", motor=JUIZ,
                      citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-07-18")
        for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    d = decidir(_ev_hoje(itens))
    assert d.nivel != "baixa"
    assert d.travas["data_incompativel"] and len(d.descontos_temporais) == 3
    assert all(x["bits_descartados"] > 0 for x in d.descontos_temporais)


def test_e4_simetrico_nao_eleva_propensao():
    """Fonte antiga que CONTESTA perde o mesmo tanto que a que confirma: E4 nunca empurra
    o nível para cima por conta própria."""
    def _um(classe):
        it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe=classe, motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2026-09-01")
        return decidir(_ev_hoje([it])).log_odds
    assert _um("REFUTA") == pytest.approx(-_um("SUSTENTA"))
    assert abs(_um("REFUTA")) < decisao.W_POSTURA


def test_e4_dentro_da_janela_ou_sem_marcador_sem_desconto():
    it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="REFUTA", motor=JUIZ,
                       citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2026-10-08")
    assert not decidir(_ev_hoje([it])).descontos_temporais
    it.data_pub = "2019-01-01"
    assert not decidir(_ev_hoje([it], texto="Café cura câncer")).descontos_temporais


def test_e4_desconto_e_mistura_de_razoes_de_verossimilhanca():
    assert decisao._descontar(1.5, 1.0) == 1.5
    assert decisao._descontar(1.5, 0.0) == pytest.approx(0.0)
    assert decisao._descontar(-1.5, 0.5) == pytest.approx(-math.log(0.5 * math.exp(1.5) + 0.5))


def test_e4_direcao_e_bits_medidos_antes_do_desconto(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.0)
    it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="REFUTA", motor=JUIZ,
                       citacao_verificada=True, curada=True, corpo_lido=True,
                       veredito="FALSO", origem_veredito="indice", data_pub="2021-01-01")
    d = decidir(_ev_hoje([it]))
    x = d.descontos_temporais[0]
    assert x["direcao"] == 1
    assert x["bits_descartados"] == pytest.approx(decisao.W_VEREDITO / math.log(2), rel=1e-3)


def test_e4_desconto_carrega_data_bruta_e_precisao(monkeypatch):
    """A telemetria `fonte estagio=data` lê a bruta e a precisão do item descontado (antes: None)."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="REFUTA", motor=JUIZ,
                       citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-12-31",
                       data_pub_bruta="2021-01-01", data_pub_precisao="ano")
    x = decidir(_ev_hoje([it])).descontos_temporais[0]
    assert (x["data_pub"], x["data_pub_bruta"], x["data_pub_precisao"]) == ("2021-12-31", "2021-01-01", "ano")


def test_e4_tudo_descontado_motivo_fala_de_periodo(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.0)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="REFUTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-01-01")
             for d in ("g1.globo.com", "bbc.com")]
    d = decidir(_ev_hoje(itens))
    assert d.nivel == "indeterminada"
    assert "outro episódio" in d.motivo and "se anulam" not in d.motivo


def test_e4_motivo_so_quando_o_desconto_muda_o_nivel(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    recente = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="REFUTA", motor=JUIZ,
                             citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2026-10-08")
               for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    antiga = ItemEvidencia(url="https://uol.com.br/a", cluster="uol", classe="REFUTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-01-01")
    d = decidir(_ev_hoje(recente + [antiga]))
    assert d.nivel == d.nivel_sem_desconto == "alta"
    assert d.travas["data_incompativel"] and "outro episódio" not in d.motivo


def test_e4_contrafactual_nunca_menor_em_modulo(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.3)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe=c, motor=JUIZ, citacao_verificada=True,
                           curada=True, corpo_lido=True, data_pub=dp)
             for d, c, dp in (("g1.globo.com", "REFUTA", "2021-01-01"), ("bbc.com", "REFUTA", "2026-10-09"))]
    d = decidir(_ev_hoje(itens))
    assert abs(d.log_odds) <= abs(d.log_odds_sem_desconto)


# ------------------------------------------------------------------ Task 4: janela por afirmação
def test_e4_decidir_desconto_por_afirmacao_nao_vaza():
    """Só a afirmação com marcador próprio é descontada; a sem marcador (n=2) não,
    mesmo com 'hoje' no texto_usuario (compat: snapshot antigo de 1 afirmação sem
    janela cai para janela_temporal do texto)."""
    from factcheck_mvp import aplicabilidade
    t = "A ponte caiu hoje. A obra custou 2 bilhões."
    j0 = aplicabilidade.janela_da_afirmacao("A ponte caiu hoje", t, 2)
    j1 = aplicabilidade.janela_da_afirmacao("A obra custou 2 bilhões", t, 2)
    assert (j0, j1) == (2, None)
    itens = [
        ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="SUSTENTA", motor=JUIZ,
                      citacao_verificada=True, curada=True, corpo_lido=True,
                      data_pub="2021-01-01", afirmacao=0),
        ItemEvidencia(url="https://bbc.com/b", cluster="bbc", classe="SUSTENTA", motor=JUIZ,
                      citacao_verificada=True, curada=True, corpo_lido=True,
                      data_pub="2021-01-01", afirmacao=1),
    ]
    ev = Evidencias(
        afirmacoes=[AfirmacaoDecisao(texto="A ponte caiu hoje", nucleo="ponte", janela=j0),
                    AfirmacaoDecisao(texto="A obra custou 2 bilhões", nucleo="obra", janela=j1)],
        itens=itens, texto_usuario=t, data_referencia="2026-10-09")
    d = decidir(ev)
    assert {x["afirmacao"] for x in d.descontos_temporais} == {0}

    # snapshot antigo (sem janela, 1 afirmação): mantém o desconto pelo texto
    ev_antigo = _ev_hoje([ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="SUSTENTA",
                                        motor=JUIZ, citacao_verificada=True, curada=True,
                                        corpo_lido=True, data_pub="2021-01-01")])
    assert decidir(ev_antigo).descontos_temporais


# ------------------------------------------------------------------ Task 6: superfície ao usuário (neutra)
def test_e4_aviso_chega_ao_usuario_e_e_neutro(monkeypatch):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="SUSTENTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-07-18")
             for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    d = decidir(_ev_hoje(itens))
    texto = d.header() + " " + d.why_1linha() + " " + d.justificativa()
    assert "outro episódio" in d.justificativa() and "notícia antiga recirculando" in texto
    assert verificar_neutralidade(texto) == []


def test_e4_bot_mostra_data_quando_descontada_e_neutro():
    from factcheck_mvp.schemas import EntradaConsulta, FonteEvidencia, RelatorioChecagem
    from factcheck_mvp.telegram_bot import formatar
    f = FonteEvidencia(url="https://g1.globo.com/a", titulo="Titulo", portal_nome="g1",
                       corpo_lido=True, relevante=True, postura="SUSTENTA",
                       data_pub="2021-07-18", relevancia_temporal=0.05)
    rel = RelatorioChecagem(
        propensao="media", justificativa="Propensão média de ser fake news.",
        consulta=EntradaConsulta(tipo="texto", conteudo="Texto sobre fato de hoje aqui"),
        fontes=[f],
        decisao={"votos": [], "descontos_temporais": [{"url": f.url, "r": 0.05}]})
    saida = formatar(rel)
    assert "📅" in saida
    assert verificar_neutralidade(saida) == []
    assert len(saida) <= 3900


# ------------------------------------------------------------------ T6 (B1c): BERTimbau × avaliador
def test_t6_pagina_que_bert_acha_fake_quase_nao_vota():
    """D1: o BERTimbau mede a CREDIBILIDADE da página; a direção vem do avaliador.
    Peso da postura = W_POSTURA·f_fonte·(1 − prob_fake_pagina): com prob_fake 0,9 a mesma
    REFUTA pesa no máximo 0,1 da de prob_fake 0."""
    limpa = decidir(_ev([_it("https://g1.globo.com/a", "REFUTA", pf=0.0)]))
    fake = decidir(_ev([_it("https://g1.globo.com/a", "REFUTA", pf=0.9)]))
    assert limpa.votos[0].direcao == fake.votos[0].direcao == 1
    assert 0 < abs(fake.votos[0].valor) <= 0.1 * abs(limpa.votos[0].valor) + 1e-9
    assert abs(fake.log_odds) <= 0.1 * abs(limpa.log_odds) + 1e-9


def test_t6_prob_fake_limitada_entre_0_e_1():
    """Fora de [0, 1] é limitada: 1,7 vira 1 (a postura não vota); −0,5 vira 0 (fator 1)."""
    assert decidir(_ev([_it("https://g1.globo.com/a", "REFUTA", pf=1.7)])).votos == []
    assert decidir(_ev([_it("https://g1.globo.com/a", "REFUTA", pf=-0.5)])).log_odds == 1.0


def test_t6_sem_bert_identico_ao_comportamento_anterior():
    """Regressão: prob_fake_pagina None (snapshot antigo, sem BERT) dá fator 1,0 e o mesmo
    log_odds de antes; prob_fake 0,0 dá o mesmo valor. Fonte curada: W_POSTURA·1,0 = 1,0."""
    sem = decidir(_ev([_it("https://g1.globo.com/a", "REFUTA", pf=None)]))
    zero = decidir(_ev([_it("https://g1.globo.com/a", "REFUTA", pf=0.0)]))
    assert sem.log_odds == zero.log_odds == 1.0
    assert sem.votos[0].valor == zero.votos[0].valor == 1.0


def test_t6_selo_de_pagina_sem_indice_nao_vota():
    """Selo ClaimReview extraído da PÁGINA (template/JSON-LD do portal) não entra em
    vereditos_aplicados: vai para vereditos_ignorados, não gera conflito e a postura segue
    votando sozinha."""
    it = _it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO", origem="pagina")
    d = decidir(_ev([it]))
    assert not d.vereditos_aplicados and not d.conflitos
    assert [v["veredito"] for v in d.vereditos_ignorados] == ["FALSO"]
    assert "não vota" in d.vereditos_ignorados[0]["motivo"]
    assert [v.motivo for v in d.votos] == ["postura REFUTA"] and d.log_odds == 1.0


def test_t6_selo_de_pagina_contra_postura_nao_gera_conflito():
    it = _it("https://aosfatos.org/x", "SUSTENTA", veredito="FALSO", origem="pagina")
    d = decidir(_ev([it]))
    assert not d.conflitos and not d.vereditos_aplicados
    assert d.log_odds == -1.0


def test_t6_selo_de_indice_continua_votando():
    it = _it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO", origem="indice")
    d = decidir(_ev([it]))
    assert d.nivel == "alta"
    assert [v["veredito"] for v in d.vereditos_aplicados] == ["FALSO"]
    assert not d.vereditos_ignorados


def test_t6_origem_ausente_vota_como_antes():
    """origem_veredito None não é 'página': mantém o comportamento anterior (o selo vota)."""
    it = ItemEvidencia(url="https://lupa.uol.com.br/x", cluster="lupa", classe="REFUTA", motor=JUIZ,
                       curada=True, corpo_lido=True, veredito="FALSO", origem_veredito=None)
    d = decidir(_ev([it]))
    assert d.nivel == "alta" and d.vereditos_aplicados


def test_t6_formula_registrada_em_parametros():
    d = decidir(_ev([_it("https://g1.globo.com/a", "REFUTA")]))
    assert d.parametros["postura"] == "W_POSTURA·f_fonte·(1−prob_fake_pagina)"


# ------------------------------------------------------------------ T7 (B2): conjunção entre afirmações (D2)
def _it_nivel(url, classe, nivel, af=0, cluster=None):
    """Item com nível de confiabilidade explícito (pesos exatos: curada 1,0; institucional/alto 0,6; baixo 0,3)."""
    return ItemEvidencia(url=url, afirmacao=af, cluster=cluster or url, classe=classe, motor=JUIZ,
                         citacao_verificada=True, curada=False, corpo_lido=True, confiabilidade=nivel)


def _ev_afs(itens, textos, pols=None, nucleos=None, **kw):
    pols = pols or ["afirma"] * len(textos)
    nucleos = nucleos or list(textos)
    afs = [AfirmacaoDecisao(texto=t, nucleo=n, polaridade=p) for t, n, p in zip(textos, nucleos, pols)]
    return Evidencias(afirmacoes=afs, itens=itens, n_lidas=len(itens), n_consultadas=len(itens), **kw)


def _sig_ref(x):
    return 1.0 / (1.0 + math.exp(-x))


def _conj_ref(valores):
    """Referência da fórmula sem atalhos: L = logit(1 − Π(1 − σ(L_a)))."""
    p = 1.0 - math.prod(1.0 - _sig_ref(v) for v in valores)
    return math.log(p / (1.0 - p))


def test_t7_contestada_e_confirmada_dao_alta_e_justificativa_cita_as_duas():
    """D2 (a): A contestada (L=+2) e B confirmada (L=-5) => alta; justificativa e why citam as duas partes."""
    itens = [_it(f"https://{d}/a", "REFUTA", cluster=d, af=0) for d in ("g1.globo.com", "estadao.com.br")]
    itens += [_it(f"https://{d}/b", "SUSTENTA", cluster=d, af=1)
              for d in ("bbc.com", "folha.uol.com.br", "agenciabrasil.ebc.com.br", "aosfatos.org", "lupa.uol.com.br")]
    d = decidir(_ev_afs(itens, ["A ponte caiu em 2024", "A obra custou 2 bilhões"]))
    assert d.por_afirmacao[0]["L"] == pytest.approx(2.0) and d.por_afirmacao[1]["L"] == pytest.approx(-5.0)
    assert d.nivel == "alta" and d.parametros["combinacao"].startswith("conjuncao")
    j = d.justificativa()
    assert '"A ponte caiu em 2024"' in j and '"A obra custou 2 bilhões"' in j
    assert "contestam" in j and "é confirmada por fontes" in j
    assert '"A ponte caiu em 2024"' in d.why_1linha() and '"A obra custou 2 bilhões"' in d.why_1linha()
    assert verificar_neutralidade(f"{d.header()} {d.why_1linha()} {j}") == []


def test_t7_sem_parte_confirmada_nao_cita_partes():
    """Alta só com partes contestadas: a frase de duas partes não aparece."""
    itens = [_it(f"https://{d}/a", "REFUTA", cluster=d, af=0) for d in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    d = decidir(_ev_afs(itens, ["A ponte caiu em 2024"]))
    assert d.nivel == "alta" and "é confirmada por fontes" not in d.justificativa()


def test_t7_parte_falsa_abaixo_do_tau_com_parte_verdadeira_forte_nao_baixa():
    """D2 (b), caso cr_lula_acabar_bets_que_criou: parte falsa fraca (L=0,9 < τ) com parte verdadeira
    forte (L=-2,3). Com o máximo |L| dava baixa (erro grave); com a conjunção, média."""
    itens = [_it_nivel("https://a1.gov.br/x", "REFUTA", confiabilidade.INSTITUCIONAL, af=0),
             _it_nivel("https://a2.com.br/x", "REFUTA", confiabilidade.BAIXO_TRAFEGO, af=0),
             _it("https://b1.com/x", "SUSTENTA", cluster="b1", af=1),
             _it("https://b2.com/x", "SUSTENTA", cluster="b2", af=1),
             _it_nivel("https://b3.com/x", "SUSTENTA", confiabilidade.BAIXO_TRAFEGO, af=1)]
    d = decidir(_ev_afs(itens, ["Parte falsa", "Parte verdadeira"]))
    assert d.por_afirmacao[0]["L"] == pytest.approx(0.9) and d.por_afirmacao[1]["L"] == pytest.approx(-2.3)
    assert d.nivel == "media" and 0 < d.log_odds < decisao.TAU


def test_t7_formula_da_conjuncao_com_os_numeros_do_spec():
    """Números do spec: (b) p=0,70 e p=0,10 => p_texto=0,73 (media); (a) A=+2, B=-5 (alta)."""
    valores = [math.log(0.7 / 0.3), math.log(0.1 / 0.9)]
    assert decisao._combinar_afirmacoes(valores) == pytest.approx(_conj_ref(valores), abs=1e-9)
    assert _conj_ref(valores) == pytest.approx(math.log(0.73 / 0.27), abs=1e-9)
    assert decisao.nivel_de(decisao._combinar_afirmacoes(valores)) == "media"
    assert decisao._combinar_afirmacoes([2.0, -5.0]) == pytest.approx(_conj_ref([2.0, -5.0]), abs=1e-9)
    assert decisao.nivel_de(decisao._combinar_afirmacoes([2.0, -5.0])) == "alta"


@pytest.mark.parametrize("valor", [3.0, -2.0, 0.9, -0.45, 0.0])
def test_t7_uma_afirmacao_devolve_o_mesmo_L(valor):
    """Com uma afirmação, σ e logit se cancelam: o L é exatamente o de antes."""
    assert decisao._combinar_afirmacoes([valor]) == valor
    assert decisao._combinar_afirmacoes([]) == 0.0


@pytest.mark.parametrize("valor", [-4.0, -1.2, 0.3, 1.0, 2.5, 4.0])
def test_t7_ramo_geral_reproduz_a_unica_parte_quando_a_outra_nao_pesa(valor):
    """O caminho geral (produto + logit) bate com o L da parte única se a outra é fortemente confirmada."""
    assert decisao._combinar_afirmacoes([valor, -40.0]) == pytest.approx(valor, abs=1e-6)


@pytest.mark.parametrize("valores", [[2.0, -5.0], [0.9, -2.3], [1.0, 1.0], [-3.0, -0.2], [0.5, 0.0, -4.0]])
def test_t7_combinacao_nunca_fica_abaixo_da_parte_mais_falsa(valores):
    """Parte falsa não some por causa de parte verdadeira: L >= max(L_a)."""
    assert decisao._combinar_afirmacoes(valores) >= max(valores) - 1e-9


def test_t7_uma_afirmacao_no_decidir_mantem_o_L_de_antes():
    d = decidir(_ev(_tres("REFUTA")))
    assert d.log_odds == 3.0 and d.log_odds == d.por_afirmacao[0]["L"]
    d2 = decidir(_ev([_it_nivel("https://a1.gov.br/x", "SUSTENTA", confiabilidade.INSTITUCIONAL),
                      _it_nivel("https://a2.com.br/x", "SUSTENTA", confiabilidade.BAIXO_TRAFEGO)]))
    assert d2.log_odds == -0.9 and d2.nivel == "media"


def test_t7_afirmacao_sem_voto_fica_fora_do_produto():
    """Ausência de evidência não é evidência: B sem voto (relato / fora do tema) não muda A (confirmada, L=-2)."""
    so_a = [_it(f"https://{d}/a", "SUSTENTA", cluster=d, af=0) for d in ("g1.globo.com", "estadao.com.br")]
    sem_voto = [_it("https://bbc.com/b", "RELATA_SEM_ENDOSSO", af=1), _it("https://uol.com.br/c", "NAO_TRATA", af=1)]
    base = decidir(_ev_afs(so_a, ["A", "B"]))
    com = decidir(_ev_afs(so_a + sem_voto, ["A", "B"]))
    assert base.log_odds == com.log_odds == -2.0
    assert base.nivel == com.nivel == "baixa"
    assert com.por_afirmacao[1]["n_votos"] == 0


def test_t7_afirmacao_com_votos_que_se_anulam_tem_voto_e_entra_no_produto():
    """Votos que se anulam (L=0) não são 'sem voto': a parte entra com p=0,5 (critério n_votos > 0)."""
    itens = [_it("https://g1.globo.com/a", "SUSTENTA", cluster="g1", af=0),
             _it("https://estadao.com.br/a", "REFUTA", cluster="estadao", af=0)]
    itens += [_it(f"https://{d}/b", "SUSTENTA", cluster=d, af=1) for d in ("bbc.com", "folha.uol.com.br")]
    d = decidir(_ev_afs(itens, ["A", "B"]))
    assert d.por_afirmacao[0]["L"] == 0 and d.por_afirmacao[0]["n_votos"] == 2
    assert d.log_odds == pytest.approx(_conj_ref([0.0, -2.0]), abs=1e-4)


def test_t7_contrafactual_usa_votos_brutos_quando_o_desconto_zera_uma_afirmacao(monkeypatch):
    """O desconto temporal zera os votos de A (r=0): no caminho com desconto A fica sem voto; no
    contrafactual (sem desconto) A entra com os votos brutos (L=+2) e o nível seria alta."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.0)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="REFUTA", motor=JUIZ, citacao_verificada=True,
                           curada=True, corpo_lido=True, data_pub="2021-01-01", afirmacao=0)
             for d in ("g1.globo.com", "estadao.com.br")]
    itens += [_it(f"https://{d}/b", "SUSTENTA", cluster=d, af=1)
              for d in ("bbc.com", "folha.uol.com.br", "agenciabrasil.ebc.com.br", "aosfatos.org", "lupa.uol.com.br")]
    ev = Evidencias(afirmacoes=[AfirmacaoDecisao(texto="A ponte caiu hoje", nucleo="A ponte caiu hoje", janela=2),
                                AfirmacaoDecisao(texto="A obra custou 2 bilhões", nucleo="A obra custou 2 bilhões")],
                    itens=itens, texto_usuario="A ponte caiu hoje. A obra custou 2 bilhões.",
                    data_referencia="2026-10-09", n_lidas=len(itens), n_consultadas=len(itens))
    d = decidir(ev)
    assert d.descontos_temporais and d.por_afirmacao[0]["n_votos"] == 0
    assert d.nivel == "baixa" and d.log_odds == pytest.approx(-5.0)
    assert d.nivel_sem_desconto == "alta"
    assert d.log_odds_sem_desconto == pytest.approx(_conj_ref([2.0, -5.0]), abs=1e-3)


def test_t7_citacao_com_expressao_proibida_vira_numero_e_o_texto_segue_neutro():
    """A afirmação do usuário pode trazer 'É falso que X' (expressão proibida no nosso texto): cita-se pelo número."""
    itens = [_it(f"https://{d}/a", "SUSTENTA", cluster=d, af=0) for d in ("g1.globo.com", "estadao.com.br")]
    itens += [_it(f"https://{d}/b", "SUSTENTA", cluster=d, af=1)
              for d in ("bbc.com", "folha.uol.com.br", "agenciabrasil.ebc.com.br", "aosfatos.org", "lupa.uol.com.br")]
    d = decidir(_ev_afs(itens, ["É falso que a vacina altera o DNA", "A obra custou 2 bilhões"],
                        pols=["nega", "afirma"], nucleos=["A vacina altera o DNA", "A obra custou 2 bilhões"]))
    assert d.nivel == "alta" and d.por_afirmacao[0]["L"] == pytest.approx(2.0)
    j = d.justificativa()
    assert "a afirmação 1" in j and "é falso" not in j.lower()
    assert verificar_neutralidade(f"{d.header()} {d.why_1linha()} {j}") == []
