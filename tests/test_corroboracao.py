"""Dedupe canônico e clusters de independência (antes do juiz)."""
import pytest

from factcheck_mvp import corroboracao as co

CORPO_ESTADAO = (
    "SÃO PAULO - O Ministério da Saúde divulgou nesta segunda-feira um balanço sobre a campanha de "
    "vacinação contra a gripe em todo o país. Segundo a pasta, a cobertura vacinal chegou a 62% do "
    "público-alvo, abaixo da meta de 90% estabelecida para o ano. Especialistas ouvidos pela reportagem "
    "afirmam que a hesitação vacinal e a desinformação nas redes sociais explicam parte da queda. "
    "O ministério planeja ampliar os horários dos postos e fazer mutirões aos sábados. "
    "(Estadão Conteúdo)")


def test_url_canonica_remove_tracking_www_amp_fragmento():
    base = co.url_canonica("https://g1.globo.com/saude/noticia/2024/01/x.ghtml")
    for u in ["http://www.g1.globo.com/saude/noticia/2024/01/x.ghtml?utm_source=tw&utm_medium=s",
              "https://g1.globo.com/saude/noticia/2024/01/x.ghtml#comentarios",
              "https://g1.globo.com/saude/noticia/2024/01/x.ghtml/amp",
              "https://amp.g1.globo.com/saude/noticia/2024/01/x.ghtml?fbclid=abc",
              "https://g1.globo.com/saude/noticia/2024/01/x.ghtml/"]:
        assert co.url_canonica(u) == base, u
    # parâmetro de conteúdo (id) é preservado
    assert co.url_canonica("https://youtube.com/watch?v=abc") != co.url_canonica("https://youtube.com/watch?v=xyz")


def test_mesma_url_indice_e_web_vira_uma_peca():
    pecas = [
        {"url": "https://www.aosfatos.org/noticias/x/", "afs": {0}, "origens": {"indice"},
         "veredito": "FALSO", "selo_original": "Falso", "titulo": "É falso que X"},
        {"url": "https://aosfatos.org/noticias/x?utm_source=google", "afs": {0}, "origens": {"web"},
         "snippet": "trecho da busca", "titulo": "É falso que X"},
    ]
    unicas, fusoes = co.fundir_por_url(pecas)
    assert len(unicas) == 1 and len(fusoes) == 1
    assert unicas[0]["origens"] == {"indice", "web"} and unicas[0]["veredito"] == "FALSO"
    assert unicas[0]["snippet"] == "trecho da busca"


def test_quatro_dominios_com_corpo_identico_de_agencia_um_cluster():
    pecas = [{"url": f"https://{d}/noticia/vacinacao-gripe", "titulo": f"Título editado {i}", "corpo": CORPO_ESTADAO}
             for i, d in enumerate(["istoe.com.br", "em.com.br", "gazetadopovo.com.br", "correiobraziliense.com.br"])]
    r = co.agrupar(pecas)
    assert r["n"] == 1
    assert all(p["cluster"] == "agencia:estadao-conteudo" for p in pecas)


def test_corpo_quase_identico_sem_assinatura_um_cluster():
    corpo = CORPO_ESTADAO.replace("(Estadão Conteúdo)", "")
    pecas = [{"url": "https://a.com.br/x", "titulo": "A", "corpo": corpo},
             {"url": "https://b.com.br/y", "titulo": "B", "corpo": corpo + " Leia também: outras notícias."}]
    assert co.agrupar(pecas)["n"] == 1


def test_veiculos_independentes_clusters_distintos():
    pecas = [{"url": "https://g1.globo.com/a", "titulo": "Vacina não altera DNA, dizem cientistas",
              "corpo": "Texto próprio do G1 sobre a vacina e o material genético humano " * 5},
             {"url": "https://www.bbc.com/portuguese/b", "titulo": "Como funciona a vacina de mRNA",
              "corpo": "Reportagem da BBC explica o mecanismo das vacinas de RNA mensageiro " * 5},
             {"url": "https://lupa.uol.com.br/c", "titulo": "Post engana ao dizer que vacina muda DNA",
              "corpo": "A Lupa verificou a publicação que circula nas redes e concluiu " * 5}]
    assert co.agrupar(pecas)["n"] == 3


def test_mencao_no_meio_do_texto_nao_e_assinatura():
    corpo = ("Abertura da reportagem própria com vários parágrafos. " * 10
             + "O ministro disse à Reuters que não comentaria. " + "Fechamento próprio do texto. " * 12)
    assert co.agencia_assinada(corpo) is None
    assert co.agencia_assinada("Texto qualquer da matéria.\nCom informações da Agência Brasil") == "agencia-brasil"


def test_mesmo_grupo_conta_1x():
    pecas = [
        {"url": "https://noticias.uol.com.br/a", "titulo": "Economia cresce, diz estudo",
         "corpo_texto": "texto completamente diferente um"},
        {"url": "https://www1.folha.uol.com.br/b", "titulo": "Outro ângulo da economia",
         "corpo_texto": "texto completamente diferente dois"},
        {"url": "https://www.bbc.com/portuguese/c", "titulo": "Terceira visão",
         "corpo_texto": "texto completamente diferente tres"},
    ]
    assert co.contar_independentes(pecas)["n"] == 2  # UOL+Folha = 1 grupo


def test_dominio_base_agrupa_grupo_economico():
    assert co.dominio_base("https://www.uol.com.br/x") == "uol.com.br"
    assert co.dominio_base("https://acervo.folha.uol.com.br/y") == "uol.com.br"
    assert co.dominio_base("https://g1.globo.com/z") == "globo.com"
    assert co.dominio_base("https://www.bbc.com/portuguese/a") == "bbc.com"


def test_divergencia_de_data_usa_data_pub():
    pecas = [{"url": "https://a.com/x", "cluster": "a.com", "data_pub": "2019-03-01"},
             {"url": "https://b.com/y", "cluster": "b.com", "data_pub": "2026-09-01T10:00:00"}]
    div = co.divergencias(pecas)
    assert div and div[0]["campo"] == "data"


# --- Revisão I1: a data viaja inteira (valor, precisão, bruta, tier) na fusão por URL ---
_URL_I1 = "https://www.jornal-exemplo.com.br/politica/2026/10/caso-x.html"


def _peca_com_data(url, data, precisao, bruta):
    return {"url": url, "titulo": "Caso X", "afs": {0}, "origens": {"web"},
            "data_pub": data, "data_pub_precisao": precisao, "data_pub_bruta": bruta}


@pytest.mark.parametrize("duplicada, esperado", [
    # A1: relativa ancorada (dia, tier 3) substitui a peça sem data, com precisão e bruta juntas
    (_peca_com_data(_URL_I1 + "?utm_source=news", "2026-10-07", "dia", "há 2 dias"),
     ("2026-10-07", "dia", "há 2 dias")),
    # A2: só ano (tier 2) substitui a peça sem data, com a precisão junto
    (_peca_com_data(_URL_I1 + "?utm_source=news", "2021-12-31", "ano", "2021"),
     ("2021-12-31", "ano", "2021")),
])
def test_fusao_copia_a_data_inteira_da_duplicata(duplicada, esperado):
    unicas, _ = co.fundir_por_url([_peca_com_data(_URL_I1, None, None, None), duplicada])
    assert (unicas[0]["data_pub"], unicas[0]["data_pub_precisao"], unicas[0]["data_pub_bruta"]) == esperado


def test_fusao_relativa_sem_ancora_cede_a_data_absoluta_da_duplicata():
    """A3: a peça mantida só tem a data relativa sem âncora; a duplicata traz a absoluta."""
    unicas, _ = co.fundir_por_url([
        _peca_com_data(_URL_I1, None, None, "há 3 dias"),
        _peca_com_data(_URL_I1 + "?utm_source=news", "2026-10-06", "dia", "6 de out. de 2026")])
    assert (unicas[0]["data_pub"], unicas[0]["data_pub_precisao"], unicas[0]["data_pub_bruta"]) == (
        "2026-10-06", "dia", "6 de out. de 2026")


def test_fusao_mantem_a_data_de_maior_tier_quando_a_duplicata_e_relativa():
    unicas, _ = co.fundir_por_url([
        _peca_com_data(_URL_I1, "2026-10-06", "dia", "6 de out. de 2026"),
        _peca_com_data(_URL_I1 + "?utm_source=news", "2026-10-07", "dia", "há 2 dias")])
    assert (unicas[0]["data_pub"], unicas[0]["data_pub_bruta"]) == ("2026-10-06", "6 de out. de 2026")
