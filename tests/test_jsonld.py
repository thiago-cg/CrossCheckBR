"""JSON-LD: ClaimReview/NewsArticle em HTML real (baixado em 28/09/2026) e casos sintéticos."""
import gzip
from pathlib import Path

import pytest

from factcheck_mvp import jsonld

FIX = Path(__file__).parent / "fixtures" / "jsonld"


def _html(nome):
    return gzip.decompress((FIX / f"{nome}.html.gz").read_bytes()).decode("utf-8", "replace")


@pytest.mark.parametrize("nome,selo,trecho_afirm,veredito,agencia", [
    ("aosfatos", "falso", "HELICÓPTERO QUE LEVAVA CANTOR RICK", "FALSO", "Aos Fatos"),       # @type lista
    ("estadao_verifica", "Enganoso", "acabar com as bets", "ENGANOSO", None),                # url relativa
    ("comprova", "Falso", "Renan Santos", "FALSO", "Comprova"),                              # bloco = lista
])
def test_claimreview_real(nome, selo, trecho_afirm, veredito, agencia):
    r = jsonld.extrair(_html(nome))
    assert len(r["claim_reviews"]) == 1
    cr = r["claim_reviews"][0]
    assert cr["selo_original"] == selo
    assert trecho_afirm in cr["afirmacao_checada"]
    assert cr["agencia"] == agencia
    assert cr["data_pub"]
    assert set(cr) >= {"afirmacao_checada", "selo_original", "url", "autor", "data_pub", "agencia"}
    v = jsonld.veredito_da_pagina(_html(nome), cr["url"] if (cr["url"] or "").startswith("http") else None)
    assert v["veredito"] == veredito and v["origem"] == "claimreview"


def test_veredito_da_pagina_usa_id_do_catalogo():
    v = jsonld.veredito_da_pagina(_html("estadao_verifica"),
                                  "https://www.estadao.com.br/estadao-verifica/enganoso-bets/")
    assert v["agencia"] == "estadao-verifica" and v["veredito"] == "ENGANOSO"


def test_newsarticle_lupa_articlebody_e_autor_por_id():
    r = jsonld.extrair(_html("lupa"))
    assert r["claim_reviews"] == []
    assert r["headline"].startswith("É falso que Lula gastou")
    assert len(r["article_body"]) > 2000 and "É falso." in r["article_body"][:400]
    assert r["autor"]  # author vem como {"@id": …} resolvido no @graph
    assert jsonld.veredito_da_pagina(_html("lupa"), "https://www.agencialupa.org/x") is None


@pytest.mark.parametrize("nome", ["boatos", "efarsas", "g1_fato_ou_fake"])
def test_paginas_sem_claimreview_nao_inventam(nome):
    r = jsonld.extrair(_html(nome))
    assert r["claim_reviews"] == []
    assert jsonld.veredito_da_pagina(_html(nome)) is None


def test_graph_lista_tipo_lista_json_invalido_e_entidades():
    html = """
    <script type="application/ld+json">{ isto não é json </script>
    <script type='application/ld+json'>
    {"@context":"https://schema.org","@graph":[
      {"@type":"Organization","@id":"#org","name":"Ag&ecirc;ncia X"},
      {"@type":["Review","ClaimReview"],"claimReviewed":"Caf&eacute; cura c&acirc;ncer",
       "author":{"@id":"#org"},"datePublished":"2026-09-01",
       "reviewRating":{"@type":"Rating","alternateName":"Distorcido","ratingValue":2}}]}
    </script>
    <script type="application/ld+json">[{"@type":"NewsArticle","headline":"T &#8211; x",
      "articleBody":"corpo &amp; mais","author":[{"name":"A"},{"name":"B"}]}]</script>"""
    r = jsonld.extrair(html)
    cr = r["claim_reviews"][0]
    assert cr["afirmacao_checada"] == "Café cura câncer"
    assert cr["selo_original"] == "Distorcido"
    assert cr["agencia"] == "Agência X"
    assert r["headline"] == "T – x" and r["article_body"] == "corpo & mais" and r["autor"] == "A, B"
    v = jsonld.veredito_da_pagina(html)
    assert v["veredito"] == "ENGANOSO"


def test_rating_so_numerico_nao_vira_selo():
    html = ('<script type="application/ld+json">{"@type":"ClaimReview","claimReviewed":"X",'
            '"reviewRating":{"ratingValue":1,"bestRating":5}}</script>')
    v = jsonld.veredito_da_pagina(html)
    assert v["selo_original"] is None and v["veredito"] is None


def test_html_vazio_ou_sem_jsonld():
    assert jsonld.extrair("")["claim_reviews"] == []
    assert jsonld.veredito_da_pagina("<html><body>nada</body></html>") is None
