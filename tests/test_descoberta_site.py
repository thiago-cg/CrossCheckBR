"""Descoberta de catálogo: extração de schema + propostas (sem rede)."""
from factcheck_mvp import descoberta_site as dz

ARTIGO = """<html><head><title>Deputado propõe lei nova</title>
<meta property="og:site_name" content="Jornal Exemplo" />
</head><body><h1>Deputado propõe lei nova</h1>
<article><p>{}</p></article></body></html>""".format("fato relevante " * 60)

HOME = """<html><head><title>Jornal Exemplo</title>
<link rel="alternate" type="application/rss+xml" href="/feed.xml" />
</head><body><nav>
<a href="/politica/x">Política</a><a href="/economia/y">Economia</a>
<a href="/politica/z">Política 2</a><a href="/login">Login</a>
</nav></body></html>"""


def test_dominio_homepage():
    assert dz.dominio_de("https://WWW.Exemplo.com/brasil/x/") == "exemplo.com"
    assert dz.homepage_de("http://blog.exemplo.com/a/b?x=1") == "http://blog.exemplo.com/"
    assert dz.homepage_de("nota-url") == ""


def test_extrair_schema_basico():
    p = dz.extrair_schema("https://exemplo.com/politica/lei-nova/", ARTIGO, HOME,
                          corpo_lido=True)
    assert p["dominio"] == "exemplo.com"
    assert p["homepage"] == "https://exemplo.com/"
    assert p["nome"] == "Jornal Exemplo"
    assert p["tipo"] == "geral"
    assert p["rss"] == ["https://exemplo.com/feed.xml"]
    assert "/politica/" in p["article_url_patterns"]
    assert "politica" in p["editorias"] and "login" not in p["editorias"]
    assert "article" in p["css_selectors"]["corpo"]
    assert p["status"] == "pendente"
    assert p["exemplo_urls"] == ["https://exemplo.com/politica/lei-nova/"]
    assert "corpo_lido=True" in p["observacoes"]


def test_extrair_schema_tipo_checagem():
    p = dz.extrair_schema("https://checa-fatos.com/verifica/x/", "<title>T</title><h1>T</title>", "")
    assert p["tipo"] == "checagem"


def test_salvar_carregar_dedupe(tmp_path):
    arq = str(tmp_path / "props.json")
    assert dz.carregar_propostas(arq) == []
    p1 = {"dominio": "exemplo.com", "nome": "A", "coletado_em": "2026-01-01", "status": "pendente"}
    assert dz.salvar_proposta(p1, arq) is True
    p2 = {"dominio": "exemplo.com", "nome": "B", "coletado_em": "2026-01-02", "status": "pendente"}
    assert dz.salvar_proposta(p2, arq) is True
    props = dz.carregar_propostas(arq)
    assert len(props) == 1 and props[0]["nome"] == "B", "dedupe por domínio atualiza"
    assert dz.salvar_proposta({}, arq) is False
    assert dz.salvar_proposta({"nome": "sem dominio"}, arq) is False


def _catalogo_tmp(tmp_path, nome="catalogo.json"):
    import json
    arq = tmp_path / nome
    arq.write_text(json.dumps({"versao_schema": "1.0.0", "portais": [
        {"id": "g1", "nome": "G1", "tipo": "geral", "homepage": "https://g1.globo.com/"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return str(arq)


def _props_tmp(tmp_path, nome="props.json"):
    import json
    arq = tmp_path / nome
    arq.write_text(json.dumps({"versao_schema": "1.0.0", "propostas": [{
        "id": "jornal-exemplo", "nome": "Jornal Exemplo", "tipo": "geral",
        "homepage": "https://exemplo.com/", "dominio": "exemplo.com",
        "rss": [], "sitemap": None, "editorias": ["politica"],
        "article_url_patterns": ["/politica/"],
        "css_selectors": {"titulo": "h1", "corpo": "article"},
        "estrategia_recomendada": "x", "observacoes": "auto",
        "exemplo_urls": ["https://exemplo.com/politica/x/"],
        "responsavel": None, "subsecoes": [],
        "titulo_exemplo": "T", "status": "pendente", "coletado_em": "2026-01-01",
    }]}, ensure_ascii=False), encoding="utf-8")
    return str(arq)


def test_promover_ok_remove_da_fila(tmp_path):
    import json
    arq_props, arq_cat = _props_tmp(tmp_path), _catalogo_tmp(tmp_path)
    res = dz.promover_proposta("exemplo.com", caminho_propostas=arq_props,
                               caminho_catalogo=arq_cat)
    assert res["ok"] is True, res
    portal = res["portal"]
    assert portal["id"] == "jornal-exemplo" and portal["tipo"] == "geral"
    assert "dominio" not in portal and "status" not in portal  # metadados fora
    assert "promovido" in portal["observacoes"]
    dados = json.loads(open(arq_cat, encoding="utf-8").read())
    assert len(dados["portais"]) == 2
    assert dz.carregar_propostas(arq_props) == [], "promovida sai da fila"


def test_promover_erros(tmp_path):
    arq_props, arq_cat = _props_tmp(tmp_path), _catalogo_tmp(tmp_path)
    assert dz.promover_proposta("", caminho_propostas=arq_props,
                                caminho_catalogo=arq_cat)["ok"] is False
    assert dz.promover_proposta("inexistente.com", caminho_propostas=arq_props,
                                caminho_catalogo=arq_cat)["ok"] is False
    assert dz.promover_proposta("exemplo.com", caminho_propostas=arq_props,
                                caminho_catalogo=arq_cat, tipo="blog")["ok"] is False
    # duplicado: promove 1x, segunda falha
    assert dz.promover_proposta("exemplo.com", caminho_propostas=arq_props,
                                caminho_catalogo=arq_cat)["ok"] is True
    # recoloca proposta e tenta de novo -> domínio já no catálogo
    dz.salvar_proposta({"id": "outro-id", "nome": "X", "tipo": "geral",
                        "homepage": "https://exemplo.com/", "dominio": "exemplo.com"},
                       arq_props)
    res = dz.promover_proposta("exemplo.com", caminho_propostas=arq_props,
                               caminho_catalogo=arq_cat)
    assert res["ok"] is False and "já existe" in res["erro"]


def test_promover_tipo_explicito_e_jev_fallback(tmp_path, monkeypatch):
    arq_props, arq_cat = _props_tmp(tmp_path), _catalogo_tmp(tmp_path)
    res = dz.promover_proposta("exemplo.com", caminho_propostas=arq_props,
                               caminho_catalogo=arq_cat, tipo="checagem")
    assert res["ok"] is True and res["portal"]["tipo"] == "checagem"
    # Jev fora: degrada p/ tipo da proposta, sem quebrar
    monkeypatch.setattr(dz, "_classificar_tipo_jev", lambda *a: (_ for _ in ()).throw(RuntimeError("sem jev")))
    res2 = dz.promover_proposta("exemplo.com",
                                caminho_propostas=_props_tmp(tmp_path, "props2.json"),
                                caminho_catalogo=_catalogo_tmp(tmp_path, "cat2.json"),
                                usar_jev=True)
    assert res2["ok"] is True and res2["portal"]["tipo"] == "geral"


def _portal_novo():
    return {"id": "jornal-exemplo", "nome": "Jornal Exemplo", "tipo": "geral",
            "homepage": "https://exemplo.com/", "dominio": "exemplo.com",
            "rss": [], "observacoes": "auto"}


def test_inserir_no_catalogo_so_propoe(tmp_path):
    """Review A5: descoberta nunca grava no catalogo.json; vira proposta pendente."""
    import json
    arq_cat = _catalogo_tmp(tmp_path)
    arq_props = str(tmp_path / "props.json")
    antes = open(arq_cat, encoding="utf-8").read()
    res = dz.inserir_no_catalogo(_portal_novo(), arq_cat, caminho_propostas=arq_props)
    assert res["ok"] is False and res["proposta"] is True, res
    assert res["portal"]["status"] == "pendente"
    assert res["portal"]["origem_proposta"] == "descoberta-automatica"
    assert open(arq_cat, encoding="utf-8").read() == antes, "catalogo.json intocado"
    props = dz.carregar_propostas(arq_props)
    assert len(props) == 1 and props[0]["dominio"] == "exemplo.com"
    # repetir não duplica (dedupe por domínio)
    assert dz.inserir_no_catalogo(_portal_novo(), arq_cat, caminho_propostas=arq_props)["proposta"] is True
    assert len(dz.carregar_propostas(arq_props)) == 1
    # domínio já catalogado: nem propõe
    g1 = {"id": "g1-bis", "nome": "G1", "homepage": "https://g1.globo.com/"}
    res2 = dz.inserir_no_catalogo(g1, arq_cat, caminho_propostas=arq_props)
    assert res2["ok"] is False and res2["proposta"] is False and "já existe" in res2["erro"]
    # proposta enfileirada é promovível pela curadoria (único caminho p/ o catálogo)
    res3 = dz.promover_proposta("exemplo.com", caminho_propostas=arq_props, caminho_catalogo=arq_cat)
    assert res3["ok"] is True
    portal = res3["portal"]
    assert "origem_proposta" not in portal and "proposto_em" not in portal
    dados = json.loads(open(arq_cat, encoding="utf-8").read())
    assert any(p["id"] == "jornal-exemplo" for p in dados["portais"])


def test_inserir_no_catalogo_default_usa_fila_global(tmp_path, monkeypatch):
    """Sem caminhos: grava em DEFAULT_PROPOSTAS (a cópia isolada do eval/CLI troca esse global)."""
    arq_props = tmp_path / "fila.json"
    monkeypatch.setattr(dz, "DEFAULT_PROPOSTAS", arq_props)
    from factcheck_mvp import catalogo as cat
    monkeypatch.setattr(cat, "DEFAULT_CATALOGO", tmp_path / "nao_existe.json")
    res = dz.inserir_no_catalogo(_portal_novo())
    assert res["proposta"] is True and arq_props.exists()


def _html_artigo_longo():
    corpo = "".join(f"<p>Parágrafo {i} da reportagem com texto corrido suficiente para contar como matéria real.</p>"
                    for i in range(15))
    return f"<html><head><title>Matéria</title></head><body><article><h1>Matéria</h1>{corpo}</article></body></html>"


def test_descobrir_usa_cascata_e_teto_5mb(monkeypatch):
    import asyncio
    from factcheck_mvp import replay
    vistos = {}

    class R:
        def __init__(self, html):
            self.status_code, self.headers, self.content = 200, {}, html.encode()
            self.truncado = False

        def raise_for_status(self):
            pass

    async def falso_get(url, max_bytes=None, headers=None, timeout=10.0, **kw):
        vistos.setdefault(url, (max_bytes, headers))
        return R(_html_artigo_longo() if "/politica/" in url else "<html></html>")

    monkeypatch.setattr(replay, "ahttp_get", falso_get)
    monkeypatch.setattr(dz, "_url_segura", lambda u: True)
    monkeypatch.setattr(dz, "_classificar_tipo_jev", lambda *a: None)
    monkeypatch.delenv("DISCOVERY_MAX_BYTES", raising=False)
    url = "https://exemplo.com/politica/lei-nova/"
    out = asyncio.run(dz.descobrir([url]))
    c = out[url]["corpo"]
    assert c is not None and c.corpo_lido and c.metodo in ("trafilatura", "regex")
    assert len(c.texto_completo) >= 500 and c.trecho_corpo
    mb, hdr = vistos[url]
    assert mb == 5_000_000
    assert "Mozilla" in hdr["User-Agent"]


def test_catalogo_adicionar_roteia_na_hora():
    from factcheck_mvp.catalogo import Catalogo
    cat = Catalogo([{"id": "g1", "nome": "G1", "tipo": "geral",
                     "homepage": "https://g1.globo.com/"}])
    assert cat.por_dominio("https://exemplo.com/politica/x/") is None
    assert cat.adicionar({"id": "jornal-exemplo", "nome": "Jornal Exemplo",
                          "tipo": "geral", "homepage": "https://exemplo.com/"}) is True
    achado = cat.por_dominio("https://exemplo.com/politica/x/")
    assert achado and achado["id"] == "jornal-exemplo"
    assert cat.adicionar({"id": "jornal-exemplo", "nome": "X",
                          "homepage": "https://outro.com/"}) is False  # id dup
    assert cat.adicionar({"id": "y", "nome": "Y",
                          "homepage": "https://exemplo.com/"}) is False  # domínio dup
