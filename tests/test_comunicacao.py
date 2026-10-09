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
