"""Agente de descoberta: roteador, ondas, crítico e teto (sem rede)."""
from factcheck_mvp import agente
from factcheck_mvp.serpapi_layer import SerpAPIClient


def _org(url, titulo, snippet="snip"):
    return {"link": url, "title": titulo, "snippet": snippet,
            "displayed_link": url, "position": 1}


class FakeClient(SerpAPIClient):
    def __init__(self, roteiro):
        super().__init__(api_key="k")
        self._roteiro = roteiro
        self.chamadas = []

    def buscar(self, params):
        self.chamadas.append(dict(params))
        self._uso_n += 1  # double honesto: cada buscar gasta de verdade
        r = self._roteiro
        if callable(r):
            return r(params, len(self.chamadas))
        return r[min(len(self.chamadas) - 1, len(r) - 1)]


def test_classificar_saude_e_quente():
    assert agente.classificar("ibuprofeno cura dengue") == {"saude": True, "quente": False}
    assert agente.classificar("bets proibidas aprovadas hoje")["quente"] is True
    assert agente.classificar("cachorro late alto") == {"saude": False, "quente": False}


def test_planejar_condicionais():
    p, _ = agente.planejar("bets são proibidas")
    assert [b.engine for b in p] == ["google", "google"]
    p, _ = agente.planejar("ibuprofeno cura dengue")
    assert [b.engine for b in p] == ["google", "google", "google_scholar"]
    p, _ = agente.planejar("resultado do jogo hoje")
    assert [b.engine for b in p] == ["google", "google", "google_news"]


def test_descobrironda1_sem_wave2_quando_ha_checagem():
    cli = FakeClient([
        {"organic_results": [_org("https://lupa.uol.com.br/x", "É falso que bets acabaram"),
                             _org("https://exame.com/y", "Bets seguem legais")],
         "related_searches": [{"query": "fim das bets é verdade"}]},
        {"organic_results": [_org("https://www.aosfatos.org/z", "Bets: o que muda")]},
    ])
    unicas, traco = agente.descobrir(["bets são proibidas"], cli, teto=8)
    assert len(cli.chamadas) == 2  # onda 2 dispensada
    assert traco["buscas_usadas"] == 2 and traco["ondas"] == 1
    assert traco["n_secao_checagem"] == 2  # lupa + aosfatos
    assert len(unicas) == 3
    assert all(d["_wave"] == 1 for d in unicas)
    assert any("onda 2 dispensada" in d for d in traco["decisoes"])


def test_critico_reformula_quando_nada_presta():
    def roteiro(params, n):
        if n == 1:
            return {"organic_results": [_org("https://exame.com/y", "Receita de bolo de cenoura")],
                    "related_searches": [{"query": "proibição das bets é verdadeira mesmo"}]}
        if n == 2:
            return {"organic_results": [_org("https://www.estadao.com.br/brasil/w",
                                             "Senado discute apostas")]}
        return {"organic_results": [_org("https://lupa.uol.com.br/w", "Bets proibidas: é falso")]}
    cli = FakeClient(roteiro)
    unicas, traco = agente.descobrir(["bets são proibidas"], cli, teto=8)
    assert len(cli.chamadas) == 3  # 2 onda 1 + 1 reformulação
    assert traco["ondas"] == 2
    assert any(d["_wave"] == 2 for d in unicas)
    assert any("reformulou" in d for d in traco["decisoes"])


def test_teto_global_corta_e_registra():
    cli = FakeClient([{"organic_results": [_org("https://x.com/a", "T")]
                       } for _ in range(10)])
    unicas, traco = agente.descobrir(["afirmacao um", "afirmacao dois"], cli, teto=1)
    assert len(cli.chamadas) == 1
    assert any("teto 1" in d for d in traco["decisoes"])


def test_sem_cliente_degrada():
    unicas, traco = agente.descobrir(["x"], None)
    assert unicas == [] and any("sem SerpAPI" in d for d in traco["decisoes"])
    cli = FakeClient([])
    cli.api_key = ""
    unicas, traco = agente.descobrir(["x"], cli)
    assert unicas == []


def test_scholar_vira_evidencia_com_citacoes():
    item = {"title": "Ibuprofen and dengue", "link": "https://scielo.org/x",
            "snippet": "estudo…",
            "publication_info": {"summary": "Autor - J Med, 2024"},
            "inline_links": {"cited_by": {"total": 42}}}
    cli = FakeClient([{"organic_results": []},
                      {"organic_results": []},
                      {"organic_results": [item]}])
    unicas, traco = agente.descobrir(["ibuprofeno cura dengue"], cli, teto=8)
    engines = [c.get("engine") for c in cli.chamadas]
    assert "google_scholar" in engines
    sch = [d for d in unicas if d.get("_scholar")]
    assert len(sch) == 1 and sch[0]["_citacoes"] == 42
    assert sch[0]["_secao_checagem"] is False  # periódico ≠ checagem


def test_dedupe_entre_ondas():
    item = _org("https://lupa.uol.com.br/x", "É falso que bets acabaram")
    cli = FakeClient([{"organic_results": [item], "related_searches": []},
                      {"organic_results": [item]}])
    unicas, traco = agente.descobrir(["bets acabaram"], cli, teto=8)
    assert [d["url"] for d in unicas].count("https://lupa.uol.com.br/x") == 1


def test_round_robin_ninguem_esfomeado():
    vistos = []

    def roteiro(params, n):
        vistos.append(params.get("q", ""))
        return {"organic_results": []}
    cli = FakeClient(roteiro)
    # af1 saúde (plano de 3) + af2 simples (plano de 2), teto 2:
    # intercalado => cada uma recebe a busca 1.
    unicas, traco = agente.descobrir(["ibuprofeno dengue", "bets lei"], cli, teto=2)
    assert len(cli.chamadas) == 2
    assert any("ibuprofeno dengue" in q for q in vistos)
    assert any("bets lei" in q and "site:" not in q for q in vistos)
    assert any("teto 2" in d for d in traco["decisoes"])


def test_cancel_para_entre_buscas_sem_gastar_cota():
    import threading
    cli = FakeClient([{"organic_results": [_org("https://x.com/a", "T")]}])
    ev = threading.Event()
    ev.set()  # cancelado antes de começar
    unicas, traco = agente.descobrir(["bets lei", "outra afirmacao"], cli,
                                     teto=8, cancel=ev)
    assert unicas == [] and len(cli.chamadas) == 0
    assert any("cancelado" in d for d in traco["decisoes"])


def test_cap_diario_cancela_ondas_restantes():
    class FakeCap(SerpAPIClient):
        def __init__(self):
            super().__init__(api_key="k")
            self.chamadas = 0

        def buscar(self, params):
            self.chamadas += 1
            self._uso_n += 1
            self.ultimo_motivo = "cap"  # como se SERPAPI_DAILY_CAP tivesse estourado
            return None

    cli = FakeCap()
    unicas, traco = agente.descobrir(["afirmacao um", "afirmacao dois"], cli, teto=8)
    assert cli.chamadas == 1  # parou na primeira, sem log-spam nas demais
    assert any("teto diário" in d for d in traco["decisoes"])
