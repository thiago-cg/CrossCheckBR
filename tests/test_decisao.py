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


def test_e4_aviso_nao_se_repete_quando_o_desconto_leva_a_indeterminada(monkeypatch):
    """O motivo já diz 'outro episódio' e 'recirculando'; a justificativa e o why não repetem."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.0)
    itens = [ItemEvidencia(url=f"https://{d}/a", cluster=d, classe="REFUTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-01-01")
             for d in ("g1.globo.com", "bbc.com")]
    d = decidir(_ev_hoje(itens))
    assert d.nivel == "indeterminada" and "outro episódio" in d.motivo
    j = d.justificativa()
    assert j.count("outro episódio") == 1 and j.count("recirculando") == 1
    assert "Datas anteriores" not in d.why_1linha()
    assert d.why_1linha().count("outro episódio") == 1
    assert verificar_neutralidade(f"{d.header()} {d.why_1linha()} {j}") == []


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


# ------------------------------------------------------------------ E4 (review): reancoragem da medida
def test_e4_medida_usa_a_janela_da_afirmacao_e_nao_a_do_texto():
    """Review: o texto diz 'nesta semana' (8) e a afirmação diz 'hoje' (2). Fonte de 2026-10-04 está
    5 dias antes da referência: 3 dias além de 'hoje' (e não 6); fonte do mesmo dia não é descontada."""
    texto = "Nesta semana o ministro caiu."
    af = AfirmacaoDecisao(texto="Hoje o ministro caiu", nucleo="ministro caiu", janela=2)

    def _ev_com_fonte(data_pub):
        it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="REFUTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub=data_pub)
        return Evidencias(afirmacoes=[af], itens=[it], texto_usuario=texto, data_referencia="2026-10-09")

    d = decidir(_ev_com_fonte("2026-10-04"))
    assert len(d.descontos_temporais) == 1
    assert d.descontos_temporais[0]["janela"] == 2 and d.descontos_temporais[0]["dias_alem_da_janela"] == 3
    assert not decidir(_ev_com_fonte("2026-10-09")).descontos_temporais


def test_e4_marco_do_evento_desconta_fonte_de_outro_episodio_e_tem_precedencia():
    """Task 4b ligada: o marco (data do evento, folga 2) mede a fonte; ele vence a janela da afirmação.
    Fonte de 2026-09-01: 35 dias além do evento de 2026-10-08 (não 36, que seria pela referência)."""
    af = AfirmacaoDecisao(texto="O jogo foi dia 8 e o time ganhou", nucleo="time ganhou o jogo",
                          janela=2, marco=("2026-10-08", 2))

    def _ev_com_fonte(data_pub):
        it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="SUSTENTA", motor=JUIZ,
                           citacao_verificada=True, curada=True, corpo_lido=True, data_pub=data_pub)
        return Evidencias(afirmacoes=[af], itens=[it], texto_usuario=af.texto, data_referencia="2026-10-09")

    d = decidir(_ev_com_fonte("2026-09-01"))
    assert [(x["janela"], x["dias_alem_da_janela"]) for x in d.descontos_temporais] == [(2, 35)]
    assert not decidir(_ev_com_fonte("2026-10-08")).descontos_temporais


def test_e4_data_explicita_da_afirmacao_nao_desconta_por_hoje(monkeypatch):
    """Decisão da usuária: "O deputado disse hoje que a ponte caiu em 2019". Fonte de 2019 não é
    descontada pelo "hoje" do ato de dizer, nem pelo caminho pelo snapshot antigo (sem janela)."""
    from factcheck_mvp import aplicabilidade
    t = "O deputado disse hoje que a ponte caiu em 2019"
    it = ItemEvidencia(url="https://g1.globo.com/a", cluster="g1", classe="REFUTA", motor=JUIZ,
                       citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2019-06-01")
    janela = aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-10-09")
    ev = Evidencias(afirmacoes=[AfirmacaoDecisao(texto=t, nucleo="ponte caiu", janela=janela)],
                    itens=[it], texto_usuario=t, data_referencia="2026-10-09")
    assert not decidir(ev).descontos_temporais
    # snapshot antigo (janela não calculada): o caminho legado também respeita a regra
    ev_antigo = Evidencias(afirmacoes=[AfirmacaoDecisao(texto=t, nucleo="ponte caiu")],
                           itens=[it], texto_usuario=t, data_referencia="2026-10-09")
    assert not decidir(ev_antigo).descontos_temporais
    # e "Bolsonaro recebeu alta do hospital hoje" (sem data explícita) continua descontando
    assert decidir(_ev_hoje([ItemEvidencia(url="https://g1.globo.com/b", cluster="g1", classe="SUSTENTA",
                                           motor=JUIZ, citacao_verificada=True, curada=True, corpo_lido=True,
                                           data_pub="2021-07-18")])).descontos_temporais


# ------------------------------------------------------------------ E4 (item 5): guarda do sinal
# Decisão da usuária (09/10, após review): E4 só remove informação de fonte de outro episódio. O nível
# pode ficar mais extremo quando a fonte antiga de sinal oposto perde peso, desde que haja voto NÃO
# descontado (fonte do período) na direção do resultado; nunca sustentado só por fonte descontada.
_RECENTE, _ANTIGA = "2026-10-08", "2021-07-18"  # "ontem" e 2021 em relação a 2026-10-09


def _it_e4(url, cluster, classe, data, veredito=None, af=0):
    return ItemEvidencia(url=url, cluster=cluster, classe=classe, motor=JUIZ, citacao_verificada=True,
                         curada=True, corpo_lido=True, data_pub=data, veredito=veredito,
                         origem_veredito="indice" if veredito else None, afirmacao=af)


def _voto_nao_descontado_na_direcao(d):
    descontadas = {x["url"] for x in d.descontos_temporais}
    sinal = 1 if d.log_odds > 0 else -1
    return any(v.direcao == sinal and any(u not in descontadas for u in v.urls) for v in d.votos)


def test_e4_guarda_sinais_opostos_em_clusters_distintos(monkeypatch):
    """Caso A: g1 SUSTENTA de 2021 (perde peso) + bbc e estadão REFUTAM de ontem. O nível sobe de
    media para alta, e isso vem de fontes do período (voto não descontado na direção de L)."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [_it_e4("https://g1.globo.com/a", "g1", "SUSTENTA", _ANTIGA),
             _it_e4("https://bbc.com/a", "bbc", "REFUTA", _RECENTE),
             _it_e4("https://estadao.com.br/a", "estadao", "REFUTA", _RECENTE)]
    d = decidir(_ev_hoje(itens))
    assert d.nivel_sem_desconto == "media" and d.nivel == "alta"
    assert _voto_nao_descontado_na_direcao(d)


def test_e4_guarda_sinais_opostos_no_mesmo_cluster(monkeypatch):
    """Caso B: a mesma mistura (g1 SUSTENTA de 2021 + bbc REFUTA de ontem) no MESMO cluster. O voto do
    cluster passa a ser o da fonte do período (+1,0), que é não descontado: a magnitude sobe na direção dele."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [_it_e4("https://g1.globo.com/a", "x", "SUSTENTA", _ANTIGA),
             _it_e4("https://bbc.com/a", "x", "REFUTA", _RECENTE)]
    d = decidir(_ev_hoje(itens))
    assert d.log_odds > 0 and abs(d.log_odds) > abs(d.log_odds_sem_desconto)
    assert _voto_nao_descontado_na_direcao(d)


@pytest.mark.parametrize("sinais", [("SUSTENTA", "REFUTA", "REFUTA"), ("SUSTENTA", "SUSTENTA", "SUSTENTA")])
def test_e4_guarda_so_fontes_descontadas_nao_viram_alta_nem_baixa(monkeypatch, sinais):
    """Só fontes de outro episódio (3 clusters, r = 0,05): nenhum nível extremo, nem com sinais mistos."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [_it_e4(f"https://{dom}/a", dom, classe, _ANTIGA)
             for dom, classe in zip(("g1.globo.com", "estadao.com.br", "bbc.com"), sinais)]
    d = decidir(_ev_hoje(itens))
    assert d.nivel == "media" and d.travas["data_incompativel"]


# Contraexemplo que a guarda não cobria (14 posturas ou 7 selos FALSO descontados, r=0,05, somavam ≥ τ e
# viravam alta sem voto do período): a trava so_fontes_de_outro_periodo (decisão de 09/10) corta para média.
@pytest.mark.parametrize("veredito,n", [(None, 14), ("FALSO", 7)])
def test_e4_guarda_so_fontes_descontadas_nao_viram_alta_com_muitas_fontes(monkeypatch, veredito, n):
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [_it_e4(f"https://s{i}.com/a", f"c{i}", "REFUTA", _ANTIGA, veredito=veredito) for i in range(n)]
    d = decidir(_ev_hoje(itens))
    assert d.nivel == "media"


# ------------------------------------------------------------------ trava so_fontes_de_outro_periodo
def test_e4_trava_nao_atua_com_fonte_do_periodo_na_mesma_direcao(monkeypatch):
    """Trava (a): uma fonte atual REFUTA + 14 posturas antigas (r=0,05, somam ≈1,15). O voto do período
    sustenta a direção de alta; a trava não atua e o nível pode chegar a alta."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [_it_e4("https://bbc.com/a", "bbc", "REFUTA", _RECENTE)]
    itens += [_it_e4(f"https://s{i}.com/a", f"c{i}", "REFUTA", _ANTIGA) for i in range(14)]
    d = decidir(_ev_hoje(itens))
    assert d.nivel == "alta" and not d.travas["so_fontes_de_outro_periodo"]
    assert _voto_nao_descontado_na_direcao(d)


def test_e4_trava_antigas_fortes_contra_atual_oposta_nao_viram_alta(monkeypatch):
    """Trava (b): 30 posturas antigas REFUTA (r=0,05, somam ≈2,47) e 1 fonte atual SUSTENTA (−1,0). A soma
    passa de τ, mas nenhum voto de alta tem fonte do período: a trava corta para 0,99τ e o nível é média
    (sem o desconto seria alta). O aviso de data aparece uma vez só."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    itens = [_it_e4(f"https://s{i}.com/a", f"c{i}", "REFUTA", _ANTIGA) for i in range(30)]
    itens.append(_it_e4("https://bbc.com/a", "bbc", "SUSTENTA", _RECENTE))
    d = decidir(_ev_hoje(itens))
    assert d.nivel_sem_desconto == "alta"
    assert d.nivel == "media" and d.travas["so_fontes_de_outro_periodo"]
    assert d.log_odds == pytest.approx(0.99 * decisao.TAU, abs=1e-4)  # L é gravado com 4 casas
    assert not _voto_nao_descontado_na_direcao(d)
    assert "outro episódio" in d.motivo
    j = d.justificativa()
    assert j.count("outro episódio") == 1 and j.count("recirculando") == 1
    assert d.why_1linha().count("Datas anteriores") == 1
    assert verificar_neutralidade(f"{d.header()} {d.why_1linha()} {j}") == []


def test_e4_trava_sem_marcador_temporal_nao_muda_nada():
    """Trava (c), regressão: sem marcador temporal nenhuma fonte é descontada; 14 posturas REFUTA de fontes
    atuais seguem dando alta, sem a trava e sem aviso de data."""
    itens = [_it_e4(f"https://s{i}.com/a", f"c{i}", "REFUTA", _ANTIGA) for i in range(14)]
    d = decidir(Evidencias(afirmacoes=[AfirmacaoDecisao(texto="A ponte caiu", nucleo="ponte caiu")],
                           itens=itens, data_referencia="2026-10-09"))
    assert not d.descontos_temporais and not d.travas["so_fontes_de_outro_periodo"]
    assert d.travas["data_incompativel"] is False
    assert d.nivel == "alta" and d.log_odds == pytest.approx(14.0)


def test_e4_trava_corta_afirmacao_so_descontada_e_a_tira_da_conjuncao(monkeypatch):
    """Trava + conjunção: A confirmada por fontes atuais (L=−3) e B só com 14 posturas antigas (r=0,05,
    L_b≈+1,15 → cortado para 0,99τ). B sai do produto. O que sobra é A (−3), e a baixa que sobraria fica
    limitada a média (D-b, decisão de 09/10): a baixa dependeria de B, que não tem fonte do período."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    a = [_it_e4(f"https://{dom}/a", dom, "SUSTENTA", _RECENTE, af=0)
         for dom in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    b = [_it_e4(f"https://s{i}.com/b", f"c{i}", "REFUTA", _ANTIGA, af=1) for i in range(14)]
    d = decidir(_ev_afs_janela(a + b, textos=("A obra custou 2 bilhões", "A ponte caiu hoje"), janelas=(None, 2)))
    assert d.por_afirmacao[1]["L"] == pytest.approx(0.99 * decisao.TAU, abs=1e-4)
    assert d.por_afirmacao[1]["fora_da_conjuncao"] == "só evidência de outro período"
    assert d.travas["so_fontes_de_outro_periodo"] and d.travas["parte_sem_checagem_atual"]
    assert d.log_odds == pytest.approx(-0.99 * decisao.TAU, abs=1e-4) and d.nivel == "media"


def test_e4_trava_e_sem_fonte_confiavel_juntas_registram_as_duas_flags(monkeypatch):
    """Ordem das travas: sem_fonte_confiavel e so_fontes leem o MESMO L_a bruto e cortam para o MESMO 0,99τ.
    Com as duas condições valendo (4 selos FALSO de sites de baixo tráfego, r=0,5), as duas flags ficam
    registradas, o nível é média e o motivo segue a precedência de sem_fonte_confiavel (redes/sites)."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.5)
    itens = [ItemEvidencia(url=f"https://blog{i}.example/a", cluster=f"c{i}", classe="REFUTA", motor=JUIZ,
                           citacao_verificada=True, curada=False, corpo_lido=True, data_pub=_ANTIGA,
                           veredito="FALSO", origem_veredito="indice") for i in range(4)]
    d = decidir(_ev_hoje(itens))
    assert d.nivel == "media" and d.log_odds == pytest.approx(0.99 * decisao.TAU, abs=1e-4)
    assert d.travas["sem_fonte_confiavel"] and d.travas["so_fontes_de_outro_periodo"]
    assert "redes sociais" in d.motivo


def test_e4_parametros_registram_as_janelas_efetivas_da_config(monkeypatch):
    from factcheck_mvp import config
    monkeypatch.setattr(config, "E4_JANELA_HOJE", 5)
    d = decidir(_ev_hoje([]))
    assert d.parametros["e4"]["janelas"]["E4_JANELA_HOJE"] == 5
    assert d.parametros["e4"]["janelas"]["E4_JANELA_SEMANA"] == config.E4_JANELA_SEMANA


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


def _decisao_com_descontos(urls_por_afirmacao, nivel="media", nivel_sem="alta"):
    """Decisao montada à mão (só o que o texto usa): descontos por (url, afirmação)."""
    return decisao.Decisao(
        nivel=nivel, log_odds=0.5, prob=0.6, motivo="evidência fraca ou dividida",
        descontos_temporais=[{"url": u, "afirmacao": a} for u, a in urls_por_afirmacao],
        travas={"data_incompativel": True}, nivel_sem_desconto=nivel_sem)


def test_e4_plural_concorda_e_conta_url_unica_na_justificativa():
    # A mesma URL em duas afirmações é UMA fonte: "1 fonte foi publicada ... o peso dela".
    uma = _decisao_com_descontos([("https://g1.globo.com/a", 0), ("https://g1.globo.com/a", 1)])
    j = uma.justificativa()
    assert "1 fonte foi publicada antes do período" in j and "o peso dela foi reduzido" in j
    assert "fontes foram" not in j and "delas" not in j
    duas = _decisao_com_descontos([("https://g1.globo.com/a", 0), ("https://bbc.com/b", 0)])
    j2 = duas.justificativa()
    assert "2 fontes foram publicadas antes do período" in j2 and "o peso delas foi reduzido" in j2
    assert verificar_neutralidade(j) == [] and verificar_neutralidade(j2) == []


def test_e4_limitacao_de_datas_concorda_e_conta_url_unica():
    assert _decisao_com_descontos([("https://g1.globo.com/a", 0), ("https://g1.globo.com/a", 1)]).limitacao_datas() \
        == "Datas: 1 fonte anterior ao período do texto teve o peso reduzido."
    lim = _decisao_com_descontos([("https://g1.globo.com/a", 0), ("https://bbc.com/b", 0)]).limitacao_datas()
    assert lim == "Datas: 2 fontes anteriores ao período do texto tiveram o peso reduzido."
    assert verificar_neutralidade(lim) == []


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


# ------------------------------------------------------------------ D-c (09/10): voto < 5% do peso não conta
def _d_c_texto(pf):
    """Repro do revisor: P1 com 1 REFUTA curada (prob_fake = pf) e P2 com 2 SUSTENTA curadas."""
    itens = [_it("https://g1.globo.com/a", "REFUTA", cluster="g1", af=0, pf=pf)]
    itens += [_it("https://estadao.com.br/b", "SUSTENTA", cluster="estadao", af=1),
              _it("https://bbc.com/b", "SUSTENTA", cluster="bbc", af=1)]
    return decidir(_ev_afs(itens, ["Parte 1", "Parte 2"]))


@pytest.mark.parametrize("pf", [1.0, 0.99999, 0.9999, 0.96])
def test_d_c_postura_com_menos_de_5_por_cento_nao_vota(pf):
    """Peso da postura < 5% do peso sem o BERT (1 − prob_fake < 0,05): não vota, como se não existisse. P1 sai
    da conjunção e o texto é o de P2 (baixa)."""
    d = _d_c_texto(pf)
    assert d.por_afirmacao[0]["n_votos"] == 0
    assert d.nivel == "baixa" and d.log_odds == pytest.approx(-2.0)
    assert len(d.posturas_fracas) == 1 and d.posturas_fracas[0]["url"] == "https://g1.globo.com/a"


def test_d_c_prob_fake_9999_e_1_dao_o_mesmo_nivel():
    """O caso do revisor: antes, 1,0 dava baixa e 0,99999 dava média. Agora os dois dão o mesmo resultado."""
    assert _d_c_texto(0.99999).nivel == _d_c_texto(1.0).nivel == "baixa"
    assert _d_c_texto(0.99999).log_odds == _d_c_texto(1.0).log_odds


@pytest.mark.parametrize("pf", [0.9, 0.94])
def test_d_c_postura_com_mais_de_5_por_cento_ainda_vota(pf):
    """Com 10% (prob_fake 0,9) ou 6% do peso, a postura ainda vota: P1 é contestada, com L = 1 − prob_fake.
    Não some e não é tratada como 'sem voto'."""
    d = _d_c_texto(pf)
    assert d.por_afirmacao[0]["n_votos"] == 1
    assert d.por_afirmacao[0]["L"] == pytest.approx(1.0 - pf, abs=1e-4)
    assert d.posturas_fracas == []


def test_d_c_nao_afeta_veredito_do_indice():
    """O fator BERT só reduz a postura. Um selo do índice (1,5, sem BERT) continua votando com prob_fake 1,0."""
    it = _it("https://lupa.uol.com.br/x", "REFUTA", veredito="FALSO", origem="indice", pf=1.0)
    d = decidir(_ev([it]))
    assert d.nivel == "alta" and len(d.vereditos_aplicados) == 1
    assert len(d.posturas_fracas) == 1


def test_d_c_constante_e_trace_registram_o_caso():
    """A fração mínima fica nomeada em `parametros` e o caso sem voto aparece no trace (`posturas_fracas`)."""
    d = _d_c_texto(1.0)
    assert d.parametros["fracao_min_voto"] == decisao.FRACAO_MIN_VOTO == 0.05
    r = d.resumo_trace()
    assert r["posturas_fracas"] == ["af0 REFUTA@https://g1.globo.com/a pf=1.0"]


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
    """Números do spec, com a decisão de 09/10 (só partes contestadas somam). (b) p=0,70 (contestada) e
    p=0,10 (confirmada): só a contestada entra, L = logit(0,70) ≈ 0,85 → média. Duas contestadas seguem o
    produto 1 − Π(1 − σ(L_a)). (a) A=+2 (contestada) e B=−5 (confirmada): L = 2 → alta."""
    contestada, confirmada = math.log(0.7 / 0.3), math.log(0.1 / 0.9)
    assert decisao._combinar_afirmacoes([contestada, confirmada]) == pytest.approx(contestada, abs=1e-9)
    assert decisao.nivel_de(decisao._combinar_afirmacoes([contestada, confirmada])) == "media"
    duas = [math.log(0.7 / 0.3), math.log(0.6 / 0.4)]
    assert decisao._combinar_afirmacoes(duas) == pytest.approx(_conj_ref(duas), abs=1e-9)
    assert decisao._combinar_afirmacoes([2.0, -5.0]) == pytest.approx(2.0, abs=1e-9)
    assert decisao.nivel_de(decisao._combinar_afirmacoes([2.0, -5.0])) == "alta"


@pytest.mark.parametrize("valor", [3.0, -2.0, 0.9, -0.45, 0.0])
def test_t7_uma_afirmacao_devolve_o_mesmo_L(valor):
    """Com uma afirmação, σ e logit se cancelam: o L é exatamente o de antes."""
    assert decisao._combinar_afirmacoes([valor]) == valor
    assert decisao._combinar_afirmacoes([]) == 0.0


@pytest.mark.parametrize("valor", [-4.0, -1.2, 0.3, 1.0, 2.5, 4.0])
def test_t7_confirmacao_forte_nao_entra_no_produto(valor):
    """Decisão de 09/10: a confirmação (L=−40) não soma. A outra parte fica como está: contestada, o próprio
    L; confirmada, o máximo, que é o L dela."""
    assert decisao._combinar_afirmacoes([valor, -40.0]) == pytest.approx(valor, abs=1e-9)


@pytest.mark.parametrize("valores", [[1.0, 0.5], [0.3, 2.5, 0.0], [0.9, 0.9]])
def test_t7_ramo_geral_so_entre_contestadas(valores):
    """O produto 1 − Π(1 − σ(L_a)) vale entre as partes contestadas (L ≥ 0); a confirmação não entra."""
    assert decisao._combinar_afirmacoes(valores + [-40.0]) == pytest.approx(_conj_ref(valores), abs=1e-6)


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
    """Votos que se anulam (L=0) não são 'sem voto' (n_votos > 0): a parte dividida conta como L_a ≥ 0
    (decisão de 09/10). B, confirmada (L=−2), não soma: o texto fica no L da dividida, 0 (média)."""
    itens = [_it("https://g1.globo.com/a", "SUSTENTA", cluster="g1", af=0),
             _it("https://estadao.com.br/a", "REFUTA", cluster="estadao", af=0)]
    itens += [_it(f"https://{d}/b", "SUSTENTA", cluster=d, af=1) for d in ("bbc.com", "folha.uol.com.br")]
    d = decidir(_ev_afs(itens, ["A", "B"]))
    assert d.por_afirmacao[0]["L"] == 0 and d.por_afirmacao[0]["n_votos"] == 2
    assert d.por_afirmacao[1]["L"] == pytest.approx(-2.0)
    assert d.log_odds == 0 and d.nivel == "media"


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
    # decisão de 09/10: no contrafactual só a contestada (A, +2) soma; a confirmada (B, −5) não entra
    assert d.log_odds_sem_desconto == pytest.approx(2.0, abs=1e-3)


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


# ------------------------------------------------------------------ C1/I1 (revisão): fonte atual decide, não a antiga
def test_c1_parte_cortada_com_voto_atual_de_sinal_oposto_fica_na_conjuncao(monkeypatch):
    """Repro do revisor (C1): A "A ponte caiu hoje" tem 1 REFUTA curada atual (bbc) e 5 SUSTENTA curadas de
    2026-10-05 (r = 0,5 no stub). Os votos antigos (−3,1) fazem a trava so_fontes cortar L_a para −0,99τ. A
    afirmação tem voto atual, então continua no produto (antes saía e dava baixa, "0 fontes contestam").
    B "A obra custou 2 bilhões" tem 2 SUSTENTA curadas atuais. O resultado esperado é média, não baixa."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.5)
    a = [_it("https://bbc.com/a", "REFUTA", cluster="bbc", af=0)]
    a += [_it_datada(f"https://s{i}.com/a", "SUSTENTA", "2026-10-05", af=0, cluster=f"s{i}") for i in range(5)]
    b = [_it(f"https://{dom}/b", "SUSTENTA", cluster=dom, af=1) for dom in ("g1.globo.com", "estadao.com.br")]
    d = decidir(_ev_afs_janela(a + b, textos=("A ponte caiu hoje", "A obra custou 2 bilhões"), janelas=(2, None)))
    assert d.nivel == "media" and d.log_odds == pytest.approx(-0.99 * decisao.TAU, abs=1e-4)
    assert d.travas["so_fontes_de_outro_periodo"]
    assert "fora_da_conjuncao" not in d.por_afirmacao[0]
    assert d.por_afirmacao[0]["L"] == pytest.approx(-0.99 * decisao.TAU, abs=1e-4)
    assert verificar_neutralidade(f"{d.header()} {d.why_1linha()} {d.justificativa()}") == []


def _pl(dom, classe="REFUTA", af=0):
    """Rede social / plataforma (peso 0,45), sem curadoria: vota, mas não destrava a trava de confiabilidade."""
    return _it_nivel(f"https://{dom}/x", classe, confiabilidade.PLATAFORMA, af=af, cluster=dom)


def test_i1_curada_antiga_nao_destrava_a_trava_de_confiabilidade(monkeypatch):
    """Repro do revisor (I1): 3 REFUTA de plataforma atuais (0,45 cada) e 1 REFUTA curada de 2021 (r ≈ 0,001).
    Antes a curada antiga destravava a trava (alta). Agora só a fonte atual conta: média, com a trava. A frase
    neutra diz que a confirmação confiável é de outro período."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    plataformas = [_pl(f"rede{i}.com") for i in range(3)]
    velha = [_it_datada("https://g1.globo.com/v", "REFUTA", "2021-07-18", cluster="g1")]
    d = decidir(_ev_hoje(plataformas + velha, texto="A ponte caiu hoje"))
    assert d.nivel == "media" and d.travas["sem_fonte_confiavel"]
    assert d.descontos_temporais[0]["confiabilidade"] == confiabilidade.CURADA
    j = d.justificativa()
    assert "redes sociais, sites pouco acessados ou de outro período" in j
    assert "ou de outro período" in d.motivo
    assert verificar_neutralidade(f"{d.header()} {d.why_1linha()} {j}") == []


def test_i1_sem_a_curada_antiga_o_texto_so_fala_de_redes(monkeypatch):
    """Controle: só as 3 plataformas atuais. A frase diz redes e sites pouco acessados, sem 'de outro período'."""
    plataformas = [_pl(f"rede{i}.com") for i in range(3)]
    d = decidir(_ev_hoje(plataformas, texto="A ponte caiu hoje"))
    assert d.nivel == "media" and d.travas["sem_fonte_confiavel"]
    j = d.justificativa()
    assert "redes sociais ou sites pouco acessados, então" in j and "outro período" not in j
    assert "outro período" not in d.motivo


def test_i1_fonte_confiavel_atual_ainda_destrava(monkeypatch):
    """A confirmação confiável ATUAL continua a destravar: 3 plataformas (1,35) + 1 curada atual (1,0) → alta."""
    plataformas = [_pl(f"rede{i}.com") for i in range(3)]
    atual = [_it("https://g1.globo.com/a", "REFUTA", cluster="g1")]
    d = decidir(_ev_hoje(plataformas + atual, texto="A ponte caiu hoje"))
    assert d.nivel == "alta" and not d.travas["sem_fonte_confiavel"]


# ------------------------------------------------------------------ D-a (09/10): só partes contestadas somam
def test_t7_cinco_confirmadas_com_uma_fonte_cada_nao_dao_alta():
    """Caso do revisor: 5 afirmações, cada uma confirmada por 1 curada, nenhuma contestada. Antes o produto dava
    alta (L≈+1,33, '0 fontes contestam e 5 confirmam'). Agora só confirmações: L = max = −1,0, que é média."""
    itens = [_it(f"https://fonte{k}.com.br/a", "SUSTENTA", cluster=f"f{k}", af=k) for k in range(5)]
    d = decidir(_ev_afs(itens, [f"Afirmacao {k}" for k in range(5)]))
    assert d.log_odds == pytest.approx(-1.0) and d.nivel == "media"
    assert d.n_clusters(+1) == 0 and d.n_clusters(-1) == 5


def test_t7_cinco_confirmadas_com_duas_fontes_cada_dao_baixa():
    """Com 2 curadas por afirmação (L = −2,0 cada), as cinco confirmam e o nível é baixa, pela confirmação mais fraca."""
    itens = [_it(f"https://f{k}-{j}.com.br/x", "SUSTENTA", cluster=f"f{k}-{j}", af=k) for k in range(5) for j in range(2)]
    d = decidir(_ev_afs(itens, [f"Afirmacao {k}" for k in range(5)]))
    assert d.log_odds == pytest.approx(-2.0) and d.nivel == "baixa"


@pytest.mark.parametrize("n", list(range(1, 13)))
def test_t7_so_confirmacoes_nunca_dao_alta(n):
    """Propriedade (decisão de 09/10): só confirmações nunca dão alta, com qualquer número de partes e qualquer
    mistura de pesos. Cada afirmação recebe 1 a 3 fontes confirmando (curada −1,0; institucional −0,6;
    baixo tráfego −0,3). O texto é baixa ou média, e o L é o da confirmação mais fraca."""
    def fontes(k):
        return [_it(f"https://c{k}.com.br/a", "SUSTENTA", cluster=f"c{k}", af=k),
                _it_nivel(f"https://i{k}.gov.br/a", "SUSTENTA", confiabilidade.INSTITUCIONAL, af=k, cluster=f"i{k}"),
                _it_nivel(f"https://b{k}.com/a", "SUSTENTA", confiabilidade.BAIXO_TRAFEGO, af=k, cluster=f"b{k}")]
    itens = [it for k in range(n) for it in fontes(k)[: 1 + (k % 3)]]
    d = decidir(_ev_afs(itens, [f"Afirmacao {k}" for k in range(n)]))
    assert all(p["L"] < 0 for p in d.por_afirmacao)
    assert d.log_odds < 0 and d.nivel in ("media", "baixa")
    assert d.log_odds == pytest.approx(max(p["L"] for p in d.por_afirmacao), abs=1e-4)


@pytest.mark.parametrize("valores", [[-0.3, -1.0], [-2.5, -0.1, -4.0], [-1.0] * 12])
def test_t7_so_confirmacoes_nao_dao_alta_na_funcao(valores):
    """Mesma propriedade na função: com L_a < 0 em todas as partes, o resultado é max(L_a), sempre < τ."""
    assert decisao._combinar_afirmacoes(valores) == pytest.approx(max(valores))
    assert decisao.nivel_de(decisao._combinar_afirmacoes(valores)) != "alta"


def test_t7_duas_partes_divididas_dao_alta_comportamento_conhecido():
    """Comportamento conhecido (decisão de 09/10): L = 0 conta como contestada. Duas partes divididas (um voto a
    favor e um contra cada) dão p_texto = 1 − 0,5·0,5 = 0,75, isto é, L = τ, e o nível é alta. Documentado aqui;
    o critério não muda nesta rodada."""
    itens = [_it("https://g1.globo.com/a", "SUSTENTA", cluster="g1", af=0),
             _it("https://estadao.com.br/a", "REFUTA", cluster="estadao", af=0),
             _it("https://bbc.com/b", "SUSTENTA", cluster="bbc", af=1),
             _it("https://folha.uol.com.br/b", "REFUTA", cluster="folha", af=1)]
    d = decidir(_ev_afs(itens, ["A", "B"]))
    assert d.por_afirmacao[0]["L"] == 0 and d.por_afirmacao[1]["L"] == 0
    assert d.log_odds == pytest.approx(decisao.TAU, abs=1e-4) and d.nivel == "alta"


def test_t7_parte_dividida_entra_no_produto_ao_lado_da_contestada():
    """Consequência de L_a ≥ 0 entrar no produto (decisão de 09/10): a dividida (L=0, p=0,5) soma com a contestada
    (A, L=+1,0): p_texto = 1 − 0,5·σ(−1,0) ≈ 0,866 e L ≈ 1,86, alta. Sem a dividida, o L seria 1,0 (média)."""
    itens = [_it("https://g1.globo.com/a", "SUSTENTA", cluster="g1", af=0),
             _it("https://estadao.com.br/a", "REFUTA", cluster="estadao", af=0),
             _it("https://bbc.com/b", "REFUTA", cluster="bbc", af=1)]
    d = decidir(_ev_afs(itens, ["A dividida", "B contestada"]))
    assert d.por_afirmacao[0]["L"] == 0 and d.por_afirmacao[1]["L"] == pytest.approx(1.0)
    assert d.log_odds == pytest.approx(_conj_ref([0.0, 1.0]), abs=1e-4)
    assert d.log_odds == pytest.approx(1.8614, abs=1e-3) and d.nivel == "alta"


# ------------------------------------------------------------------ T7 + E4: só evidência descontada fora da conjunção
def _it_datada(url, classe, data_pub, af=0, cluster=None):
    """Item curado com corpo lido e data de publicação: pode ser descontado pelo E4."""
    return ItemEvidencia(url=url, afirmacao=af, cluster=cluster or url, classe=classe, motor=JUIZ,
                         citacao_verificada=True, curada=True, corpo_lido=True, data_pub=data_pub)


def _ev_afs_janela(itens, textos=("A obra custou 2 bilhões", "A ponte caiu hoje"), janelas=(None, 2)):
    """Duas afirmações, cada uma com a própria janela (E4). Com janela None (e 2 afirmações) a
    afirmação não é descontada; com janela 2 ("hoje") só as fontes fora da janela descontam."""
    texto = ". ".join(textos) + "."
    afs = [AfirmacaoDecisao(texto=t, nucleo=t, janela=j) for t, j in zip(textos, janelas)]
    return Evidencias(afirmacoes=afs, itens=itens, texto_usuario=texto, data_referencia="2026-10-09",
                      n_lidas=len(itens), n_consultadas=len(itens))


def test_t7_so_evidencia_descontada_sai_da_conjuncao(monkeypatch):
    """Achado do T7 (09/10): A confirmada (L=-3, fontes atuais) + B só com fonte de 2021, descontada
    (r≈0,001, L_b≈+0,002). B fica fora do produto. Sem B, A daria baixa, e D-b limita o texto a média, com a
    frase neutra sobre a afirmação B."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    a = [_it(f"https://{dom}/a", "SUSTENTA", cluster=dom, af=0) for dom in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    b = [_it_datada("https://folha.uol.com.br/b", "REFUTA", "2021-07-18", af=1)]
    d = decidir(_ev_afs_janela(a + b))
    assert d.por_afirmacao[0]["L"] == pytest.approx(-3.0)
    assert d.por_afirmacao[1]["fora_da_conjuncao"] == "só evidência de outro período"
    assert "fora_da_conjuncao" not in d.por_afirmacao[0]
    assert d.log_odds == pytest.approx(-0.99 * decisao.TAU, abs=1e-4) and d.nivel == "media"  # D-b
    assert d.travas["parte_sem_checagem_atual"]
    assert "E4" in d.parametros["combinacao"]
    # o contrafactual (sem desconto) usa o mesmo critério (decisão de 09/10): lá B conta com o voto bruto
    # (REFUTA, +1, contestada) e é a única parte que soma; A, confirmada (−3), não entra
    assert d.log_odds_sem_desconto == pytest.approx(1.0, abs=1e-3)
    assert {x["afirmacao"] for x in d.descontos_temporais} == {1}


def test_t7_votos_atuais_que_se_anulam_continuam_no_produto():
    """Disputa real dentro da janela: B tem uma REFUTA e uma SUSTENTA atuais (L_b=0, n_votos=2). A parte
    dividida é contestada (L ≥ 0) e entra no produto; A, confirmada (−3), não soma (decisão de 09/10).
    O texto fica média, não baixa."""
    a = [_it(f"https://{dom}/a", "SUSTENTA", cluster=dom, af=0) for dom in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    b = [_it_datada("https://uol.com.br/b", "REFUTA", "2026-10-08", af=1, cluster="uol"),
         _it_datada("https://folha.uol.com.br/b", "SUSTENTA", "2026-10-08", af=1, cluster="folha")]
    d = decidir(_ev_afs_janela(a + b))
    assert not d.descontos_temporais
    assert d.por_afirmacao[1]["L"] == 0 and d.por_afirmacao[1]["n_votos"] == 2
    assert "fora_da_conjuncao" not in d.por_afirmacao[1]
    assert d.nivel == "media"
    assert d.log_odds == pytest.approx(0.0, abs=1e-4)


def test_t7_so_descontada_forte_e_cortada_e_sai_da_conjuncao(monkeypatch):
    """Regra decidida (09/10, guarda do sinal: E4 nunca sustenta extremo só com fonte descontada). Antes da
    trava, B (3 fontes de 2021, r=0,9 no stub, L_b≈+2,8 ≥ τ) entrava na conjunção e A (confirmada, −3)
    virava alta (L≈+2,85). Agora a trava so_fontes corta B para 0,99τ e ela sai do produto. Sobra A (−3),
    e D-b limita a baixa a média, porque a baixa dependeria de B, sem fonte do período."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.9)
    a = [_it(f"https://{dom}/a", "SUSTENTA", cluster=dom, af=0) for dom in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    b = [_it_datada(f"https://{dom}/b", "REFUTA", "2021-07-18", af=1)
         for dom in ("uol.com.br", "folha.uol.com.br", "lupa.uol.com.br")]
    d = decidir(_ev_afs_janela(a + b))
    assert d.por_afirmacao[1]["L"] == pytest.approx(0.99 * decisao.TAU, abs=1e-4)
    assert d.por_afirmacao[1]["fora_da_conjuncao"] == "só evidência de outro período"
    assert d.travas["so_fontes_de_outro_periodo"]
    assert d.log_odds == pytest.approx(-0.99 * decisao.TAU, abs=1e-4) and d.nivel == "media"  # D-b


def test_t7_uma_afirmacao_so_descontada_nao_muda(monkeypatch):
    """Uma afirmação só (texto com 'hoje'): a regra não age; o L é o dela, mesmo só descontado e < τ."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    it = _it_datada("https://g1.globo.com/a", "REFUTA", "2021-07-18", cluster="g1")
    d = decidir(_ev_hoje([it]))
    assert d.descontos_temporais and 0 < abs(d.log_odds) < decisao.TAU
    assert d.log_odds == d.por_afirmacao[0]["L"]
    assert "fora_da_conjuncao" not in d.por_afirmacao[0]


def test_t7_sem_conjuncao_com_uma_afirmacao_com_voto_nada_muda(monkeypatch):
    """Com uma só afirmação com voto não há conjunção: a que só tem evidência descontada mantém o L
    (sem marcador). A outra, sem voto, não muda o resultado (D2)."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    a = [_it("https://bbc.com/a", "NAO_TRATA", af=0)]
    b = [_it_datada("https://g1.globo.com/b", "REFUTA", "2021-07-18", af=1, cluster="g1")]
    d = decidir(_ev_afs_janela(a + b))
    assert d.por_afirmacao[0]["n_votos"] == 0 and d.por_afirmacao[1]["n_votos"] == 1
    assert "fora_da_conjuncao" not in d.por_afirmacao[1]
    assert d.log_odds == d.por_afirmacao[1]["L"] and d.log_odds != 0


def test_t7_duas_so_descontadas_fracas_nao_informam(monkeypatch):
    """Duas partes com evidência só de outro período e |L|<τ: nenhuma informa o fato atual. O produto
    fica vazio (L=0, media) e as duas ficam marcadas."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    a = [_it_datada("https://g1.globo.com/a", "REFUTA", "2021-07-18", af=0, cluster="g1")]
    b = [_it_datada("https://bbc.com/b", "SUSTENTA", "2021-07-18", af=1, cluster="bbc")]
    d = decidir(_ev_afs_janela(a + b, textos=("A ponte caiu hoje", "A obra custou 2 bilhões hoje"), janelas=(2, 2)))
    assert {x["afirmacao"] for x in d.descontos_temporais} == {0, 1}
    assert all(p["fora_da_conjuncao"] == "só evidência de outro período" for p in d.por_afirmacao)
    assert d.log_odds == 0 and d.nivel == "media"


# ------------------------------------------------------------------ D-b (09/10): parte sem fonte atual limita a média
def test_t7_parte_sem_fonte_atual_nao_deixa_o_texto_cravar_baixa(monkeypatch):
    """Repro do revisor: A confirmada por 3 curadas atuais (L=−3) e B "A ponte caiu hoje" só com REFUTA de 2021
    (fora da conjunção). Antes o texto ficava baixa. Agora fica média (L = −0,99τ), com a trava, a frase
    neutra sobre B e o registro no trace."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    a = [_it(f"https://{dom}/a", "SUSTENTA", cluster=dom, af=0) for dom in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    b = [_it_datada("https://folha.uol.com.br/b", "REFUTA", "2021-07-18", af=1)]
    d = decidir(_ev_afs_janela(a + b))
    assert d.nivel == "media" and d.log_odds == pytest.approx(-0.99 * decisao.TAU, abs=1e-4)
    assert d.travas["parte_sem_checagem_atual"] is True
    assert d.resumo_trace()["parte_sem_checagem_atual"] is True
    j = d.justificativa()
    assert 'A afirmação "A ponte caiu hoje" não tem fonte do período descrito.' in j
    assert "não tem fonte" not in d.why_1linha()
    assert verificar_neutralidade(f"{d.header()} {d.why_1linha()} {j}") == []


def test_t7_parte_sem_fonte_atual_mantem_alta_pela_contestada_atual(monkeypatch):
    """D-b: se o nível é alta pelas partes contestadas atuais, a parte só de outro período não rebaixa nada,
    não há limite e a frase neutra não aparece."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    a = [_it(f"https://{dom}/a", "REFUTA", cluster=dom, af=0) for dom in ("g1.globo.com", "estadao.com.br")]
    b = [_it_datada("https://folha.uol.com.br/b", "SUSTENTA", "2021-07-18", af=1)]
    d = decidir(_ev_afs_janela(a + b))
    assert d.nivel == "alta" and d.log_odds == pytest.approx(2.0, abs=1e-4)
    assert not d.travas["parte_sem_checagem_atual"]
    assert "não tem fonte do período descrito" not in d.justificativa()


def test_t7_duas_partes_fora_com_baixa_citam_as_duas_e_ficam_em_media(monkeypatch):
    """Com duas afirmações fora (só de outro período) e a confirmada atual, a frase neutra cita as duas."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    textos = ("A obra custou 2 bilhões", "A ponte caiu hoje", "O prefeito renunciou hoje")
    a = [_it(f"https://{dom}/a", "SUSTENTA", cluster=dom, af=0) for dom in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    b = [_it_datada("https://folha.uol.com.br/b", "REFUTA", "2021-07-18", af=1)]
    c = [_it_datada("https://uol.com.br/c", "SUSTENTA", "2021-07-18", af=2)]
    d = decidir(_ev_afs_janela(a + b + c, textos=textos, janelas=(None, 2, 2)))
    assert d.nivel == "media" and d.travas["parte_sem_checagem_atual"]
    j = d.justificativa()
    assert '"A ponte caiu hoje" não tem fonte do período descrito' in j
    assert '"O prefeito renunciou hoje" não tem fonte do período descrito' in j


# ------------------------------------------------------------------ contagem do texto (só o que pesou)
def test_t7_contagem_do_texto_so_conta_afirmacoes_que_entram_no_resultado(monkeypatch):
    """A afirmação excluída da conjunção (só evidência de outro período) mantém os votos no trace (`votos`),
    mas 'N fonte(s) contestam/confirmam' do texto conta só as afirmações que pesaram."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    a = [_it(f"https://{dom}/a", "SUSTENTA", cluster=dom, af=0) for dom in ("g1.globo.com", "estadao.com.br", "bbc.com")]
    b = [_it_datada("https://folha.uol.com.br/b", "REFUTA", "2021-07-18", af=1)]
    d = decidir(_ev_afs_janela(a + b))
    assert d.por_afirmacao[1]["fora_da_conjuncao"] == "só evidência de outro período"
    assert any(v.afirmacao == 1 for v in d.votos)  # trace: o voto de B continua registrado
    assert d.n_clusters(+1) == 0 and d.n_clusters(-1) == 3  # texto: só A pesou
    assert "0 fonte(s) independente(s) contestam o que o texto afirma e 3 o confirmam" in d.justificativa()
    assert "0 fonte(s) independente(s) contestam e 3 confirmam" in d.why_1linha()


def test_t7_selos_considerados_nao_listam_afirmacao_excluida(monkeypatch):
    """Selo de checagem de afirmação excluída (só de outro período) não entra em 'selos de checagem
    considerados'; o selo da afirmação que pesa continua listado. O trace guarda os dois."""
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.001)
    ia = ItemEvidencia(url="https://g1.globo.com/a", afirmacao=0, cluster="g1", classe="SUSTENTA", motor=JUIZ,
                       citacao_verificada=True, curada=True, corpo_lido=True, veredito="VERDADEIRO",
                       origem_veredito="indice", veiculo="Agencia A")
    ib = ItemEvidencia(url="https://bbc.com/b", afirmacao=1, cluster="bbc", classe="REFUTA", motor=JUIZ,
                       citacao_verificada=True, curada=True, corpo_lido=True, data_pub="2021-07-18",
                       veredito="FALSO", origem_veredito="indice", veiculo="Agencia B")
    d = decidir(_ev_afs_janela([ia, ib]))
    assert d.por_afirmacao[1]["fora_da_conjuncao"] == "só evidência de outro período"
    assert {v["afirmacao"] for v in d.vereditos_aplicados} == {0, 1}  # trace guarda os dois selos
    j = d.justificativa()
    assert "selos de checagem considerados: VERDADEIRO (Agencia A)" in j
    assert "Agencia B" not in j
