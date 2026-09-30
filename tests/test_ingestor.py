"""Ingestor: veredito (ClaimReview > título > categoria > corpo), merge idempotente, exclusões. Offline."""
import gzip
import json
from pathlib import Path

import pytest

from factcheck_mvp import ingestor as ing

FIX = Path(__file__).parent / "fixtures" / "jsonld"


@pytest.mark.parametrize("titulo,selo,afirm", [
    ("É #FAKE que Lula menosprezou garis; vídeo foi cortado", "#FAKE", "Lula menosprezou garis"),
    ("É #FATO: Voto nas eleições vale como prova de vida", "#FATO", "Voto nas eleições vale como prova de vida"),
    ("É FATO: Vaca se levanta após ser atingida por trem", "FATO", "Vaca se levanta após ser atingida por trem"),
    ("É falso que Cristiano Ronaldo anunciou compra do Santa Cruz", "falso",
     "Cristiano Ronaldo anunciou compra do Santa Cruz"),
    ("Não é verdade que Lula tenha proposta para salário de R$ 3 mil; vídeos são IA", "não é verdade",
     "Lula tenha proposta para salário de R$ 3 mil"),
    ("Nikolas Ferreira não disse que Virgem Maria foi estuprada", "não disse",
     "Nikolas Ferreira disse que Virgem Maria foi estuprada"),
    ("Vídeo não mostra ato de Flávio em Recife, mas festa em Campina Grande", "não mostra",
     "Vídeo mostra ato de Flávio em Recife"),
    ("Áudio atribuído a VAR da expulsão de Osório é falso", "falso", "Áudio atribuído a VAR da expulsão de Osório"),
    ("Não há registros de pagamentos de Vorcaro a Flávio Dino", "Não há registros",
     "Pagamentos de Vorcaro a Flávio Dino"),
])
def test_veredito_do_titulo(titulo, selo, afirm):
    s, a = ing.veredito_do_titulo(titulo)
    assert s == selo and a == afirm


@pytest.mark.parametrize("titulo", [
    "É verdade que o presidente Lula construiu uma ponte gigante em São Paulo?",  # pergunta (E-farsas)
    "Veja o que é #FATO ou #FAKE no debate de candidatos ao Senado",              # resumo múltiplo
    "Checamos o discurso de Lula na ONU",
    "Flávio mentiu ao menos 43 vezes sobre elo com Vorcaro",
])
def test_titulo_sem_selo(titulo):
    assert ing.veredito_do_titulo(titulo)[0] is None


def test_veredito_do_corpo_e_categorias():
    assert ing.veredito_do_corpo("Um vídeo alega que X gastou R$ 7 bi. É falso. Por WhatsApp…") == "É falso"
    assert ing.veredito_do_corpo("Boato – circula nas redes…") == "Boato"
    assert ing.veredito_do_corpo("Texto neutro sobre economia.") is None
    assert ing.veredito_das_categorias(["Conspirações", "Falso", "Humor"], "e-farsas") == "Falso"
    assert ing.veredito_das_categorias(["Falso", "Verdadeiro"], "e-farsas") is None  # ambíguo


def test_chave_e_canonizacao_url():
    a = ing.chave_url("http://www.e-farsas.com/x.html?utm_source=a#frag")
    b = ing.chave_url("https://e-farsas.com/x.html")
    assert a == b
    assert ing.chave_url("https://projetocomprova.com.br/publica%C3%A7%C3%B5es/x/") == \
        ing.chave_url("https://projetocomprova.com.br/publicações/x")
    assert ing.canonizar_url("https://A.com/p/?utm_source=x#f") == "https://a.com/p/"


RSS = b"""<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">
<channel><title>t</title>
<item><title>&#201; falso que caf&#233; cura c&#226;ncer</title><link>https://ag.test/a/?utm_source=rss</link>
<pubDate>Mon, 28 Sep 2026 10:00:00 +0000</pubDate><content:encoded><![CDATA[<p>Boato &#8211; texto</p>]]></content:encoded></item>
<item><title>Checagem com ClaimReview</title><link>https://ag.test/b/</link><category>Falso</category></item>
<item><title>Reportagem sem selo</title><link>https://ag.test/c/</link></item>
<item><title>Duplicada</title><link>https://ag.test/a/</link></item>
</channel></rss>"""

ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Falso: X</title>
<link rel="alternate" href="https://ag.test/atom"/><updated>2026-09-01T00:00:00Z</updated>
<category term="S&#225;tira"/></entry></feed>"""


def test_parse_feed_rss_e_atom():
    itens = ing.parse_feed(RSS)
    assert len(itens) == 4 and itens[0]["titulo"] == "É falso que café cura câncer"
    assert itens[0]["data"].startswith("2026-09-28") and "Boato" in ing.texto_de_html(itens[0]["corpo_html"])
    assert itens[1]["categorias"] == ["Falso"]
    at = ing.parse_feed(ATOM)
    assert at[0]["link"] == "https://ag.test/atom" and at[0]["categorias"] == ["Sátira"]
    assert ing.parse_feed(b"lixo") == []


def test_montar_registro_claimreview_vence_titulo():
    html = gzip.decompress((FIX / "comprova.html.gz").read_bytes()).decode()
    item = {"titulo": "Áudio de Renan Santos foi criado por IA", "link": "https://projetocomprova.com.br/x/",
            "data": None, "corpo_html": "", "categorias": []}
    r = ing.montar_registro(item, "comprova", html)
    assert r["origem"] == "claimreview" and r["veredito"] == "FALSO" and r["selo_original"] == "Falso"
    assert "Intercept" in r["afirmacao_checada"]
    assert set(r) >= {"url", "titulo", "agencia", "afirmacao_checada", "selo_original", "veredito",
                      "data_pub", "trecho", "origem"}


def test_mesclar_idempotente_manual_e_exclusoes():
    velho = [{"url": "https://a.test/1", "veredito": "FALSO", "origem": "rss-titulo"},
             {"url": "https://a.test/2", "veredito": None, "origem": "manual", "nota": "curado"},
             {"url": "https://a.test/3", "veredito": None, "origem": "rss-titulo"}]
    novo = [{"url": "http://www.a.test/1/", "veredito": None, "origem": "rss-titulo"},   # não apaga veredito
            {"url": "https://a.test/2", "veredito": "FALSO", "origem": "claimreview"},  # manual fica
            {"url": "https://a.test/4", "veredito": "FALSO", "origem": "claimreview"}]
    m = ing.mesclar(velho, novo, excluidas={ing.chave_url("https://a.test/3")})
    por = {ing.chave_url(r["url"]): r for r in m}
    assert por["a.test/1"]["veredito"] == "FALSO"
    assert por["a.test/2"]["origem"] == "manual"
    assert "a.test/3" not in por and "a.test/4" in por
    assert ing.mesclar(m, novo, {ing.chave_url("https://a.test/3")}) == m  # idempotente


def test_ingerir_offline(tmp_path, monkeypatch):
    cr = ('<script type="application/ld+json">{"@type":"ClaimReview","claimReviewed":"Y é verdade",'
          '"reviewRating":{"alternateName":"Enganoso"}}</script>')
    paginas = {"https://feed.test/rss": (200, RSS), "https://ag.test/b/": (200, cr.encode())}

    def falso_baixar(self, url):
        cod, corpo = paginas.get(url, (404, b""))
        return cod, corpo, url

    monkeypatch.setattr(ing.Baixador, "baixar", falso_baixar)
    monkeypatch.setattr(ing, "FONTES", [{"agencia": "ag", "nome": "Ag", "feeds": ["https://feed.test/rss"]}])
    monkeypatch.setattr(ing, "ARQ_CATALOGO", tmp_path / "nao-existe.json")
    excl = tmp_path / "excl.json"
    excl.write_text(json.dumps({"checagens": [{"url": "https://ag.test/c"}]}), encoding="utf-8")
    monkeypatch.setattr(ing, "ARQ_EXCLUIDAS", excl)
    monkeypatch.setattr(ing, "INTERVALO_HOST", 0.0)
    saida = tmp_path / "checagens.jsonl"
    res = ing.ingerir(max_por_feed=10, saida=saida, log=lambda *a: None)
    regs = [json.loads(l) for l in saida.read_text(encoding="utf-8").splitlines()]
    por = {ing.chave_url(r["url"]): r for r in regs}
    assert set(por) == {"ag.test/a", "ag.test/b"}          # c excluída, duplicada deduplicada
    assert por["ag.test/a"]["veredito"] == "FALSO" and por["ag.test/a"]["origem"] == "rss-titulo"
    assert por["ag.test/a"]["url"] == "https://ag.test/a/"  # sem utm
    assert por["ag.test/b"]["origem"] == "claimreview" and por["ag.test/b"]["veredito"] == "ENGANOSO"
    assert res["estatisticas"]["ag"]["n"] == 2
    ing.ingerir(max_por_feed=10, saida=saida, log=lambda *a: None)  # 2ª execução: mesmo conteúdo
    assert len(saida.read_text(encoding="utf-8").splitlines()) == 2


def test_mesclar_nao_rebaixa_claimreview():
    velho = [{"url": "https://a.test/1", "veredito": "ENGANOSO", "origem": "claimreview"}]
    novo = [{"url": "https://a.test/1", "veredito": "FALSO", "origem": "rss-titulo"}]
    assert ing.mesclar(velho, novo)[0]["origem"] == "claimreview"
