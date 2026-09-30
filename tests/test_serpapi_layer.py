"""SerpAPI: queries, normalização e roteamento (sem rede, sem denylist)."""
import json
from pathlib import Path

from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.catalogo import Catalogo

FIX = Path(__file__).parent / "fixtures" / "serpapi_bets.json"


def test_queries_pt_br():
    qs = camada.construir_queries("bets são proibidas no brasil", engine="google_news")
    assert len(qs) == 2
    assert all(q["hl"] == "pt-br" and q["gl"] == "br" for q in qs)
    assert "checagem" in qs[1]["q"]


def test_normaliza_item():
    bruto = json.loads(FIX.read_text(encoding="utf-8"))
    n = camada.normalizar_item(bruto["news_results"][0])
    assert n["titulo"].startswith("'Se depender")
    assert n["url"].startswith("https://noticias.uol.com.br")
    assert n["data_publicacao"] == "2026-04-08T07:00:00Z"
    assert n["corpo_texto"] is None and n["veredito"] is None  # parcial: exige deep crawl


def test_roteamento_catalogo_sem_bloqueio():
    bruto = json.loads(FIX.read_text(encoding="utf-8"))
    # Catálogo isolado (não o production, que cresce via descoberta automática)
    cat = Catalogo([{"id": "uol", "nome": "UOL", "tipo": "geral",
                     "homepage": "https://www.uol.com.br/"}])
    por_url = {}
    for item in bruto["news_results"]:
        p = camada.rotear_fonte(camada.normalizar_item(item), cat)
        por_url[item["link"]] = (p["fonte"].get("id"), p["catalogada"])
    assert por_url["https://noticias.uol.com.br/politica/ultimas-noticias/2026/04/08/lula-critica-fechar-bets.ghtm"][0] == "uol"
    assert por_url["https://exame.com/economia/bets-foram-barradas-por-falta-de-documentos-e-de-idoneidade-diz-secretario-de-apostas/"] == ("", False)
    # spam afiliado NÃO é bloqueado por domínio (sem denylist): segue p/ filtro de relevância
    assert por_url["https://www.brasil247.com/sites/apostas/"] == ("", False)


def test_cliente_sem_chave_desliga():
    c = camada.SerpAPIClient(api_key="")
    assert c.ativo is False
    assert c.buscar({"q": "x"}) is None


def test_teto_diario_bloqueia_sem_rede_e_marca_motivo(monkeypatch):
    import httpx as _hx
    import factcheck_mvp.config as cfg
    monkeypatch.setattr(cfg, "SERPAPI_DAILY_CAP", 1)
    calls = []

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"news_results": [{"link": "https://x.com/a", "title": "T",
                                      "source": {}}]}

    monkeypatch.setattr(_hx, "get", lambda *a, **k: (calls.append(1), FakeResp())[1])
    c = camada.SerpAPIClient(api_key="k")
    assert c.buscar({"q": "a"}) is not None
    assert c.buscar({"q": "b"}) is None  # teto: nem bate rede
    assert c.ultimo_motivo == "cap" and c.bloqueios_cap == 1
    assert len(calls) == 1


def test_erro_rede_marca_motivo(monkeypatch):
    import httpx as _hx
    monkeypatch.setattr(_hx, "get", lambda *a, **k: (_ for _ in ()).throw(_hx.ConnectError("dns")))
    c = camada.SerpAPIClient(api_key="k")
    assert c.buscar({"q": "a"}) is None
    assert c.ultimo_motivo == "erro" and c.erros_rede == 1


# --- Motor google orgânico: simples vs avançada ---

def test_simples_duas_queries_sem_operador_site():
    qs = camada.construir_queries("vacina causa autismo", engine="google", estrategia="simples")
    assert len(qs) == 2
    assert all(q["engine"] == "google" and q["hl"] == "pt-br" for q in qs)
    assert "site:" not in qs[0]["q"] and "site:" not in qs[1]["q"]
    assert "checagem" in qs[1]["q"] or "falso" in qs[1]["q"]


def test_avancada_duas_buscas_agencias_do_catalogo_sem_inurl():
    # Decisão live 23/09/2026: cadeia (site:inurl:) em OR retorna zero no
    # Google — o filtro avançado é 1 bloco site: puro (host ou host/caminho).
    qs = camada.construir_queries("vacina causa autismo", engine="google", estrategia="avancada")
    assert len(qs) == 2  # teto: 2 buscas por afirmação
    crua, filtro = qs
    assert "site:" not in crua["q"] and "inurl:" not in crua["q"]
    for s in camada.sites_checagem():  # consulta curta: todas as agências cabem em 32 palavras
        assert f"site:{s['site']}" in filtro["q"], f"agência sem cobertura: {s['site']}"
    assert "inurl:" not in filtro["q"] and len(filtro["q"].split()) <= 32


def test_agencias_derivadas_do_catalogo_sem_lista_fixa():
    cat = Catalogo([
        {"id": "ag", "nome": "Agência", "tipo": "checagem", "homepage": "https://www.agencia.org/",
         "aliases": ["agencia-nova.org"], "editorias": ["checagem", "verificação"]},
        {"id": "sec", "nome": "Seção", "tipo": "checagem", "homepage": "https://portal.com.br/verifica/",
         "editorias": ["desinformação"]},
        {"id": "fin", "nome": "Finanças anti-golpe", "tipo": "checagem", "homepage": "https://fin.globo.com/",
         "editorias": ["golpes financeiros", "fraudes"]},
        {"id": "forcado", "nome": "Explícito", "tipo": "checagem", "homepage": "https://x.org/",
         "editorias": ["checagem"], "agencia_checagem": False},
        {"id": "cand", "nome": "Candidato", "tipo": "checagem", "homepage": "https://cand.org/",
         "editorias": ["checagem"], "origem_catalogo": "descoberta-automatica"},
        {"id": "geral", "nome": "Jornal", "tipo": "geral", "homepage": "https://jornal.com/",
         "editorias": ["checagem"]},
    ])
    sites = [a["site"] for a in camada.agencias_checagem(cat)]
    assert sites == ["agencia.org", "portal.com.br/verifica", "agencia-nova.org"]
    assert camada.eh_secao_checagem("https://portal.com.br/verifica/x", cat) is True
    assert camada.eh_secao_checagem("https://portal.com.br/esportes/x", cat) is False
    assert camada.eh_secao_checagem("https://fin.globo.com/golpe-x", cat) is False


def test_agencias_do_catalogo_real_lupa_nova_sim_valorinveste_nao():
    E = camada.eh_secao_checagem
    assert E("https://www.agencialupa.org/verificacao/2026/09/x/") is True
    assert E("https://valorinveste.globo.com/mercados/noticia/2026/x.ghtml") is False
    assert E("https://www.canalsaude.fiocruz.br/canal/videoAberto/x") is False
    sites = [a["site"] for a in camada.agencias_checagem()]
    assert "agencialupa.org" in sites and not any("valorinveste" in x for x in sites)


def test_queries_nunca_passam_de_32_palavras():
    longa = " ".join(f"palavra{i}" for i in range(60))
    for est in ("simples", "avancada", "ambas"):
        for q in camada.construir_queries(longa, engine="google", estrategia=est):
            assert len(q["q"].split()) <= 32, (est, q["q"])
    for q in camada.construir_queries(longa, engine="google_news"):
        assert len(q["q"].split()) <= 32
    q, fora = camada.query_agencias(longa)
    assert len(q["q"].split()) <= 32 and "site:" in q["q"] and fora


def test_buscar_ex_motivo_por_chamada_e_cache(monkeypatch):
    import httpx as _hx

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"organic_results": [{"link": "https://x.com/a", "title": "T"}]}
    monkeypatch.setattr(_hx, "get", lambda *a, **k: R())
    c = camada.SerpAPIClient(api_key="k")
    q = {"q": "a", "engine": "google", "hl": "pt-br", "gl": "br", "num": 10}
    r1, r2 = c.buscar_ex(q), c.buscar_ex(q)
    assert (r1.motivo, r1.cache) == ("ok", False) and (r2.motivo, r2.cache) == ("ok", True)
    assert c.uso_hoje == 1
    assert camada.SerpAPIClient(api_key="").buscar_ex(q).motivo == "sem_chave"


def test_secao_checagem_dedicado_vs_geral():
    E = camada.eh_secao_checagem
    assert E("https://lupa.uol.com.br/jornalismo/2026/01/01/x") is True
    assert E("https://www.aosfatos.org/noticias/temer-lula-bets/") is True
    assert E("https://www.e-farsas.com/artigos/x") is True
    assert E("https://g1.globo.com/fato-ou-fake/noticia/x.ghtml") is True
    assert E("https://g1.globo.com/politica/noticia/x.ghtml") is False
    assert E("https://www.estadao.com.br/estadao-verifica/x/") is True
    assert E("https://www.estadao.com.br/brasil/x/") is False
    assert E("https://www.gov.br/saude/pt-br/assuntos/saude-com-ciencia/x") is True
    assert E("https://www.gov.br/economia/pt-br/x") is False
    assert E("https://exame.com/economia/x") is False
    assert E("") is False
    # Segmento exato: /radar/ vale, /radar-meteorologico não.
    assert E("https://www.aosfatos.org/radar/x") is True
    assert E("https://www.aosfatos.org/noticias/radar-meteorologico-x") is True  # host dedicado
    assert E("https://g1.globo.com/fato-ou-fake-x/y") is False


def test_extras_preservam_host_sem_corromper(monkeypatch):
    import factcheck_mvp.config as cfg
    monkeypatch.setattr(cfg, "SERP_SITES_EXTRAS", "webdobem.com, www.outro.org")
    hosts = [s["host"] for s in camada.sites_checagem()]
    assert "webdobem.com" in hosts  # lstrip("www.") corrompia p/ ebdobem.com
    assert "outro.org" in hosts


def test_extras_url_completa_vira_host(monkeypatch):
    import factcheck_mvp.config as cfg
    monkeypatch.setattr(cfg, "SERP_SITES_EXTRAS", "https://www.exemplo.com/caminho?x=1")
    hosts = [s["host"] for s in camada.sites_checagem()]
    assert "exemplo.com" in hosts


def test_construir_queries_agente_levanta():
    import pytest
    with pytest.raises(ValueError):
        camada.construir_queries("x", engine="google", estrategia="agente")


def test_vazio_nao_cola_no_cache(monkeypatch):
    import httpx as _hx
    calls = []

    class FakeResp:
        def __init__(self, p):
            self._p = p

        def raise_for_status(self):
            pass

        def json(self):
            return self._p

    cargas = [{}, {"organic_results": [{"link": "https://x.com/a", "title": "T"}]}]
    monkeypatch.setattr(_hx, "get",
                        lambda *a, **k: (calls.append(1), FakeResp(cargas[min(len(calls) - 1, 1)]))[1])
    c = camada.SerpAPIClient(api_key="k")
    q = {"q": "a", "engine": "google", "hl": "pt-br", "gl": "br", "num": 10}
    assert c.buscar(q) == {}
    assert c.buscar(q) is not None  # vazio não cacheou: rebateu a rede
    assert len(calls) == 2


def test_roteador_scholar_nao_cai_no_google():
    qs = camada.construir_queries("ibuprofeno dengue", engine="google_scholar")
    assert len(qs) == 1 and qs[0]["engine"] == "google_scholar"


def test_avancada_valida_e_limita_hosts_a_32_palavras():
    sites = [{"host": f"h{i}.exemplo.com"} for i in range(30)]
    sites.append({"host": "http://evil .com"})
    qs = camada.construir_queries_avancada("x", sites=sites)
    filtro = qs[1]["q"]
    assert filtro.count("site:") == 16  # 1 palavra + 16 sites + 15 OR = 32
    assert len(filtro.split()) == 32
    assert "evil" not in filtro


def test_scholar_ano_nao_resumo_inteiro():
    n = camada.normalizar_item({
        "title": "T", "link": "https://scielo.org/x",
        "publication_info": {"summary": "Autor - J Med, 2024"},
        "inline_links": {"cited_by": {"total": 7}},
    }, engine="google_scholar")
    assert n["data_publicacao"] == "2024"
    assert n["_scholar_pub"] == "Autor - J Med, 2024"
    assert n["_citacoes"] == 7


def test_fonte_displayed_sem_scheme():
    n = camada.normalizar_item({
        "link": "https://lupa.uol.com.br/x", "title": "T",
        "displayed_link": "https://lupa.uol.com.br › jornalismo",
    }, engine="google")
    assert n["fonte"]["nome"] == "lupa.uol.com.br"


def test_roteador_mantem_news_e_respeita_estrategia():
    news = camada.construir_queries("x", engine="google_news")
    assert all(q["engine"] == "google_news" for q in news)
    ambas = camada.construir_queries("x", engine="google", estrategia="ambas")
    simples = camada.construir_queries("x", engine="google", estrategia="simples")
    assert len(ambas) > len(simples) and all(q["engine"] == "google" for q in ambas)


def test_normaliza_organic_com_snippet_e_fonte_displayed():
    n = camada.normalizar_item({
        "link": "https://lupa.uol.com.br/jornalismo/2026/01/01/falso-x",
        "title": "É falso que X",
        "snippet": "Verificamos e é falso…",
        "displayed_link": "https://lupa.uol.com.br › jornalismo",
        "date": "há 2 dias",
        "position": 3,
    }, engine="google")
    assert n["url"].startswith("https://lupa.uol.com.br")
    assert n["_snippet"] == "Verificamos e é falso…"
    assert "lupa.uol.com.br" in n["fonte"]["nome"]
    assert n["corpo_texto"] is None and n["veredito"] is None
    assert n["_engine"] == "google" and n["_top_story"] is False


def test_normaliza_top_story_sem_snippet():
    n = camada.normalizar_item({
        "title": "T", "link": "https://x.com/a",
        "source": {"name": "G1"},
    }, engine="google", top_story=True)
    assert n["_top_story"] is True and n["_snippet"] is None
    assert n["fonte"]["nome"] == "G1"


def test_cliente_respeita_engine_por_query_e_result_key(monkeypatch):
    import httpx as _hx
    vistos = []

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    cargas = [{}, {"organic_results": [{"link": "https://x.com/a", "title": "T"}]}]

    def fake_get(url, params=None, timeout=None):
        vistos.append(dict(params or {}))
        return FakeResp(cargas[min(len(vistos) - 1, 1)])

    monkeypatch.setattr(_hx, "get", fake_get)
    c = camada.SerpAPIClient(api_key="k")  # default do env (google)
    out = c.buscar({"q": "a", "engine": "google_news", "hl": "pt-br", "gl": "br"})
    assert vistos[0]["engine"] == "google_news"  # override por query vence
    assert c.ultimo_motivo == "vazio"  # news_results ausente
    out2 = c.buscar({"q": "a", "engine": "google", "hl": "pt-br", "gl": "br", "num": 10})
    assert vistos[1]["engine"] == "google" and out2 is not None
    assert c.result_key({"engine": "google"}) == "organic_results"
    assert c.result_key({"engine": "google_news"}) == "news_results"
    assert c.ultimo_motivo == "ok"  # organic_results presente
