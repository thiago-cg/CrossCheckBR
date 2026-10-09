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
