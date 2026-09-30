"""Catálogo: roteamento por URL (prefixo + aliases + eTLD+1), nome só por igualdade, curadoria."""
import pytest

from factcheck_mvp.catalogo import Catalogo, dominio_registravel


def _cat():
    return Catalogo([
        {"id": "g1", "nome": "G1", "tipo": "geral", "homepage": "https://g1.globo.com/"},
        {"id": "fato-ou-fake", "nome": "Fato ou Fake (G1)", "tipo": "checagem",
         "homepage": "https://g1.globo.com/fato-ou-fake/", "aliases_nome": ["Fato ou Fake"]},
        {"id": "oglobo", "nome": "O Globo", "tipo": "geral", "homepage": "https://oglobo.globo.com/"},
        {"id": "estadao", "nome": "Estadão", "tipo": "geral", "homepage": "https://www.estadao.com.br/"},
        {"id": "estadao-verifica", "nome": "Estadão Verifica", "tipo": "checagem",
         "homepage": "https://www.estadao.com.br/estadao-verifica/"},
        {"id": "lupa", "nome": "Lupa", "tipo": "checagem", "homepage": "https://lupa.uol.com.br/",
         "aliases": ["agencialupa.org", "piaui.folha.uol.com.br/lupa"], "aliases_nome": ["Agência Lupa"]},
        {"id": "folha", "nome": "Folha de S.Paulo", "tipo": "geral", "homepage": "https://www.folha.uol.com.br/"},
        {"id": "blog-auto", "nome": "Ações contra a dengue", "tipo": "geral", "homepage": "https://blog.exemplo.org.br/",
         "origem_catalogo": "descoberta-automatica"},
        {"id": "manual-cand", "nome": "Candidato", "tipo": "geral", "homepage": "https://cand.com.br/",
         "status": "candidato"},
    ])


@pytest.mark.parametrize("url,esperado", [
    ("https://www.agencialupa.org/verificacao/2026/09/25/x/", "lupa"),      # alias (A3)
    ("https://lupa.uol.com.br/jornalismo/x", "lupa"),
    ("https://piaui.folha.uol.com.br/lupa/2019/x", "lupa"),                # alias com caminho
    ("https://piaui.folha.uol.com.br/materia/x", "folha"),                # subdomínio da Folha
    ("https://g1.globo.com/fato-ou-fake/noticia/2026/x.ghtml", "fato-ou-fake"),  # caminho mais longo vence
    ("https://g1.globo.com/politica/noticia/x.ghtml", "g1"),
    ("https://www.estadao.com.br/estadao-verifica/x/", "estadao-verifica"),
    ("https://esportes.estadao.com.br/x", "estadao"),
    ("https://valor.globo.com/x", None),        # globo.com é compartilhado: eTLD+1 ambíguo
    ("https://m.blog.exemplo.org.br/x", "blog-auto"),
    ("https://exemplo.org.br/x", "blog-auto"),  # eTLD+1 com um único dono
    ("https://desconhecido.com/x", None),
    ("", None),
])
def test_por_url(url, esperado):
    p = _cat().por_url(url)
    assert (p["id"] if p else None) == esperado


def test_por_url_apenas_curados():
    c = _cat()
    assert c.por_url("https://blog.exemplo.org.br/x", apenas_curados=True) is None


@pytest.mark.parametrize("nome,esperado", [
    ("", None), (None, None), ("   ", None),
    ("Agência Lupa", "lupa"), ("agencia lupa", "lupa"), ("Lupa", "lupa"),
    ("Estadão", "estadao"), ("ESTADAO", "estadao"), ("Estadão Mato Grosso", None),   # sem substring
    ("G1", "g1"), ("Fato ou Fake", "fato-ou-fake"), ("agencialupa.org", "lupa"),
    ("g1.globo.com", "g1"), ("Folha", "folha"), ("Folha Vitória", None),
])
def test_por_nome_fonte_so_igualdade(nome, esperado):
    p = _cat().por_nome_fonte(nome)
    assert (p["id"] if p else None) == esperado


def test_por_dominio_legado_com_alias():
    c = _cat()
    assert c.por_dominio("https://www.agencialupa.org/x")["id"] == "lupa"
    assert c.por_dominio("https://noticias.exemplo.test/") is None


def test_curadoria():
    c = _cat()
    assert c.eh_curado(c.por_url("https://g1.globo.com/x"))
    assert not c.eh_curado(c.por_url("https://blog.exemplo.org.br/x"))  # descoberta-automatica
    assert c.status(c.por_url("https://cand.com.br/")) == "candidato"
    assert not c.eh_curado(None)
    assert {p["id"] for p in c.candidatos()} == {"blog-auto", "manual-cand"}


def test_catalogo_real_lupa_e_candidatos():
    c = Catalogo.carregar()
    assert c.por_url("https://www.agencialupa.org/verificacao/2026/09/25/x")["id"] == "lupa"
    assert c.por_nome_fonte("") is None
    auto = [p for p in c.portais if p.get("origem_catalogo") == "descoberta-automatica"]
    assert auto and all(not c.eh_curado(p) for p in auto)


def test_dominio_registravel():
    assert dominio_registravel("noticias.uol.com.br") == "uol.com.br"
    assert dominio_registravel("g1.globo.com") == "globo.com"
    assert dominio_registravel("https://www.justicaeleitoral.jus.br/fato-ou-boato/") == "justicaeleitoral.jus.br"
    assert dominio_registravel("agencialupa.org") == "agencialupa.org"
