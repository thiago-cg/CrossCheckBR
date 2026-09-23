"""SerpAPI: queries, normalização e roteamento (sem rede, sem denylist)."""
import json
from pathlib import Path

from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.catalogo import Catalogo

FIX = Path(__file__).parent / "fixtures" / "serpapi_bets.json"


def test_queries_pt_br():
    qs = camada.construir_queries("bets são proibidas no brasil")
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
