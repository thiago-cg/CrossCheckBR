"""Replay: cassetes record/replay, chave sem segredos, orçamento SerpAPI, pipeline instrumentado."""
import asyncio
import json

import curl_cffi.requests as _curl
import pytest

from factcheck_mvp import replay, telemetria as tel


@pytest.fixture
def amb(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRIA", "1")
    monkeypatch.setenv("TELEMETRIA_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CASSETES_DIR", str(tmp_path / "cass"))
    monkeypatch.setenv("SERPAPI_USO_ARQ", str(tmp_path / "uso.json"))
    monkeypatch.delenv("IO_MODO", raising=False)
    replay.definir_modo(None)
    replay.definir_teto_serpapi(None)
    yield tmp_path
    replay.definir_modo(None)
    replay.definir_teto_serpapi(None)


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status
        self.content = json.dumps(payload).encode()
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._p


def _eventos(tmp, rid, tipo):
    linhas = (tmp / "runs" / rid / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(x)["dados"] for x in linhas if json.loads(x)["tipo"] == tipo]


def test_modo_padrao_e_override(amb, monkeypatch):
    assert replay.modo_atual() == "live"
    monkeypatch.setenv("IO_MODO", "record")
    assert replay.modo_atual() == "record"
    with replay.modo("replay"):
        assert replay.modo_atual() == "replay"
    replay.definir_modo("live")
    assert replay.modo_atual() == "live"
    with pytest.raises(ValueError):
        replay.definir_modo("gravar")


def test_chave_ignora_segredos_e_ordem_da_query():
    a = replay.chave("GET", "https://serpapi.com/search.json?q=x&api_key=AAA&engine=google")
    b = replay.chave("GET", "https://serpapi.com/search.json?engine=google&q=x&api_key=BBB")
    c = replay.chave("GET", "https://serpapi.com/search.json?engine=google&q=y&api_key=AAA")
    assert a == b and a != c
    assert replay.chave("POST", "http://h/v1/chat", {"m": 1}) != replay.chave("POST", "http://h/v1/chat", {"m": 2})


def test_record_grava_e_replay_reproduz_sem_rede(amb, monkeypatch):
    chamadas = []
    monkeypatch.setattr(_curl, "get", lambda url, **k: (chamadas.append(k), FakeResp({"ok": 1}))[1])
    with replay.modo("record"):
        r = replay.http_get("http://exemplo.test/api", params={"q": "a", "api_key": "SEGREDO"}, timeout=5)
    assert r.json() == {"ok": 1} and r.cache == "live" and chamadas[0]["params"]["q"] == "a"
    arqs = list((amb / "cass").glob("*.json"))
    assert len(arqs) == 1 and "SEGREDO" not in arqs[0].read_text(encoding="utf-8")
    # replay: rede proibida, mesma resposta
    monkeypatch.setattr(_curl, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rede!")))
    with replay.modo("replay"):
        r2 = replay.http_get("http://exemplo.test/api", params={"api_key": "OUTRA", "q": "a"})
        assert r2.json() == {"ok": 1} and r2.cache == "hit"
        # record reusa cassete existente (não chama de novo)
    with replay.modo("record"):
        assert replay.http_get("http://exemplo.test/api", params={"q": "a"}).cache == "hit"


def test_replay_miss_e_erro_de_rede_tratavel(amb):
    rid = tel.iniciar_run({})
    with replay.modo("replay"):
        with pytest.raises(replay.HTTPError) as ei:  # chamadores já capturam HTTPError/Exception
            replay.http_post("http://exemplo.test/v1/chat/completions", json={"model": "m"})
    tel.finalizar_run(None)
    assert isinstance(ei.value, replay.ReplayMiss)
    http = _eventos(amb, rid, "http")
    assert http[0]["cache"] == "miss" and http[0]["metodo"] == "POST"


def test_raise_for_status_e_headers_case_insensitive():
    r = replay.Resposta(404, {"Location": "https://x/y"}, b"nao", "https://x/?api_key=S")
    assert r.headers.get("location") == "https://x/y" and "LOCATION" in r.headers
    with pytest.raises(replay.HTTPError) as ei:
        r.raise_for_status()
    assert "api_key=REDACTED" in str(ei.value) and "404" in str(ei.value)


def test_llm_post_emite_evento_llm(amb, monkeypatch):
    payload = {"model": "m1", "messages": [{"role": "user", "content": "Divida o texto"}], "temperature": 0}
    resp = {"choices": [{"message": {"content": "Café cura câncer"}}]}
    monkeypatch.setattr(_curl, "post", lambda url, **k: FakeResp(resp))
    rid = tel.iniciar_run({})
    r = replay.llm_post("http://127.0.0.1:8888/v1/chat/completions", payload, motor="llm-local",
                        finalidade="afirmacoes", headers={"Authorization": "Bearer sk-or-v1-segredosegredo123"})
    tel.finalizar_run(None)
    assert r.json() == resp
    ev = _eventos(amb, rid, "llm")[0]
    assert ev["motor"] == "llm-local" and ev["modelo"] == "m1" and ev["finalidade"] == "afirmacoes"
    assert ev["saida"] == "Café cura câncer" and ev["cache"] == "live" and ev["prompt_sha"]
    assert "segredosegredo" not in (amb / "runs" / rid / "trace.jsonl").read_text(encoding="utf-8")


def test_finalidade_inferida_do_chamador(amb, monkeypatch):
    monkeypatch.setattr(_curl, "post", lambda url, **k: FakeResp({"choices": [{"message": {"content": "x"}}]}))
    rid = tel.iniciar_run({})

    def termometro():  # simula juiz_llm.termometro chamando via llm_openrouter
        return replay.llm_post("http://h/v1/chat/completions", {"model": "m", "messages": []})
    termometro()
    with tel.finalidade("juiz"):
        replay.llm_post("http://h/v1/chat/completions", {"model": "m", "messages": [1]})
    tel.finalizar_run(None)
    fins = [e["finalidade"] for e in _eventos(amb, rid, "llm")]
    assert fins[0].endswith("termometro") and fins[1] == "juiz"


def _cliente_mock(monkeypatch, handler):
    """Fake AsyncSession do curl_cffi: handler(url) -> (status, headers, body)."""
    from contextlib import asynccontextmanager

    class _StreamResp:
        def __init__(self, status, headers, url, body):
            self.status_code = status
            self.headers = headers
            self.url = url
            self._body = body

        async def aiter_content(self):
            yield self._body

        async def aclose(self):
            pass

    class _Sess:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        @asynccontextmanager
        async def stream(self, method, url, **kw):
            status, headers, body = handler(url)
            yield _StreamResp(status, headers, url, body)

    monkeypatch.setattr(_curl, "AsyncSession", _Sess)


def test_ahttp_get_nao_segue_redirect_e_respeita_teto(amb, monkeypatch):
    def handler(url):
        if url.endswith("/antigo"):
            return 302, {"location": "/novo"}, b""
        return 200, {"content-type": "text/html"}, b"a" * 1000
    _cliente_mock(monkeypatch, handler)

    async def _go():
        with replay.modo("record"):
            r1 = await replay.ahttp_get("https://site.test/antigo", max_bytes=100)
            r2 = await replay.ahttp_get("https://site.test/novo", max_bytes=100)
        return r1, r2
    r1, r2 = asyncio.run(_go())
    assert r1.status_code == 302 and r1.headers.get("location") == "/novo"
    assert len(r2.content) == 100 and r2.truncado

    async def _rep(mb):
        with replay.modo("replay"):
            return await replay.ahttp_get("https://site.test/novo", max_bytes=mb)
    monkeypatch.setattr(_curl, "AsyncSession",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("rede!")))
    assert asyncio.run(_rep(50)).content == b"a" * 50
    with pytest.raises(replay.ReplayMiss):  # cassete truncado em 100 não serve p/ teto maior
        asyncio.run(_rep(500))


def test_orcamento_serpapi_bloqueia_live_e_cassete_nao_conta(amb, monkeypatch):
    (amb / "uso.json").write_text(json.dumps({"total_live": 2}))
    monkeypatch.setenv("SERPAPI_ORCAMENTO_RESTANTE", "3")
    monkeypatch.setattr(_curl, "get", lambda url, **k: FakeResp({"organic_results": [1]}))
    url = "https://serpapi.com/search.json"
    rid = tel.iniciar_run({})
    with replay.modo("record"):
        replay.http_get(url, params={"q": "a", "api_key": "k"})  # 3/3
        assert replay.http_get(url, params={"q": "a", "api_key": "k"}).cache == "hit"  # não conta
        with pytest.raises(replay.OrcamentoSerpAPIEsgotado):
            replay.http_get(url, params={"q": "b", "api_key": "k"})
    tel.finalizar_run(None)
    uso = json.loads((amb / "uso.json").read_text(encoding="utf-8"))
    assert uso["total_live"] == 3 and uso["chamadas"][-1]["q"] == "a"
    assert replay.uso_serpapi()["restantes"] == 0
    fb = _eventos(amb, rid, "fallback")
    assert fb and fb[0]["onde"] == "serpapi" and fb[0]["motivo"] == "orcamento"


def test_sem_env_nao_ha_teto_mas_conta(amb, monkeypatch):
    monkeypatch.delenv("SERPAPI_ORCAMENTO_RESTANTE", raising=False)
    (amb / "uso.json").write_text(json.dumps({"total_live": 500}))
    monkeypatch.setattr(_curl, "get", lambda url, **k: FakeResp({"organic_results": [1]}))
    replay.http_get("https://serpapi.com/search.json", params={"q": "z", "api_key": "k"})
    u = replay.uso_serpapi()
    assert u["usadas"] == 501 and u["orcamento"] is None and u["restantes"] is None


def test_cota_da_conta_esgotada_vira_erro_explicito(amb, monkeypatch):
    from factcheck_mvp.serpapi_layer import SerpAPIClient
    monkeypatch.setattr(_curl, "get", lambda url, **k: FakeResp(
        {"error": "Your account has run out of searches."}, status=429))
    c = SerpAPIClient(api_key="k")
    rid = tel.iniciar_run({})
    with replay.modo("record"):
        assert c.buscar({"q": "a", "engine": "google"}) is None
    tel.finalizar_run(None)
    fb = _eventos(amb, rid, "fallback")
    assert len(fb) == 1 and "nova SERPAPI_KEY" in fb[0]["motivo"] and "run out" in fb[0]["motivo"]
    assert not list((amb / "cass").glob("*.json"))  # erro de cota não vira cassete


def test_teto_do_processo_e_serpapi_client(amb, monkeypatch):
    from factcheck_mvp.serpapi_layer import SerpAPIClient
    monkeypatch.setattr(_curl, "get", lambda url, **k: FakeResp({"organic_results": [{"link": "u"}]}))
    replay.definir_teto_serpapi(1)
    c = SerpAPIClient(api_key="k")
    rid = tel.iniciar_run({})
    assert c.buscar({"q": "a", "engine": "google"}) is not None
    assert c.buscar({"q": "b", "engine": "google"}) is None  # recusado pelo teto
    tel.finalizar_run(None)
    assert c.ultimo_motivo == "erro"
    fb = _eventos(amb, rid, "fallback")
    assert [f["motivo"] for f in fb] == ["orcamento"]  # sem fallback duplicado
    assert replay.uso_serpapi()["usadas_processo"] == 1


def test_pipeline_instrumentado_gera_trace_completo(amb):
    from factcheck_mvp import serpapi_layer as camada
    from factcheck_mvp.catalogo import Catalogo
    from factcheck_mvp.indice import Indice
    from factcheck_mvp.pipeline import Pipeline
    from factcheck_mvp.schemas import EntradaConsulta

    pipe = Pipeline(Catalogo.carregar(), Indice(), Indice(), serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer"),
                                    usar_llm=False))
    rid = tel.ultimo_run_id()
    ev = tel.ler_run(rid)
    tipos = [e["tipo"] for e in ev["eventos"]]
    assert tipos[0] == "run_inicio" and tipos[-1] == "run_fim"
    assert "decisao" in tipos and "sinal" in tipos
    etapas = [e["dados"]["nome"] for e in ev["eventos"] if e["tipo"] == "etapa"]
    assert etapas == [x.nome for x in rel.etapas]
    assert ev["resultado"]["relatorio"]["propensao"] == rel.propensao
    assert ev["metricas"]["nivel"] == rel.propensao
    ini = ev["eventos"][0]["dados"]
    assert ini["config"]["IO_MODO"] == "live" and ini["tipo"] == "titulo"


def test_limitador_openrouter_espaca_chamadas_vivas(monkeypatch):
    """3 rpm => >= 20 s entre chamadas vivas ao OpenRouter; nada dorme de verdade."""
    from factcheck_mvp import replay
    dormiu = []
    relogio = {"t": 1000.0}
    monkeypatch.setenv("OPENROUTER_RPM", "3")
    monkeypatch.setattr(replay, "_ultima_chamada_or", 0.0)
    monkeypatch.setattr(replay.time, "monotonic", lambda: relogio["t"])
    monkeypatch.setattr(replay.time, "sleep", lambda s: (dormiu.append(s), relogio.__setitem__("t", relogio["t"] + s)))
    replay._esperar_limite_openrouter()      # 1ª: sem espera
    replay._esperar_limite_openrouter()      # 2ª: espera o intervalo inteiro
    replay._esperar_limite_openrouter()
    assert len(dormiu) == 2 and all(20.0 <= s <= 22.0 for s in dormiu), dormiu


def test_limitador_desligado_com_rpm_zero(monkeypatch):
    from factcheck_mvp import replay
    monkeypatch.setenv("OPENROUTER_RPM", "0")
    monkeypatch.setattr(replay.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("dormiu")))
    replay._esperar_limite_openrouter()
    replay._esperar_limite_openrouter()


def test_relogio_replay_grava_e_reproduz(tmp_path, monkeypatch):
    from factcheck_mvp import replay
    monkeypatch.setenv("CASSETES_DIR", str(tmp_path))   # replay.dir_cassetes(), replay.py:126-128
    with replay.modo("record"):
        d1 = replay.hoje("Bolsonaro recebeu alta do hospital hoje")
    with replay.modo("replay"):
        assert replay.hoje("Bolsonaro recebeu alta do hospital hoje") == d1
        assert replay.hoje("texto nunca gravado") is None   # + fallback onde=relogio


# --- E4 revisão (item 5): relógio valida a data gravada; cassete sem data = None + fallback ---
def _capturar_fallbacks(monkeypatch):
    capturados = []
    monkeypatch.setattr(replay.telemetria, "fallback",
                        lambda onde, motivo="", /, **extra: capturados.append((onde, motivo)))
    return capturados


def _cassete_relogio(contexto, corpo):
    """Grava no cassete do relógio um corpo arbitrário (cassete bom ou ruim)."""
    k = replay.chave("GET", replay.RELOGIO_URL, contexto)
    resp = replay.Resposta(200, {"content-type": "application/json"},
                           json.dumps(corpo).encode("utf-8"), replay.RELOGIO_URL, cache="live")
    replay._gravar(k, "GET", replay.RELOGIO_URL, contexto, resp, 0.0)


@pytest.mark.parametrize("corpo", [{}, {"data": None}, {"data": "2026-1-9"}, {"data": "amanhã"},
                                   {"data": "2026-10-09T10:00"}, {"data": "2026-02-30"}])
def test_relogio_cassete_sem_data_valida_vira_none_com_fallback(tmp_path, monkeypatch, corpo):
    monkeypatch.setenv("CASSETES_DIR", str(tmp_path))
    fb = _capturar_fallbacks(monkeypatch)
    _cassete_relogio("texto X", corpo)
    with replay.modo("replay"):
        assert replay.hoje("texto X") is None  # nunca a string "None" nem data inventada
    assert fb == [("relogio", "cassete sem data")]


def test_relogio_replay_sem_cassete_registra_nao_gravada(tmp_path, monkeypatch):
    monkeypatch.setenv("CASSETES_DIR", str(tmp_path))
    fb = _capturar_fallbacks(monkeypatch)
    with replay.modo("replay"):
        assert replay.hoje("nunca gravado") is None
    assert fb == [("relogio", "data de referência não gravada")]


def test_relogio_record_refaz_cassete_sem_data_e_reproduz(tmp_path, monkeypatch):
    monkeypatch.setenv("CASSETES_DIR", str(tmp_path))
    monkeypatch.setattr(replay, "_hoje_brt_iso", lambda: "2026-10-09")
    _cassete_relogio("texto Y", {"outra": 1})
    with replay.modo("record"):
        assert replay.hoje("texto Y") == "2026-10-09"  # grava o que falta (cassete ruim é refeito)
    with replay.modo("replay"):
        assert replay.hoje("texto Y") == "2026-10-09"


def test_relogio_live_devolve_a_data_de_hoje_brt(monkeypatch):
    monkeypatch.setattr(replay, "_hoje_brt_iso", lambda: "2026-10-09")
    with replay.modo("live"):
        assert replay.hoje("qualquer texto") == "2026-10-09"
