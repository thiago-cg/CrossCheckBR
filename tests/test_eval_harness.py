"""Harness de eval com Pipeline falso (sem rede): casos, métricas, baseline, gate, overrides."""
import json
from pathlib import Path

import pytest

from eval import run as ev
from factcheck_mvp import config, replay, telemetria as tel
from factcheck_mvp.schemas import RelatorioChecagem


@pytest.fixture
def amb(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRIA", "1")
    monkeypatch.setenv("TELEMETRIA_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CASSETES_DIR", str(tmp_path / "cass"))
    monkeypatch.setenv("SERPAPI_USO_ARQ", str(tmp_path / "uso.json"))
    return tmp_path


def _caso(id, conteudo, rotulo, split="dev", tags=(), **kw):
    return {"id": id, "entrada": {"tipo": "titulo", "conteudo": conteudo}, "rotulo": rotulo,
            "tags": list(tags), "split": split, "origem": "manual", "nota": "", **kw}


class PipelineFalso:
    """Devolve o nível do mapa e emite telemetria como o pipeline real."""

    def __init__(self, mapa, pulada=True):
        self.mapa, self.pulada = mapa, pulada

    async def executar(self, entrada):
        tel.iniciar_run({"entrada": entrada.conteudo})  # aninhado no run do eval
        tel.evento("etapa", nome="descoberta", status="pulada" if self.pulada else "ok", detalhe="")
        tel.evento("llm", motor="llm-local", finalidade="afirmacoes", latencia_ms=10)
        tel.fallback("juiz", "sem chave")
        nivel = self.mapa[entrada.conteudo]
        if nivel == "EXPLODE":
            raise RuntimeError("quebrou")
        tel.evento("decisao", nivel=nivel)
        rel = RelatorioChecagem(propensao=nivel, justificativa="j", consulta=entrada)
        tel.finalizar_run(rel.model_dump(mode="json"))
        return rel


CASOS = [
    _caso("f1", "Café cura câncer", "falso", tags=["saude", "fora_do_indice"]),
    _caso("f2", "Vacina altera DNA", "falso", tags=["saude", "no_indice"]),
    _caso("v1", "É falso que a vacina altera o DNA", "verdadeiro", tags=["negacao"]),
    _caso("s1", "Só piorou", "sem_evidencia"),
    _caso("h1", "Holdout", "falso", split="holdout"),
]


def test_casos_reais_validos_e_estaveis():
    casos = ev.carregar_casos()
    ids = [c["id"] for c in casos]
    assert len(ids) == len(set(ids)) >= 12
    assert {c["split"] for c in casos} == {"dev", "holdout"}
    assert all(c["rotulo"] in ev.ROTULOS for c in casos)
    tags = {t for c in casos for t in c["tags"]}
    assert {"adversarial", "negacao", "relato", "caixa"} <= tags
    neg = next(c for c in casos if c["id"] == "falso_que_vacina_dna")
    assert neg["rotulo"] == "verdadeiro" and ev.esperado_de(neg)[0] == ["baixa"]


def test_carregar_multiplos_arquivos_glob_e_validacao(tmp_path):
    (tmp_path / "casos_a.jsonl").write_text(json.dumps(CASOS[0]) + "\n# comentario\n\n")
    (tmp_path / "casos_b.jsonl").write_text(json.dumps(CASOS[1]) + "\n" + json.dumps(CASOS[0]) + "\n")
    cs = ev.carregar_casos([str(tmp_path / "casos*.jsonl")])
    assert [c["id"] for c in cs] == ["f1", "f2"]  # duplicado ignorado
    (tmp_path / "ruim.jsonl").write_text(json.dumps({**CASOS[0], "rotulo": "mentira"}) + "\n")
    with pytest.raises(ValueError):
        ev.carregar_casos([str(tmp_path / "ruim.jsonl")])


def test_esperado_padrao_filtro_e_erro_grave():
    assert ev.esperado_de(CASOS[0]) == (["alta"], ["media"])
    assert ev.esperado_de(CASOS[2]) == (["baixa"], [])
    assert ev.esperado_de(CASOS[3]) == (["indeterminada"], [])
    assert ev.esperado_de(_caso("x", "y", "falso", esperado=["indeterminada", "alta"])) == (["indeterminada", "alta"], [])
    assert [c["id"] for c in ev.filtrar(CASOS, "dev")] == ["f1", "f2", "v1", "s1"]
    assert [c["id"] for c in ev.filtrar(CASOS, "todos", tags=["negacao"])] == ["v1"]
    assert [c["id"] for c in ev.filtrar(CASOS, "dev", ids=["h1"])] == ["h1"]
    assert ev.eh_grave("falso", "baixa") and ev.eh_grave("verdadeiro", "alta")
    assert not ev.eh_grave("falso", "indeterminada") and not ev.eh_grave("sem_evidencia", "alta")


def test_rodar_metricas_artefatos_baseline_e_gate(amb):
    mapa = {"Café cura câncer": "alta", "Vacina altera DNA": "media",
            "É falso que a vacina altera o DNA": "alta", "Só piorou": "indeterminada"}
    kw = dict(modo="replay", dir_resultados=amb / "res", baseline=amb / "baseline.json", progresso=lambda s: None)
    out = ev.rodar(ev.filtrar(CASOS, "dev"), pipeline_factory=lambda: PipelineFalso(mapa), nome="t", **kw)
    m = out["metricas"]
    assert m["n"] == 4 and m["acerto"] == 0.5 and m["acerto_com_parcial"] == 0.75
    assert m["n_erro_grave"] == 1 and m["por_rotulo"]["verdadeiro"]["n_erro_grave"] == 1
    assert m["taxa_indeterminada"] == 0.25 and m["llm_chamadas_por_caso"] == 1
    assert m["taxa_fallback_por_onde"] == {"juiz": 1.0} and m["descoberta"] == {"pulada": 1.0}
    assert set(m["destaque_indice"]) == {"fora_do_indice", "no_indice"}
    assert m["serpapi"]["live_no_eval"] == 0 and out["meta"]["env"] == ev.ENV_PADRAO
    assert m["acuracia_balanceada"] is not None and m["cobertura"] == 0.75
    assert m["auc_ordinal"] is not None and "sempre_alta" in m["baselines_triviais"]
    assert "acerto" in m["intervalos_wilson"]
    d = out["dir"]
    assert (d / "metricas.json").exists() and (d / "casos.jsonl").exists()
    md = (d / "relatorio.md").read_text()
    assert "cli trace" in md and "ATENÇÃO" in md and "🛑" in md
    assert "sempre_alta" in md and "balanceada" in md
    casos = [json.loads(x) for x in (d / "casos.jsonl").read_text().splitlines()]
    assert all(c["run_id"] and (amb / "runs" / c["run_id"] / "resultado.json").exists() for c in casos)
    ev.salvar_baseline(out["meta"], m, out["casos"], amb / "baseline.json")

    # piora: f1 alta->baixa (novo erro grave) => gate falha
    pior = dict(mapa, **{"Café cura câncer": "baixa"})
    out2 = ev.rodar(ev.filtrar(CASOS, "dev"), pipeline_factory=lambda: PipelineFalso(pior), **kw)
    dl = out2["delta"]
    assert dl["n_comuns"] == 4 and dl["n_erro_grave_delta"] == 1 and dl["acerto_pp"] == -25.0
    assert [x["id"] for x in dl["mudaram"]] == ["f1"]
    ok, motivos = ev.avaliar_gate(dl, 5.0)
    assert not ok and len(motivos) >= 3
    assert "vs baseline" in ev.resumo_texto(out2["meta"], out2["metricas"], dl)
    assert "balanceada" in ev.resumo_texto(out2["meta"], out2["metricas"], dl)


def test_esperado_normaliza_legado_claimreview():
    assert ev.esperado_de({"rotulo": "falso", "esperado": ["alta"]}) == (["alta"], ["media"])
    assert ev.esperado_de({"rotulo": "enganoso", "esperado": ["alta", "media"]}) == (["alta"], ["media"])
    assert ev.esperado_de({"rotulo": "falso", "esperado": ["indeterminada", "alta"]}) == (["indeterminada", "alta"], [])


def test_caso_que_quebra_nao_derruba_eval(amb):
    out = ev.rodar([CASOS[0]], pipeline_factory=lambda: PipelineFalso({"Café cura câncer": "EXPLODE"}),
                   dir_resultados=amb / "res", baseline=amb / "nao.json", progresso=lambda s: None)
    r = out["casos"][0]
    assert r["nivel"] is None and "quebrou" in r["erro"] and not r["ok"]
    assert out["metricas"]["n_erros_execucao"] == 1


def test_overrides_env_aplicam_e_restauram(monkeypatch):
    monkeypatch.delenv("INDICE_CHECAGENS", raising=False)
    antes = config.AGENTE_MAX_BUSCAS
    env = ev.parse_env(["AGENTE_MAX_BUSCAS=3", "X_NOVA=1"])
    assert env["INDICE_CHECAGENS"] == "0" and env["AGENTE_MAX_BUSCAS"] == "3"
    with ev.overrides_env(env):
        assert config.AGENTE_MAX_BUSCAS == 3 and isinstance(config.AGENTE_MAX_BUSCAS, int)
        import os
        assert os.environ["INDICE_CHECAGENS"] == "0" and os.environ["X_NOVA"] == "1"
    import os
    assert config.AGENTE_MAX_BUSCAS == antes and "INDICE_CHECAGENS" not in os.environ
    with pytest.raises(SystemExit):
        ev.parse_env(["sem_igual"])


def test_teto_de_buscas_nao_inicia_caso_que_estouraria(amb, monkeypatch):
    monkeypatch.setattr(config, "SERP_ESTRATEGIA", "agente")
    monkeypatch.setattr(config, "AGENTE_MAX_BUSCAS", 3)
    out = ev.rodar(ev.filtrar(CASOS, "dev"), modo="record", max_buscas=2,
                   pipeline_factory=lambda: PipelineFalso({}), dir_resultados=amb / "res",
                   baseline=amb / "nao.json", progresso=lambda s: None)
    assert all(c.get("nao_rodado") for c in out["casos"])
    assert out["metricas"]["n_nao_rodados"] == 4
    assert replay.uso_serpapi()["teto_processo"] is None  # restaurado


def test_descoberta_status_distingue_falhou_de_rodou():
    from eval.run import _descoberta_status, _busca_indisponivel
    etapa = {"tipo": "etapa", "dados": {"nome": "descoberta", "status": "ok"}}
    fb = {"tipo": "fallback", "dados": {"onde": "serpapi", "motivo": "HTTP 429"}}
    ok = {"tipo": "http", "dados": {"url": "https://serpapi.com/search.json?q=x", "status": 200}}
    assert _descoberta_status([etapa, ok]) == "rodou"
    assert _descoberta_status([etapa, fb]) == "falhou"          # cota esgotada: caso não mede a busca
    assert _descoberta_status([etapa, ok, fb]) == "parcial"
    assert _descoberta_status([{"tipo": "etapa", "dados": {"nome": "descoberta", "status": "pulada"}}]) == "pulada"
    assert abs(_busca_indisponivel({"descoberta": {"falhou": 0.4, "pulada": 0.2}}) - 0.6) < 1e-9


def test_descoberta_status_ignora_descoberta_catalogo():
    from eval.run import _descoberta_status
    catalogo_ok = {"tipo": "etapa", "dados": {"nome": "descoberta-catalogo", "status": "ok"}}
    descoberta_pulada = {"tipo": "etapa", "dados": {"nome": "descoberta", "status": "pulada"}}
    descoberta_ok = {"tipo": "etapa", "dados": {"nome": "descoberta", "status": "ok"}}
    agente_ok = {"tipo": "etapa", "dados": {"nome": "descoberta-agente", "status": "ok"}}
    ok = {"tipo": "http", "dados": {"url": "https://serpapi.com/search.json?q=x", "status": 200}}
    assert _descoberta_status([catalogo_ok, descoberta_pulada]) == "pulada"
    assert _descoberta_status([descoberta_ok, catalogo_ok, ok]) == "rodou"
    assert _descoberta_status([agente_ok, ok]) == "rodou"
    assert _descoberta_status([catalogo_ok]) == "ausente"


# ------------------------------------------------------------------ E4: medição do desconto temporal (Task 7)
def _linha_snapshot_e4():
    return {"id": "e4-hoje", "rotulo": "falso", "esperado": ["alta"], "aceitavel": ["media"],
            "tags": [], "run_id": "x",
            "evidencias": {
                "afirmacoes": [{"texto": "Bolsonaro recebeu alta do hospital hoje",
                                "nucleo": "Bolsonaro recebeu alta do hospital", "polaridade": "afirma"}],
                "itens": [{"url": "https://g1.globo.com/a", "afirmacao": 0, "cluster": "g1",
                           "classe": "REFUTA", "motor": "llm-juiz:llm-local",
                           "citacao_verificada": True, "curada": True, "corpo_lido": True,
                           "veredito": None, "origem_veredito": None, "veiculo": "",
                           "data_pub": "2021-01-01"}],
                "vago": False, "opiniao": False, "rumor": False, "juiz_disponivel": True,
                "n_lidas": 1, "n_consultadas": 1,
                "texto_usuario": "Bolsonaro recebeu alta do hospital hoje",
                "data_referencia": "2026-10-09"}}


def test_sem_e4_zera_desconto_e_recupera_modulo():
    from eval.decisao import avaliar_snapshot
    base = avaliar_snapshot([_linha_snapshot_e4()])
    sem = avaliar_snapshot([_linha_snapshot_e4()], sem_e4=True)
    assert base[0]["e4"]["desconto"] and not sem[0]["e4"]["desconto"]
    assert abs(sem[0]["log_odds"]) > abs(base[0]["log_odds"])


def test_gerar_snapshot_de_resultado_le_casos_e_evidencias(amb):
    from eval.decisao import gerar_snapshot_de_resultado
    res_dir = amb / "resultado"
    res_dir.mkdir()
    runs = amb / "runs"
    (runs / "rid1").mkdir(parents=True)
    evd = dict(_linha_snapshot_e4()["evidencias"])
    (runs / "rid1" / "trace.jsonl").write_text(
        json.dumps({"ts": "2026-10-09T00:00:00+00:00", "t_rel_ms": 1.0, "tipo": "evidencias",
                    "dados": evd}, ensure_ascii=False) + "\n", encoding="utf-8")
    (res_dir / "casos.jsonl").write_text(
        json.dumps({"id": "e4-hoje", "rotulo": "falso", "esperado": ["alta"], "aceitavel": ["media"],
                    "tags": [], "run_id": "rid1"}) + "\n", encoding="utf-8")
    n, pul = gerar_snapshot_de_resultado(res_dir, amb / "snap.jsonl", runs_dir=runs)
    assert (n, pul) == (1, 0)
    linha = json.loads((amb / "snap.jsonl").read_text(encoding="utf-8"))
    assert linha["id"] == "e4-hoje" and linha["evidencias"]["data_referencia"] == "2026-10-09"


def test_metricas_e4_agrega_por_caso():
    assert ev.calcular_metricas([
        {"rotulo": "falso", "nivel": "media", "ok": False, "parcial": True, "grave": False,
         "e4": {"marcador": True, "desconto": True, "nivel_mudou": True, "bits": 1.5,
                "referencia_ausente": False}},
        {"rotulo": "verdadeiro", "nivel": "baixa", "ok": True, "parcial": False, "grave": False,
         "e4": {"marcador": False, "desconto": False, "nivel_mudou": False, "bits": 0.0,
                "referencia_ausente": True}},
    ])["e4"] == {"casos_com_marcador": 1, "casos_com_desconto": 1, "casos_nivel_mudou": 1,
                 "bits_descartados_total": 1.5, "referencia_ausente": 1}
