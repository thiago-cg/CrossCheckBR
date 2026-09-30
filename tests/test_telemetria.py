"""Telemetria: trace JSONL por run, redação de segredos, spans, contexto async."""
import asyncio
import json

import pytest

from factcheck_mvp import telemetria as tel


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRIA", "1")
    monkeypatch.setenv("TELEMETRIA_DIR", str(tmp_path / "runs"))
    return tmp_path / "runs"


def _eventos(tdir, rid):
    return [json.loads(x) for x in (tdir / rid / "trace.jsonl").read_text().splitlines()]


def test_evento_sem_run_e_noop(tdir):
    tel.evento("etapa", nome="x")  # não levanta, não cria nada
    assert not tdir.exists()


def test_desligada_por_env(tdir, monkeypatch):
    monkeypatch.setenv("TELEMETRIA", "0")
    assert tel.iniciar_run({"entrada": "x"}) is None
    tel.evento("etapa", nome="x")
    assert tel.finalizar_run({}) is None


def test_run_grava_trace_e_resultado(tdir):
    rid = tel.iniciar_run({"entrada": "Café cura câncer", "tipo": "titulo"})  # 'tipo' não colide
    assert rid and tel.run_atual() == rid
    tel.evento("etapa", nome="afirmacoes", status="ok", detalhe="1 afirmação")
    tel.fallback("juiz", "sem chave")
    tel.evento("fonte", url="https://x", estagio="deep-crawl", decisao="descartada", motivo="corpo curto")
    tel.evento("llm", motor="llm-local", finalidade="afirmacoes", latencia_ms=12.0, saida="ok")
    tel.evento("decisao", nivel="baixa", score=0.1)
    tel.finalizar_run({"propensao": "baixa"})
    assert tel.run_atual() is None and tel.ultimo_run_id() == rid
    ev = _eventos(tdir, rid)
    assert ev[0]["tipo"] == "run_inicio" and ev[0]["dados"]["tipo"] == "titulo"
    assert ev[-1]["tipo"] == "run_fim" and ev[-1]["dados"]["nivel"] == "baixa"
    assert all({"ts", "t_rel_ms", "tipo", "dados"} <= set(e) for e in ev)
    res = json.loads((tdir / rid / "resultado.json").read_text())
    m = res["metricas"]
    assert res["relatorio"]["propensao"] == "baixa"
    assert m["n_llm"] == 1 and m["fallbacks_por_onde"] == {"juiz": 1}
    assert m["descartes_por_motivo"] == {"corpo curto": 1} and m["nivel"] == "baixa"


def test_redacao_de_segredos_e_truncamento(tdir, monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "segredoSuperLongo123")
    rid = tel.iniciar_run({})
    tel.evento("http", url="https://serpapi.com/search.json?q=a&api_key=abc123&key=zzz",
               h="Authorization: Bearer sk-or-v1-abcdefghijklmnopqrstuv",
               t="https://api.telegram.org/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getMe",
               bruto="valor segredoSuperLongo123 no meio", grande="x" * 5000)
    tel.finalizar_run(None)
    txt = (tdir / rid / "trace.jsonl").read_text()
    for s in ("abc123", "zzz", "abcdefghijklmnop", "AAHdqTcv", "segredoSuperLongo123"):
        assert s not in txt
    assert "REDACTED" in txt
    d = [e for e in _eventos(tdir, rid) if e["tipo"] == "http"][0]["dados"]
    assert len(d["grande"]) < 2100 and "chars]" in d["grande"]


def test_span_sync_e_async(tdir):
    rid = tel.iniciar_run({})
    with tel.span("bloco", n=1) as sp:
        sp.dados["achados"] = 3
    with pytest.raises(ValueError):
        with tel.span("quebra"):
            raise ValueError("boom")

    async def _a():
        async with tel.span("assincrono"):
            await asyncio.sleep(0)
    asyncio.run(_a())
    tel.finalizar_run(None)
    fins = {e["dados"]["nome"]: e["dados"] for e in _eventos(tdir, rid) if e["tipo"] == "span_fim"}
    assert fins["bloco"]["ok"] and fins["bloco"]["achados"] == 3 and "dur_ms" in fins["bloco"]
    assert not fins["quebra"]["ok"] and "boom" in fins["quebra"]["erro"]
    assert fins["assincrono"]["ok"]


def test_run_aninhado_reusa_id(tdir):
    externo = tel.iniciar_run({"caso_id": "c1"})
    interno = tel.iniciar_run({"entrada": "x"})
    assert interno == externo
    tel.finalizar_run({"propensao": "alta"})  # fecha só o aninhado
    assert tel.run_atual() == externo
    tel.finalizar_run(None)  # fecha de verdade, com o resultado guardado
    res = json.loads((tdir / externo / "resultado.json").read_text())
    assert res["relatorio"]["propensao"] == "alta"
    assert [e["tipo"] for e in _eventos(tdir, externo)].count("run_fim") == 1


def test_contexto_isolado_entre_tasks_e_herdado_por_thread(tdir):
    async def um(nome):
        rid = tel.iniciar_run({"entrada": nome})
        await asyncio.sleep(0.01)
        await asyncio.to_thread(tel.evento, "etapa", nome=nome)  # thread herda o run
        tel.finalizar_run(None)
        return rid

    async def dois():
        return await asyncio.gather(um("a"), um("b"))

    ra, rb = asyncio.run(dois())
    assert ra != rb
    ea = [e["dados"]["nome"] for e in _eventos(tdir, ra) if e["tipo"] == "etapa"]
    eb = [e["dados"]["nome"] for e in _eventos(tdir, rb) if e["tipo"] == "etapa"]
    assert ea == ["a"] and eb == ["b"]


def test_ler_resumir_e_listar(tdir):
    with tel.run({"entrada": "Vacina altera DNA", "tipo": "titulo"}) as rid:
        tel.evento("etapa", nome="afirmacoes", status="ok", detalhe="1 afirmação via llm-local")
        tel.evento("etapa", nome="descoberta", status="pulada", detalhe="sem chave")
        tel.evento("llm", motor="llm-local", modelo="m", finalidade="padroes", latencia_ms=900, erro="HTTP 500")
        tel.fallback("padroes", "HTTP 500")
        tel.evento("fonte", url="https://a", estagio="relatorio", decisao="descartada", motivo="juiz: irrelevante")
        tel.evento("sinal", motor="modelo-fake", rotulo="modelo", valor="0.9", direcao=1.0, peso=0.2)
        tel.evento("decisao", nivel="alta", nivel_agregador="alta", score=0.7, why="porque")
    r = tel.ler_run("last")
    assert r["run_id"] == rid and r["resultado"] is not None
    txt = tel.resumir_run(rid)
    for trecho in ("ETAPAS", "descoberta", "LLM (1", "FALLBACKS", "padroes x1", "juiz: irrelevante",
                   "SINAIS", "DECISAO nivel=alta", "Vacina altera DNA"):
        assert trecho in txt, trecho
    runs = tel.listar_runs(5)
    assert runs[0]["run_id"] == rid and runs[0]["nivel"] == "alta" and runs[0]["n_fallbacks"] == 1
    assert [e["tipo"] for e in tel.eventos_de(rid, ["fallback"])] == ["fallback"]
    with pytest.raises(KeyError):
        tel.ler_run("nao-existe")


def test_run_context_manager_registra_erro(tdir):
    with pytest.raises(RuntimeError):
        with tel.run({"entrada": "x"}) as rid:
            raise RuntimeError("falhou")
    res = json.loads((tdir / rid / "resultado.json").read_text())
    assert "falhou" in res["erro"]
