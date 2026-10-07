"""Bot e web: o que cada fonte faz, em linguagem simples e coerente com a decisão."""
from factcheck_mvp.agregador import direcoes_por_url, postura_legivel, verificar_neutralidade
from factcheck_mvp.schemas import EntradaConsulta, EtapaRecibo, FonteEvidencia, RelatorioChecagem


def _fonte(url, postura, **kw):
    return FonteEvidencia(url=url, titulo="Título da notícia", portal_nome="Portal",
                          postura=postura, relevante=postura != "NAO_TRATA", **kw)


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
