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
    return [json.loads(x) for x in (tdir / rid / "trace.jsonl").read_text(encoding="utf-8").splitlines()]


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
    res = json.loads((tdir / rid / "resultado.json").read_text(encoding="utf-8"))
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
    txt = (tdir / rid / "trace.jsonl").read_text(encoding="utf-8")
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
    res = json.loads((tdir / externo / "resultado.json").read_text(encoding="utf-8"))
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
    res = json.loads((tdir / rid / "resultado.json").read_text(encoding="utf-8"))
    assert "falhou" in res["erro"]


def test_trace_avaliador_por_peca_tem_campos(tdir):
    """Contrato do N×1: fonte `avaliador` por peça carrega posicao/n_chars_trecho/
    corpo_lido/metodo; timeout/cap vira fallback com parcial preservado."""
    with tel.run({"entrada": "Ibuprofeno cura dengue"}) as rid:
        tel.evento("fonte", url="https://ex00.com/noticia/0", estagio="avaliador", decisao="mantida",
                   motivo="avaliador: REFUTA", afirmacao=0, posicao="REFUTA", classe="REFUTA",
                   n_chars_trecho=1234, corpo_lido=True, metodo="llm-juiz:fake")
        tel.fallback("deep-crawl", "teto 25s/cap: 10/20 sem resposta, parcial preservado")
        tel.fallback("avaliador", "teto 600s: 2 sem avaliar, parcial preservado")
    ev = _eventos(tdir, rid)
    fontes = [e for e in ev if e["tipo"] == "fonte" and e["dados"].get("estagio") == "avaliador"]
    assert len(fontes) == 1
    assert {"posicao", "n_chars_trecho", "corpo_lido", "metodo"} <= set(fontes[0]["dados"])
    ondes = {e["dados"].get("onde") for e in ev if e["tipo"] == "fallback"}
    assert {"deep-crawl", "avaliador"} <= ondes


# ------------------------------------------------------------------ E4: telemetria do desconto temporal (Task 7)
_JUIZ_E4 = "llm-juiz:llm-local"


def _dec_e4_com_desconto(monkeypatch, data_pub="2021-01-01", bruta=None, precisao=None):
    from factcheck_mvp import decisao
    from factcheck_mvp.decisao import AfirmacaoDecisao, Evidencias, ItemEvidencia, decidir
    monkeypatch.setattr(decisao, "relevancia_temporal", lambda e, j: 0.05)
    texto = "Bolsonaro recebeu alta do hospital hoje"
    it = ItemEvidencia(url="https://g1.globo.com/noticia-sobre-alta-do-hospital-hoje", cluster="g1",
                       classe="REFUTA", motor=_JUIZ_E4, citacao_verificada=True, curada=True,
                       corpo_lido=True, data_pub=data_pub, data_pub_bruta=bruta, data_pub_precisao=precisao)
    ev = Evidencias(afirmacoes=[AfirmacaoDecisao(texto=texto, nucleo=texto)], itens=[it],
                    texto_usuario=texto, data_referencia="2026-10-09")
    d = decidir(ev)
    assert d.descontos_temporais
    return d


def test_e4_resumo_trace_traz_contrafactual_e_descontos(tdir, monkeypatch):
    d = _dec_e4_com_desconto(monkeypatch)
    r = d.resumo_trace()
    assert r["L_sem_desconto"] == round(d.log_odds_sem_desconto, 3)
    assert r["nivel_sem_desconto"] == d.nivel_sem_desconto
    assert len(r["descontos"]) == len(d.descontos_temporais) <= 6
    x = d.descontos_temporais[0]
    assert r["descontos"][0] == f"{x['url'][:60]} +{x['dias_alem_da_janela']}d r={x['r']} −{x['bits_descartados']}b"


def test_resumo_trace_nao_tem_chave_repetida():
    """Chave repetida num dict literal some em silêncio (L, p e motivo estavam duas vezes):
    confere a fonte, porque em tempo de execução a duplicata já não aparece."""
    import ast
    import inspect
    import textwrap
    from factcheck_mvp.decisao import Decisao
    arvore = ast.parse(textwrap.dedent(inspect.getsource(Decisao.resumo_trace)))
    retorno = next(n for n in ast.walk(arvore) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict))
    chaves = [k.value for k in retorno.value.keys if isinstance(k, ast.Constant)]
    assert len(chaves) == len(set(chaves)), sorted({c for c in chaves if chaves.count(c) > 1})
    # M7: em runtime, `**self.travas` sobrescreve em silêncio uma chave literal de mesmo nome (a fonte
    # não acusa). Com as travas que `decidir` de fato produz, o resumo precisa ter as literais e as
    # travas todas, sem perda.
    from factcheck_mvp.decisao import AfirmacaoDecisao, Evidencias, decidir
    d = decidir(Evidencias(afirmacoes=[AfirmacaoDecisao(texto="Governo vai confiscar a poupança")]))
    r = d.resumo_trace()
    assert not (set(chaves) & set(d.travas)), sorted(set(chaves) & set(d.travas))
    assert len(r) == len(set(chaves)) + len(d.travas)


def test_e4_emitir_decisao_emite_fonte_data(tdir, monkeypatch):
    from factcheck_mvp.pipeline import Pipeline
    # Peça com placeholder de ano: a bruta vem como "2021-01-01" e a normalizada é o fim do ano.
    d = _dec_e4_com_desconto(monkeypatch, data_pub="2021-12-31", bruta="2021-01-01", precisao="ano")
    with tel.run({"entrada": "Bolsonaro recebeu alta do hospital hoje"}):
        Pipeline._emitir_decisao(d)
    ev = _eventos(tdir, tel.ultimo_run_id())
    fontes = [e for e in ev if e["tipo"] == "fonte" and e["dados"].get("estagio") == "data"]
    assert len(fontes) == len(d.descontos_temporais) == 1
    f = fontes[0]["dados"]
    x = d.descontos_temporais[0]
    assert f["decisao"] == "descontada"
    assert f["motivo"] == f"{x['dias_alem_da_janela']} dias além da janela de {x['janela']}"
    assert f["afirmacao"] == 0 and f["data_pub"] == "2021-12-31"
    assert f["data_pub_bruta"] == "2021-01-01" and f["precisao"] == "ano"
    assert f["r"] == x["r"] and f["bits"] == x["bits_descartados"]
