"""Bot e web: o que cada fonte faz, em linguagem simples e coerente com a decisão."""
from factcheck_mvp.agregador import direcoes_por_url, postura_legivel, verificar_neutralidade
from factcheck_mvp.schemas import EntradaConsulta, EtapaRecibo, FonteEvidencia, RelatorioChecagem


def _fonte(url, postura, **kw):
    kw.setdefault("titulo", "Título da notícia")
    kw.setdefault("portal_nome", "Portal")
    return FonteEvidencia(url=url, postura=postura,
                          relevante=postura != "NAO_TRATA", **kw)


def _decisao(*votos):
    return {"votos": [{"urls": [u], "direcao": d} for u, d in votos]}


def test_rotulo_vem_do_voto_e_respeita_a_negacao():
    # Usuário NEGA o núcleo: a página que REFUTA o núcleo confirma o que ele disse,
    # e a decisão registra isso como voto de direção -1.
    f = _fonte("https://a.test/1", "REFUTA")
    assert postura_legivel(f, direcoes_por_url(_decisao(("https://a.test/1", -1.0)))) \
        == "confirma o que o texto afirma"
    assert postura_legivel(f, direcoes_por_url(_decisao(("https://a.test/1", 1.0)))) \
        == "contesta o que o texto afirma"


def test_sem_voto_usa_a_postura_sem_direcao():
    assert postura_legivel(_fonte("https://a.test/2", "RELATA_SEM_ENDOSSO"), {}) \
        == "relata o assunto sem tomar posição"
    assert postura_legivel(_fonte("https://a.test/3", None), {}) == "não avaliada"


def _rel():
    return RelatorioChecagem(
        propensao="alta", justificativa="Alta propensão de ser fake news.",
        header="🔴 Alta propensão de ser fake news",
        consulta=EntradaConsulta(tipo="texto", conteudo="Governo vai confiscar a poupança"),
        fontes=[_fonte("https://a.test/1", "REFUTA", corpo_lido=True, confiabilidade="alto_trafego"),
                _fonte("https://a.test/fora", "NAO_TRATA")],
        etapas=[EtapaRecibo(nome="juiz", status="ok"), EtapaRecibo(nome="base-checagem", status="parcial")],
        limitacoes=["Uma limitação."],
        decisao=_decisao(("https://a.test/1", 1.0)))


def test_bot_mostra_postura_leitura_e_etapas_legiveis():
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel())
    assert "Portal contesta o que o texto afirma (📄 texto lido · site muito acessado)" in t
    assert "a.test/fora" not in t  # fora do tema não vira evidência
    assert "✅ Análise das fontes" in t and "⚠️ Base de checagens" in t
    assert "juiz:ok" not in t
    assert verificar_neutralidade(t) == []


def test_web_mostra_postura_e_esconde_fora_do_tema():
    from factcheck_mvp.api import _render_html
    h = _render_html("x", _rel())
    assert "contesta o que o texto afirma" in h and "📄 texto lido" in h and "site muito acessado" in h
    assert "a.test/fora" not in h
    assert "Análise das fontes" in h


def _rel_e3():
    # E3: 1 fonte lida + 1 só título (corpo_lido=False, em Decisao.nao_analisadas).
    return RelatorioChecagem(
        propensao="media", justificativa="Evidência fraca ou dividida.",
        header="🟡 Propensão média de ser fake news",
        consulta=EntradaConsulta(tipo="texto", conteudo="Governo vai confiscar a poupança"),
        fontes=[_fonte("https://a.test/lida", "REFUTA", corpo_lido=True,
                       confiabilidade="alto_trafego", titulo="Titulo da fonte lida",
                       portal_nome="PortalLido"),
                _fonte("https://a.test/so-titulo", "REFUTA", corpo_lido=False,
                       confiabilidade="alto_trafego", titulo="Titulo da fonte so manchete",
                       portal_nome="PortalTitulo")],
        etapas=[], limitacoes=[],
        decisao={**_decisao(("https://a.test/lida", 1.0)),
                 "nao_analisadas": [{"url": "https://a.test/so-titulo", "classe": "REFUTA",
                                      "veredito": None, "afirmacao": 0}]})


def test_e3_bot_marca_so_titulo_como_nao_analisada_integralmente():
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel_e3())
    assert t.count("não analisada integralmente") == 1
    itens = [l for l in t.splitlines() if l[:2] in ("1.", "2.")]
    assert len(itens) == 2
    assert "não analisada integralmente" not in itens[0]
    assert "não analisada integralmente" in itens[1]
    assert verificar_neutralidade(t) == []


def test_e3_web_marca_so_titulo_como_nao_analisada_integralmente():
    from factcheck_mvp.api import _render_html
    h = _render_html("x", _rel_e3())
    assert h.count("não analisada integralmente") == 1
    cartoes = h.split("<div")
    lido = next(c for c in cartoes if "a.test/lida" in c)
    titulo = next(c for c in cartoes if "a.test/so-titulo" in c)
    assert "não analisada integralmente" not in lido
    assert "não analisada integralmente" in titulo


# ------------------------------------------------------------------ B1a: raciocínio do avaliador
# Texto livre do LLM, sempre ATRIBUÍDO ao avaliador automático. A neutralidade vale para a
# NOSSA conclusão: a varredura ignora só o bloco atribuído ("Avaliação automática: ...").
RAC_FALSO = "A página diz que é falso que o governo vá confiscar a poupança, citando a lei."


def _rel_rac(rac, url="https://a.test/rac"):
    return RelatorioChecagem(
        propensao="alta", justificativa="Alta propensão de ser fake news.",
        header="🔴 Alta propensão de ser fake news",
        consulta=EntradaConsulta(tipo="texto", conteudo="Governo vai confiscar a poupança"),
        fontes=[_fonte(url, "REFUTA", corpo_lido=True, confiabilidade="alto_trafego", raciocinio=rac)],
        etapas=[], limitacoes=[], decisao=_decisao((url, 1.0)))


def _visivel(h):
    """Texto visível do HTML: quebra de linha onde há <br> ou bloco, entidades desfeitas."""
    import html as _h
    import re as _re
    return _h.unescape(_re.sub(r"<[^>]+>", "", _re.sub(r"<br\s*/?>|</(?:div|p|li|h\d)>", "\n", h)))


def test_bot_mostra_raciocinio_atribuido_por_fonte():
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel_rac("A página nega que a poupança será confiscada."))
    assert "🧠 Avaliação automática: A página nega que a poupança será confiscada." in t
    assert verificar_neutralidade(t) == []


def test_bot_raciocinio_vazio_nao_mostra_linha():
    from factcheck_mvp.telegram_bot import formatar
    for vazio in (None, "", "   "):
        assert "Avaliação automática" not in formatar(_rel_rac(vazio))


def test_bot_raciocinio_com_quebra_vira_uma_linha():
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel_rac("Primeira frase.\nSegunda frase, sem quebra."))
    linha = next(l for l in t.splitlines() if "Avaliação automática" in l)
    assert linha.endswith("Primeira frase. Segunda frase, sem quebra.")


def test_bot_raciocinio_longo_e_cortado_so_na_exibicao():
    from factcheck_mvp.telegram_bot import RACIOCINIO_MAX_BOT, formatar
    longo = "A página afirma que " + "o café cura o câncer " * 40
    rel = _rel_rac(longo)
    linha = next(l for l in formatar(rel).splitlines() if "Avaliação automática" in l)
    exibido = linha.split("Avaliação automática: ", 1)[1]
    assert len(exibido) <= RACIOCINIO_MAX_BOT and exibido.endswith("…")
    assert rel.fontes[0].raciocinio == longo  # o dado em Fonte não muda


def test_bot_nao_corta_a_ultima_linha_quando_o_texto_cabe():
    """Bug: `texto[:3900].rsplit("\\n", 1)[0]` tirava SEMPRE a última linha (a última pergunta-guia).
    O corte só existe acima de 3900 caracteres."""
    from factcheck_mvp.telegram_bot import formatar
    rel = RelatorioChecagem(
        propensao="alta", justificativa="Alta propensão de ser fake news.",
        header="🔴 Alta propensão de ser fake news",
        consulta=EntradaConsulta(tipo="texto", conteudo="Governo vai confiscar a poupança"),
        perguntas_guia=["Pergunta um?", "Pergunta dois?", "Última pergunta-guia?"])
    t = formatar(rel)
    assert len(t) <= 3900
    assert "• Última pergunta-guia?" in t
    assert verificar_neutralidade(t) == []


def test_limitacao_de_datas_nao_some_do_bot_com_tres_limitacoes():
    """O bot mostra limitacoes[:3]; o aviso de datas era anexado no fim e sumia."""
    from factcheck_mvp.decisao import Decisao
    from factcheck_mvp.pipeline import Pipeline
    from factcheck_mvp.telegram_bot import formatar
    dec = Decisao(nivel="media", log_odds=0.5, prob=0.6, motivo="x",
                  descontos_temporais=[{"url": "https://g1.globo.com/a", "afirmacao": 0}],
                  travas={"data_incompativel": True}, nivel_sem_desconto="alta")
    rel = Pipeline._relatorio(EntradaConsulta(tipo="texto", conteudo="Governo vai confiscar a poupança"),
                              dec, [], [], [], ["L1", "L2", "L3"], "web")
    assert rel.limitacoes[0].startswith("Datas:")
    assert "• Datas:" in formatar(rel)
    assert verificar_neutralidade(formatar(rel)) == []


# ------------------------------------------------------------------ data exibida: precisão e normalizada
def _fonte_data(**kw):
    base = dict(url="https://g1.globo.com/a", titulo="Título", portal_nome="g1", corpo_lido=True,
                relevante=True, postura="SUSTENTA", relevancia_temporal=0.05)
    return FonteEvidencia(**{**base, **kw})


def _rel_com_fonte(f):
    return RelatorioChecagem(
        propensao="media", justificativa="Propensão média de ser fake news.",
        consulta=EntradaConsulta(tipo="texto", conteudo="Texto sobre fato de hoje aqui"),
        fontes=[f], decisao={"votos": [], "descontos_temporais": [{"url": f.url, "r": 0.05}]})


def test_data_publicacao_legivel_respeita_a_precisao():
    from factcheck_mvp.agregador import data_publicacao_legivel
    assert data_publicacao_legivel("2021-12-31", "ano") == "2021"
    assert data_publicacao_legivel("2026-10-06", "dia") == "06/10/2026"
    assert data_publicacao_legivel("2026-07-18", None) == "18/07/2026"
    assert data_publicacao_legivel(None, "dia") == ""
    assert data_publicacao_legivel("há 3 dias", None) == ""  # nunca o texto bruto


def test_bot_placeholder_de_ano_mostra_so_o_ano():
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel_com_fonte(_fonte_data(data_pub="2021-12-31", data_pub_precisao="ano",
                                            data_pub_bruta="2021-01-01")))
    assert "📅 publicada em 2021 · anterior ao período do texto" in t
    assert "01/01/2021" not in t


def test_bot_data_relativa_mostra_a_data_normalizada():
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel_com_fonte(_fonte_data(data_pub="2026-10-06", data_pub_precisao="dia",
                                            data_pub_bruta="há 3 dias")))
    assert "📅 publicada em 06/10/2026" in t and "há 3 dias" not in t


def test_bot_iso_com_fuso_mostra_o_dia_brt_usado_na_decisao():
    # 2026-07-19T02:00Z é 18/07 em BRT (UTC−3): a decisão usou 18/07 e é essa data que se mostra.
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel_com_fonte(_fonte_data(data_pub="2026-07-18", data_pub_precisao="dia",
                                            data_pub_bruta="2026-07-19T02:00:00+00:00")))
    assert "📅 publicada em 18/07/2026" in t and "19/07" not in t


def test_web_data_respeita_a_precisao_e_a_normalizada():
    from factcheck_mvp.api import _render_html
    h = _visivel(_render_html("x", _rel_com_fonte(_fonte_data(data_pub="2021-12-31", data_pub_precisao="ano",
                                                              data_pub_bruta="2021-01-01"))))
    assert "📅 publicada em 2021 · anterior ao período do texto" in h and "01/01/2021" not in h
    h2 = _visivel(_render_html("x", _rel_com_fonte(_fonte_data(data_pub="2026-10-06", data_pub_precisao="dia",
                                                               data_pub_bruta="há 3 dias"))))
    assert "📅 publicada em 06/10/2026" in h2 and "há 3 dias" not in h2


def test_fontes_leva_a_precisao_da_data_da_peca():
    from factcheck_mvp import decisao
    from factcheck_mvp.pipeline import Pipeline
    from factcheck_mvp.schemas import Afirmacao
    peca = {"url": "https://g1.globo.com/a", "titulo": "T", "veiculo": "g1", "corpo": "texto", "corpo_lido": True,
            "data_pub": "2021-12-31", "data_pub_precisao": "ano", "data_pub_bruta": "2021-01-01"}
    julg = {(0, 0): {"classe": "REFUTA", "citacao": "x", "citacao_verificada": True, "motor": "llm-juiz:x"}}
    dec = decisao.Decisao(nivel="media", log_odds=0.0, prob=0.5, motivo="x")
    f = Pipeline._fontes(None, [Afirmacao(texto="Governo vai confiscar a poupança")], [peca], julg, dec)[0]
    assert (f.data_pub, f.data_pub_precisao, f.data_pub_bruta) == ("2021-12-31", "ano", "2021-01-01")


def test_bot_e_web_sem_decisao_renderizam_sem_descontos():
    """Relatório sem `decisao` (snapshot antigo): sem descontos, sem aviso de data e sem erro."""
    from factcheck_mvp.api import _render_html
    from factcheck_mvp.telegram_bot import formatar
    rel = RelatorioChecagem(
        propensao="media", justificativa="Propensão média de ser fake news.",
        consulta=EntradaConsulta(tipo="texto", conteudo="Texto sobre fato de hoje aqui"),
        fontes=[_fonte("https://a.test/1", "REFUTA", corpo_lido=True, confiabilidade="alto_trafego")],
        decisao=None)
    assert "📅" not in formatar(rel)
    assert "Fontes com desconto por data" not in _render_html("x", rel)


def test_perguntas_guia_traz_a_de_data_primeiro_sem_parametro_inutil():
    """A pergunta de data já é a primeira da lista; o antigo `priorizar_data` não mudava nada."""
    import inspect
    from factcheck_mvp.agregador import perguntas_guia
    assert perguntas_guia()[0].startswith("Compare a data")
    assert list(inspect.signature(perguntas_guia).parameters) == []


def test_web_lista_descontos_por_data_em_como_chegamos_aqui_sem_bits_no_bot():
    """Task 6: a web mostra cada desconto (url escapada, dias além da janela, r e bits) no
    <details> 'Como chegamos aqui'; os bits ficam FORA do bot (técnico demais)."""
    from factcheck_mvp.api import _render_html
    from factcheck_mvp.telegram_bot import formatar
    rel = RelatorioChecagem(
        propensao="media", justificativa="Propensão média de ser fake news.",
        consulta=EntradaConsulta(tipo="texto", conteudo="Texto sobre fato de hoje aqui"),
        fontes=[_fonte("https://a.test/<x>", "SUSTENTA", corpo_lido=True, confiabilidade="alto_trafego")],
        etapas=[EtapaRecibo(nome="juiz", status="ok")],
        decisao={"votos": [], "descontos_temporais": [
            {"url": "https://a.test/<x>", "dias_alem_da_janela": 1905, "janela": 2, "r": 0.05,
             "bits_descartados": 0.876}]})
    h = _render_html("x", rel)
    inicio = h.index("Como chegamos aqui")
    bloco = h[inicio:h.index("</details>", inicio)]
    assert "https://a.test/&lt;x&gt;" in bloco and "<script>" not in h
    assert "1905 dias além da janela de 2" in bloco and "r=0.05" in bloco and "0.88 bit" in bloco
    t = formatar(rel)
    assert "bit" not in t.lower()


def test_bot_pior_caso_cabe_no_limite_e_mantem_o_fim():
    from factcheck_mvp.telegram_bot import formatar
    rac = "A página afirma que o café cura o câncer, citando um estudo de dois anos. " * 4  # > cap
    fontes = [_fonte(f"https://portal{i}.test/noticia/{'a' * 110}", "REFUTA", corpo_lido=True,
                     confiabilidade="alto_trafego", titulo="T" * 90, portal_nome=f"Portal {i}",
                     quote="q" * 140, raciocinio=rac, data_pub="2021-07-18",
                     data_pub_bruta="2021-07-18", relevancia_temporal=0.05) for i in range(3)]
    rel = RelatorioChecagem(
        propensao="alta", justificativa="J" * 400, header="🔴 " + "H" * 60, why_1linha="W" * 200,
        consulta=EntradaConsulta(tipo="texto", conteudo="Governo vai confiscar a poupança"),
        fontes=fontes,
        etapas=[EtapaRecibo(nome=n, status="ok") for n in
                ("recebimento", "afirmacoes", "base-checagem", "descoberta", "deep-crawl", "juiz")],
        limitacoes=["L" * 200] * 3, perguntas_guia=["P" * 100] * 3,
        decisao={"votos": [{"urls": [f.url], "direcao": 1.0} for f in fontes],
                 "descontos_temporais": [{"url": f.url, "r": 0.05} for f in fontes]})
    t = formatar(rel)
    assert len(t) <= 3900
    assert t.count("🧠 Avaliação automática:") == 3
    assert "Para avaliar você mesmo" in t


def test_bot_raciocinio_com_e_falso_so_passa_no_bloco_atribuido():
    from factcheck_mvp.telegram_bot import formatar
    t = formatar(_rel_rac(RAC_FALSO))
    assert "🧠 Avaliação automática: " + RAC_FALSO in t
    assert verificar_neutralidade(t) == []  # o bloco atribuído não conta
    assert verificar_neutralidade(t + "\nNossa conclusão: é falso.") != []  # nossa conclusão, não
    assert verificar_neutralidade(t.replace("Avaliação automática:", "Nossa leitura:")) != []


def test_neutralidade_so_ignora_linha_que_comeca_com_o_rotulo():
    from factcheck_mvp.agregador import linha_raciocinio
    assert verificar_neutralidade("🧠 Avaliação automática: a página diz que é falso.") == []
    assert verificar_neutralidade("   Avaliação automática: é falso que X") == []
    assert verificar_neutralidade("Nossa conclusão: Avaliação automática: é falso.") != []
    assert verificar_neutralidade("Avaliação automática: ok.\nNossa conclusão: é falso.") != []
    assert linha_raciocinio(None) == "" and linha_raciocinio("  \n ") == ""
    assert linha_raciocinio("a\nb") == "🧠 Avaliação automática: a b"
    assert linha_raciocinio("x" * 50, limite=10) == "🧠 Avaliação automática: " + "x" * 9 + "…"


def test_web_mostra_raciocinio_atribuido_e_escapado():
    from factcheck_mvp.api import _render_html
    h = _render_html("x", _rel_rac("<script>alert('x')</script> A página diz \"é falso\"."))
    assert "<script>" not in h and "&lt;script&gt;" in h and "&quot;é falso&quot;" in h
    assert "🧠 Avaliação automática:" in h
    assert verificar_neutralidade(_visivel(h)) == []  # o bloco atribuído não conta
    assert verificar_neutralidade(_visivel(h) + "\nNossa conclusão: é falso.") != []


def test_web_sem_raciocinio_nao_mostra_linha():
    from factcheck_mvp.api import _render_html
    assert "Avaliação automática" not in _render_html("x", _rel_rac(None))


def test_api_json_ja_expoe_raciocinio_por_fonte(monkeypatch):
    from fastapi.testclient import TestClient
    from factcheck_mvp import api
    rel = _rel_rac(RAC_FALSO)

    class _PipeFalso:
        catalogo = object()

        async def executar(self, entrada, **_):
            return rel

    monkeypatch.setattr(api, "pipeline", lambda: _PipeFalso())
    monkeypatch.setattr(api, "_rate_ok", lambda request: True)
    r = TestClient(api.app).post("/checar", json={"tipo": "texto", "conteudo": "Governo vai confiscar a poupança"})
    assert r.status_code == 200
    assert r.json()["fontes"][0]["raciocinio"] == RAC_FALSO
