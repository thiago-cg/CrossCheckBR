"""Harness de avaliação: roda o pipeline nos casos rotulados e mede.

  python3 -m eval.run [--modo replay|record|live] [--split dev|holdout|todos]
                      [--casos 'eval/casos*.jsonl' ...] [--filtro-tag negacao] [--ids a,b]
                      [--env CHAVE=VALOR ...] [--nome x] [--paralelo N]
                      [--max-buscas-serpapi N] [--salvar-baseline] [--gate]

Saída: eval/resultados/<timestamp>[-<nome>]/{metricas.json, casos.jsonl, relatorio.md}
+ resumo no stdout com delta vs eval/baseline.json (sobre os ids em comum).
Cada caso vira um run de telemetria (runs/<run_id>): `python3 -m factcheck_mvp.cli trace <run_id>`.

Perfil padrão: INDICE_CHECAGENS=0 (mede a busca aberta). O atalho do índice é
métrica secundária: `--env INDICE_CHECAGENS=1 --nome indice`.
"""
from __future__ import annotations

import argparse
import asyncio
import glob as _glob
import json
import math
import os
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from factcheck_mvp import config, replay, telemetria  # noqa: E402  (config carrega .env)
from factcheck_mvp.schemas import EntradaConsulta  # noqa: E402

DIR_EVAL = Path(__file__).resolve().parent
CASOS_PADRAO = str(DIR_EVAL / "casos*.jsonl")
BASELINE = DIR_EVAL / "baseline.json"
RESULTADOS = DIR_EVAL / "resultados"

NIVEIS = ("baixa", "media", "alta", "indeterminada")
ROTULOS = ("falso", "enganoso", "verdadeiro", "sem_evidencia")
# rótulo -> (esperado, aceitável como acerto parcial). Propensão = "a se tratar de desinformação".
MAPA_ESPERADO: Dict[str, Tuple[List[str], List[str]]] = {
    "falso": (["alta"], ["media"]),
    "enganoso": (["alta"], ["media"]),
    "verdadeiro": (["baixa"], []),
    "sem_evidencia": (["indeterminada"], []),
}
ENV_PADRAO = {"INDICE_CHECAGENS": "0"}  # perfil principal: busca aberta
TAGS_DESTAQUE = ("fora_do_indice", "no_indice")


# --------------------------------------------------------------------------
# Casos

def carregar_casos(padroes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Lê 1+ arquivos/globs JSONL (linhas vazias e '#' ignoradas). Ids duplicados: vale o 1º."""
    arquivos: List[str] = []
    for p in (padroes or [CASOS_PADRAO]):
        achados = sorted(_glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
        arquivos += [a for a in achados if a not in arquivos]
    casos, vistos = [], set()
    for arq in arquivos:
        with open(arq, encoding="utf-8") as f:
            for n, linha in enumerate(f, 1):
                linha = linha.strip()
                if not linha or linha.startswith("#"):
                    continue
                try:
                    c = json.loads(linha)
                except ValueError as e:
                    raise ValueError(f"{arq}:{n}: JSON inválido: {e}")
                validar_caso(c, f"{arq}:{n}")
                if c["id"] in vistos:
                    print(f"[aviso] id duplicado ignorado: {c['id']} ({arq}:{n})", file=sys.stderr)
                    continue
                vistos.add(c["id"])
                c["_arquivo"] = os.path.relpath(arq, RAIZ)
                casos.append(c)
    return casos


def validar_caso(c: Dict[str, Any], onde: str = "") -> None:
    for campo in ("id", "entrada", "rotulo"):
        if campo not in c:
            raise ValueError(f"{onde}: caso sem campo obrigatório {campo!r}")
    e = c["entrada"]
    if not isinstance(e, dict) or e.get("tipo") not in ("texto", "titulo", "link") or not e.get("conteudo"):
        raise ValueError(f"{onde}: entrada deve ser {{tipo: texto|titulo|link, conteudo}}")
    if c["rotulo"] not in ROTULOS:
        raise ValueError(f"{onde}: rotulo {c['rotulo']!r} fora de {ROTULOS}")
    for campo in ("esperado", "aceitavel"):
        if campo in c and (not isinstance(c[campo], list) or any(x not in NIVEIS for x in c[campo])):
            raise ValueError(f"{onde}: {campo} deve ser lista de {NIVEIS}")


def esperado_de(c: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Resolve (esperado, aceitavel) pelo rótulo; normaliza legados claimreview p/ README.

    README: falso/enganoso esperado [alta], parcial [media].
    Legados claimreview gravam `esperado` explícito sem `aceitavel`:
      falso {"esperado":["alta"]} -> (["alta"],["media"])
      enganoso {"esperado":["alta","media"]} -> (["alta"],["media"])
    Overrides genuínos preservados (com `aceitavel` explícito ou `esperado`
    fora de {"alta"}/{"alta","media"}).
    """
    padrao_esp, padrao_ac = MAPA_ESPERADO[c["rotulo"]]
    if "esperado" not in c:
        esp = c.get("esperado") or padrao_esp
        ac = c.get("aceitavel", padrao_ac)
        return list(esp), [x for x in ac if x not in esp]
    esp_raw = c.get("esperado") or padrao_esp
    if "aceitavel" in c:
        return list(esp_raw), [x for x in c["aceitavel"] if x not in esp_raw]
    if c["rotulo"] in ("falso", "enganoso") and set(esp_raw) in ({"alta"}, {"alta", "media"}):
        return ["alta"], ["media"]
    return list(esp_raw), []


def filtrar(casos: List[Dict[str, Any]], split: str = "dev", tags: Optional[List[str]] = None,
            ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out = casos
    if ids:
        out = [c for c in out if c["id"] in set(ids)]
    elif split != "todos":
        out = [c for c in out if c.get("split", "dev") == split]
    if tags:
        out = [c for c in out if set(tags) & set(c.get("tags") or [])]
    return out


def eh_grave(rotulo: str, nivel: Optional[str]) -> bool:
    """Erro grave: falso/enganoso -> baixa, ou verdadeiro -> alta."""
    return (rotulo in ("falso", "enganoso") and nivel == "baixa") or (rotulo == "verdadeiro" and nivel == "alta")


# --------------------------------------------------------------------------
# Overrides de configuração (isolados por execução do eval)

def _coagir(atual: Any, valor: str) -> Any:
    if isinstance(atual, bool):
        return valor.strip().lower() in ("1", "true", "sim", "yes", "on")
    if isinstance(atual, int):
        return int(valor)
    if isinstance(atual, float):
        return float(valor)
    return valor


class overrides_env:
    """Aplica CHAVE=VALOR em os.environ e em factcheck_mvp.config (tipo preservado); restaura na saída."""

    def __init__(self, env: Dict[str, str]):
        self.env = dict(env)
        self._env_antes: Dict[str, Optional[str]] = {}
        self._cfg_antes: Dict[str, Any] = {}

    def __enter__(self):
        for k, v in self.env.items():
            self._env_antes[k] = os.environ.get(k)
            os.environ[k] = str(v)
            if hasattr(config, k):
                self._cfg_antes[k] = getattr(config, k)
                setattr(config, k, _coagir(getattr(config, k), str(v)))
        return self

    def __exit__(self, *a):
        for k, v in self._env_antes.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k, v in self._cfg_antes.items():
            setattr(config, k, v)
        return False


def parse_env(itens: Optional[List[str]]) -> Dict[str, str]:
    out = dict(ENV_PADRAO)
    for it in itens or []:
        if "=" not in it:
            raise SystemExit(f"--env espera CHAVE=VALOR (recebi {it!r})")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _estimativa_buscas_por_caso() -> int:
    """Teto de buscas SerpAPI live que 1 caso pode gastar (p/ não começar caso que estouraria)."""
    est = (getattr(config, "SERP_ESTRATEGIA", "") or "").strip().lower()
    if est == "agente":
        return int(getattr(config, "AGENTE_MAX_BUSCAS", 8) or 8)
    return 3 * int(getattr(config, "MAX_AFIRMACOES", 3) or 3)  # até 3 queries/afirmação (ambas)


# --------------------------------------------------------------------------
# Execução

def _descoberta_status(eventos: List[Dict[str, Any]]) -> str:
    """rodou | parcial | falhou | pulada | ausente — a partir do trace.

    `falhou` = a descoberta tentou buscar mas TODAS as buscas foram recusadas (cota da SerpAPI,
    timeout): o caso não mede a qualidade da busca/juiz. `parcial` = algumas falharam.

    Só contam as etapas de busca web real: `descoberta` e `descoberta-agente`.
    `descoberta-catalogo` (proposta de catálogo) é ignorada.
    """
    nomes = ("descoberta", "descoberta-agente")
    sts = [e["dados"].get("status") for e in eventos
           if e.get("tipo") == "etapa" and str(e["dados"].get("nome", "")) in nomes]
    if not sts:
        return "ausente"
    if all(s == "pulada" for s in sts):
        return "pulada"
    falhas = sum(1 for e in eventos if e.get("tipo") == "fallback"
                 and str(e["dados"].get("onde", "")).startswith("serpapi"))
    ok = sum(1 for e in eventos if e.get("tipo") == "http"
             and "serpapi.com" in str(e["dados"].get("url", "")) and e["dados"].get("status") == 200)
    if falhas and not ok:
        return "falhou"
    return "parcial" if falhas else "rodou"


def _busca_indisponivel(m: Dict[str, Any]) -> float:
    d = m.get("descoberta", {}) or {}
    return float(d.get("pulada", 0) or 0) + float(d.get("falhou", 0) or 0)


async def avaliar_caso(pipe: Any, caso: Dict[str, Any], timeout: float, modo: str) -> Dict[str, Any]:
    esp, ac = esperado_de(caso)
    ent = caso["entrada"]
    res: Dict[str, Any] = {"id": caso["id"], "rotulo": caso["rotulo"], "tags": caso.get("tags") or [],
                           "split": caso.get("split", "dev"), "origem": caso.get("origem"),
                           "entrada": ent["conteudo"][:200], "esperado": esp, "aceitavel": ac}
    t0 = time.perf_counter()
    with replay.modo(modo):
        rid = telemetria.iniciar_run({"entrada": ent["conteudo"][:500], "tipo": ent["tipo"],
                                      "caso_id": caso["id"], "rotulo": caso["rotulo"], "esperado": esp,
                                      "origem_run": "eval"})
        nivel, erro = None, None
        try:
            entrada = EntradaConsulta(tipo=ent["tipo"], conteudo=ent["conteudo"])
            rel = await asyncio.wait_for(pipe.executar(entrada), timeout=timeout)
            nivel = rel.propensao
            res["why"] = rel.why_1linha
        except asyncio.TimeoutError:
            erro = f"timeout {timeout:.0f}s"
        except Exception as e:  # caso quebrado não derruba o eval
            erro = f"{type(e).__name__}: {e}"[:300]
        if erro:
            telemetria.evento("erro", onde="eval", erro=erro)
        telemetria.finalizar_run(None, erro=erro)
    res.update(run_id=rid, nivel=nivel, erro=erro, dur_ms=round((time.perf_counter() - t0) * 1000, 1))
    res["ok"] = nivel in esp
    res["parcial"] = (not res["ok"]) and nivel in ac
    res["grave"] = eh_grave(caso["rotulo"], nivel)
    m: Dict[str, Any] = {}
    eventos: List[Dict[str, Any]] = []
    if rid:
        try:
            r = telemetria.ler_run(rid)
            m, eventos = r["metricas"], r["eventos"]
        except KeyError:
            pass
    res["n_llm"] = m.get("n_llm", 0)
    res["n_fallbacks"] = m.get("n_fallbacks", 0)
    res["fallbacks_por_onde"] = m.get("fallbacks_por_onde", {})
    res["descartes_por_motivo"] = m.get("descartes_por_motivo", {})
    res["descoberta"] = _descoberta_status(eventos) if eventos else "desconhecida"
    res["serpapi_live"] = sum(1 for e in eventos if e.get("tipo") == "http"
                              and "serpapi.com" in str(e["dados"].get("url", ""))
                              and e["dados"].get("cache") == "live")
    res["http_miss"] = sum(1 for e in eventos if e.get("tipo") == "http" and e["dados"].get("cache") == "miss")
    n_llm_miss = sum(1 for e in eventos if e.get("tipo") == "llm" and e["dados"].get("cache") == "miss")
    n_or_http_miss = sum(1 for e in eventos if e.get("tipo") == "http"
                         and "openrouter.ai" in str(e["dados"].get("url", ""))
                         and e["dados"].get("cache") == "miss")
    res["llm_miss"] = n_llm_miss if n_llm_miss else n_or_http_miss
    res["nao_reproduzido"] = bool(modo == "replay" and res["llm_miss"] > 0)
    return res


async def executar_casos(pipe: Any, casos: List[Dict[str, Any]], modo: str = "replay", paralelo: int = 1,
                         timeout: float = 300.0, max_buscas: Optional[int] = None,
                         progresso: Callable[[str], None] = lambda s: None) -> List[Dict[str, Any]]:
    """Roda os casos (N em paralelo). Com max_buscas, não inicia caso que poderia estourar o teto."""
    sem = asyncio.Semaphore(max(1, paralelo))
    estimativa = _estimativa_buscas_por_caso()
    resultados: Dict[str, Dict[str, Any]] = {}

    async def _um(c: Dict[str, Any]) -> None:
        async with sem:
            if replay.cota_esgotada():
                resultados[c["id"]] = {"id": c["id"], "rotulo": c["rotulo"], "nao_rodado": True,
                                       "motivo": "cota SerpAPI esgotada (troque SERPAPI_KEY)"}
                progresso(f"  - {c['id']}: NÃO RODADO (cota SerpAPI esgotada)")
                return
            if max_buscas is not None and modo != "replay":
                usadas = replay.uso_serpapi()["usadas_processo"]
                if usadas + estimativa > max_buscas:
                    resultados[c["id"]] = {"id": c["id"], "rotulo": c["rotulo"], "nao_rodado": True,
                                           "motivo": f"teto --max-buscas-serpapi ({usadas}+{estimativa}>{max_buscas})"}
                    progresso(f"  - {c['id']}: NÃO RODADO (teto de buscas SerpAPI)")
                    return
            r = await avaliar_caso(pipe, c, timeout, modo)
            resultados[c["id"]] = r
            marca = "OK " if r["ok"] else ("~  " if r["parcial"] else ("!! " if r["grave"] else "x  "))
            progresso(f"  {marca}{c['id']:<28} nivel={str(r['nivel']):<13} esperado={'/'.join(r['esperado'])}"
                      f" {_fmt_s(r['dur_ms'])} serp={r['serpapi_live']} fb={r['n_fallbacks']} run={r['run_id']}"
                      + (f" ERRO {r['erro']}" if r.get("erro") else ""))

    await asyncio.gather(*[_um(c) for c in casos])
    return [resultados[c["id"]] for c in casos if c["id"] in resultados]


def _saldo(d: Dict[str, Any]) -> str:
    if d.get("orcamento") is None:
        return f"{d.get('usadas_total', d.get('usadas'))} no total, sem teto"
    return f"saldo {d.get('restantes')}/{d.get('orcamento')}"


def _fmt_s(ms: Any) -> str:
    try:
        return f"{float(ms) / 1000:.1f}s"
    except (TypeError, ValueError):
        return "?"


# --------------------------------------------------------------------------
# Métricas

def _pct(xs: List[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[i], 1)


def _bloco(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rs)
    if not n:
        return {"n": 0}
    return {"n": n,
            "acerto": round(sum(r["ok"] for r in rs) / n, 4),
            "acerto_com_parcial": round(sum(r["ok"] or r["parcial"] for r in rs) / n, 4),
            "n_erro_grave": sum(r["grave"] for r in rs),
            "erro_grave": round(sum(r["grave"] for r in rs) / n, 4),
            "taxa_indeterminada": round(sum(r["nivel"] == "indeterminada" for r in rs) / n, 4),
            "niveis": dict(Counter(str(r["nivel"]) for r in rs))}


SCORE_ORDINAL = {"baixa": 0, "media": 1, "indeterminada": 1, "alta": 2}


def _wilson(k: int, n: int, z: float = 1.96) -> List[Optional[float]]:
    """IC95 Wilson p/ proporção k/n. [None,None] se n==0."""
    if n <= 0:
        return [None, None]
    p = k / n
    denom = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / denom
    delta = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centro - delta), 4), round(min(1.0, centro + delta), 4)]


def _recall_por_rotulo(rs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for rot in ROTULOS:
        sub = [r for r in rs if r["rotulo"] == rot]
        if not sub:
            continue
        k = sum(1 for r in sub if r.get("ok"))
        out[rot] = {"n": len(sub), "ok": k, "recall": round(k / len(sub), 4),
                    "wilson": _wilson(k, len(sub))}
    return out


def _acuracia_balanceada(rs: List[Dict[str, Any]]) -> Optional[float]:
    rec = _recall_por_rotulo(rs)
    if not rec:
        return None
    return round(sum(v["recall"] for v in rec.values()) / len(rec), 4)


def _erro_grave_por_direcao(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
    fe = [r for r in rs if r["rotulo"] in ("falso", "enganoso")]
    v = [r for r in rs if r["rotulo"] == "verdadeiro"]
    fe_k = sum(1 for r in fe if r.get("grave"))
    v_k = sum(1 for r in v if r.get("grave"))
    return {"fe_para_baixa": round(fe_k / len(fe), 4) if fe else None,
            "fe_k": fe_k, "fe_n": len(fe), "fe_wilson": _wilson(fe_k, len(fe)),
            "v_para_alta": round(v_k / len(v), 4) if v else None,
            "v_k": v_k, "v_n": len(v), "v_wilson": _wilson(v_k, len(v))}


def _precisao_por_nivel(rs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for niv in NIVEIS:
        sub = [r for r in rs if r.get("nivel") == niv]
        k = sum(1 for r in sub if r.get("ok"))
        out[niv] = {"n": len(sub), "ok": k,
                    "precisao": round(k / len(sub), 4) if sub else None,
                    "wilson": _wilson(k, len(sub))}
    n_none = sum(1 for r in rs if r.get("nivel") not in NIVEIS)
    if n_none:
        out["sem_nivel"] = {"n": n_none, "ok": 0, "precisao": 0.0,
                            "wilson": _wilson(0, n_none)}
    return out


def _auc_ordinal(rs: List[Dict[str, Any]]) -> Optional[float]:
    pos = [SCORE_ORDINAL[r["nivel"]] for r in rs
           if r["rotulo"] in ("falso", "enganoso") and r.get("nivel") in SCORE_ORDINAL]
    neg = [SCORE_ORDINAL[r["nivel"]] for r in rs
           if r["rotulo"] == "verdadeiro" and r.get("nivel") in SCORE_ORDINAL]
    if not pos or not neg:
        return None
    conc = sum(1 for a in pos for b in neg if a > b)
    ties = sum(1 for a in pos for b in neg if a == b)
    return round((conc + 0.5 * ties) / (len(pos) * len(neg)), 4)


def _sintetizar_trivial(rs: List[Dict[str, Any]], nivel_fixo: str) -> List[Dict[str, Any]]:
    pseudo = []
    for r in rs:
        esp = r.get("esperado") or []
        ac = r.get("aceitavel") or []
        ok = nivel_fixo in esp
        pseudo.append({"rotulo": r["rotulo"], "nivel": nivel_fixo, "ok": ok,
                       "parcial": (not ok) and nivel_fixo in ac,
                       "grave": eh_grave(r["rotulo"], nivel_fixo)})
    return pseudo


def _bloco_completo(rs: List[Dict[str, Any]]) -> Dict[str, Any]:
    b = _bloco(rs)
    if not b.get("n"):
        return b
    b["acuracia_balanceada"] = _acuracia_balanceada(rs)
    b["erro_grave_por_direcao"] = _erro_grave_por_direcao(rs)
    b["cobertura"] = round(1 - b["taxa_indeterminada"], 4)
    b["auc_ordinal"] = _auc_ordinal(rs)
    return b


def _baselines_triviais(rs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {f"sempre_{s}": _bloco_completo(_sintetizar_trivial(rs, s))
            for s in ("alta", "media")}


def calcular_metricas(res: List[Dict[str, Any]]) -> Dict[str, Any]:
    rodados = [r for r in res if not r.get("nao_rodado") and not r.get("nao_reproduzido")]
    m = _bloco(rodados)
    n = len(rodados) or 1
    m["n_nao_rodados"] = sum(1 for r in res if r.get("nao_rodado"))
    m["n_nao_reproduzidos"] = sum(1 for r in res if r.get("nao_reproduzido"))
    m["n_erros_execucao"] = sum(1 for r in rodados if r.get("erro"))
    m["por_rotulo"] = {rot: _bloco([r for r in rodados if r["rotulo"] == rot])
                       for rot in ROTULOS if any(r["rotulo"] == rot for r in rodados)}
    tags = sorted({t for r in rodados for t in r.get("tags") or []})
    m["por_tag"] = {t: _bloco([r for r in rodados if t in (r.get("tags") or [])]) for t in tags}
    m["destaque_indice"] = {t: m["por_tag"][t] for t in TAGS_DESTAQUE if t in m["por_tag"]}
    ondes = Counter(o for r in rodados for o in (r.get("fallbacks_por_onde") or {}))
    m["taxa_fallback_por_onde"] = {o: round(c / n, 4) for o, c in ondes.most_common()}
    tot = Counter()
    for r in rodados:
        tot.update(r.get("fallbacks_por_onde") or {})
    m["fallbacks_total_por_onde"] = dict(tot.most_common())
    desc = Counter()
    for r in rodados:
        desc.update(r.get("descartes_por_motivo") or {})
    m["descartes_por_motivo"] = dict(desc.most_common())
    m["llm_chamadas_total"] = sum(r.get("n_llm", 0) for r in rodados)
    m["llm_chamadas_por_caso"] = round(m["llm_chamadas_total"] / n, 2)
    durs = [float(r["dur_ms"]) for r in rodados if r.get("dur_ms") is not None]
    m["latencia_p50_ms"], m["latencia_p95_ms"] = _pct(durs, 0.5), _pct(durs, 0.95)
    m["latencia_media_ms"] = round(statistics.mean(durs), 1) if durs else None
    desc_st = Counter(r.get("descoberta") for r in rodados)
    m["descoberta"] = {k: round(v / n, 4) for k, v in desc_st.items()}
    m["http_miss_total"] = sum(r.get("http_miss", 0) for r in rodados)
    m["llm_miss_total"] = sum(int(r.get("llm_miss") or 0) for r in res if not r.get("nao_rodado"))
    rec = _recall_por_rotulo(rodados)
    m["recall_por_rotulo"] = rec
    m["acuracia_balanceada"] = round(sum(v["recall"] for v in rec.values()) / len(rec), 4) if rec else None
    m["erro_grave_por_direcao"] = _erro_grave_por_direcao(rodados)
    m["cobertura"] = round(1 - m.get("taxa_indeterminada", 0), 4) if m.get("n") else None
    m["precisao_por_nivel"] = _precisao_por_nivel(rodados)
    m["auc_ordinal"] = _auc_ordinal(rodados)
    m["baselines_triviais"] = _baselines_triviais(rodados)
    n_ok = sum(r["ok"] for r in rodados)
    n_grave = sum(r["grave"] for r in rodados)
    n_ind = sum(r["nivel"] == "indeterminada" for r in rodados)
    m["intervalos_wilson"] = {
        "acerto": _wilson(n_ok, len(rodados)),
        "erro_grave": _wilson(n_grave, len(rodados)),
        "taxa_indeterminada": _wilson(n_ind, len(rodados)),
        "cobertura": _wilson(len(rodados) - n_ind, len(rodados)),
        "recall_por_rotulo": {k: v["wilson"] for k, v in rec.items()},
        "precisao_por_nivel": {k: v["wilson"] for k, v in m["precisao_por_nivel"].items()},
    }
    return m


def comparar(res: List[Dict[str, Any]], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Delta vs baseline sobre os ids em comum (casos novos não distorcem)."""
    base_casos = {c["id"]: c for c in baseline.get("casos", []) if not c.get("nao_rodado")
                  and not c.get("nao_reproduzido")}
    comuns = [r for r in res if not r.get("nao_rodado") and not r.get("nao_reproduzido")
              and r["id"] in base_casos]
    if not comuns:
        return {"n_comuns": 0, "n_nao_reproduzidos": sum(1 for r in res if r.get("nao_reproduzido"))}
    base_comuns = [base_casos[r["id"]] for r in comuns]
    atual = _bloco_completo(comuns)
    antes = _bloco_completo([{"rotulo": c.get("rotulo"), "nivel": c.get("nivel"),
                              "ok": bool(c.get("ok")), "parcial": bool(c.get("parcial")),
                              "grave": bool(c.get("grave"))} for c in base_comuns])
    mudaram = [{"id": r["id"], "antes": base_casos[r["id"]].get("nivel"), "agora": r["nivel"],
                "ok_antes": base_casos[r["id"]].get("ok"), "ok_agora": r["ok"]}
               for r in comuns if base_casos[r["id"]].get("nivel") != r["nivel"]]
    bal_pp = (round((atual["acuracia_balanceada"] - antes["acuracia_balanceada"]) * 100, 1)
              if atual.get("acuracia_balanceada") is not None and antes.get("acuracia_balanceada") is not None else None)
    gd_at, gd_an = atual["erro_grave_por_direcao"], antes["erro_grave_por_direcao"]
    return {"n_comuns": len(comuns), "baseline_nome": baseline.get("meta", {}).get("nome"),
            "acerto_pp": round((atual["acerto"] - antes["acerto"]) * 100, 1),
            "acerto_com_parcial_pp": round((atual["acerto_com_parcial"] - antes["acerto_com_parcial"]) * 100, 1),
            "acuracia_balanceada_pp": bal_pp,
            "n_erro_grave_delta": atual["n_erro_grave"] - antes["n_erro_grave"],
            "grave_dir_delta": {"fe": gd_at["fe_k"] - gd_an["fe_k"], "v": gd_at["v_k"] - gd_an["v_k"]},
            "taxa_indeterminada_pp": round((atual["taxa_indeterminada"] - antes["taxa_indeterminada"]) * 100, 1),
            "cobertura_pp": round((atual["cobertura"] - antes["cobertura"]) * 100, 1),
            "antes": antes, "agora": atual, "mudaram": mudaram,
            "trivial": _baselines_triviais(comuns)}


def avaliar_gate(delta: Dict[str, Any], max_queda_pp: float) -> Tuple[bool, List[str]]:
    if not delta.get("n_comuns"):
        return True, ["sem casos em comum com o baseline: gate não avaliado"]
    motivos = []
    if delta["n_erro_grave_delta"] > 0:
        motivos.append(f"erro_grave aumentou (+{delta['n_erro_grave_delta']})")
    gd = delta.get("grave_dir_delta") or {}
    if gd.get("fe", 0) > 0:
        motivos.append(f"grave F/E->baixa aumentou (+{gd['fe']})")
    if gd.get("v", 0) > 0:
        motivos.append(f"grave V->alta aumentou (+{gd['v']})")
    if delta["acerto_pp"] < -abs(max_queda_pp):
        motivos.append(f"acerto caiu {delta['acerto_pp']}pp (limite -{abs(max_queda_pp)}pp)")
    bal_agora = (delta.get("agora") or {}).get("acuracia_balanceada")
    bal_tri = ((delta.get("trivial") or {}).get("sempre_alta") or {}).get("acuracia_balanceada")
    if bal_agora is not None and bal_tri is not None and bal_agora < bal_tri:
        motivos.append(f"balanceada {bal_agora} < sempre_alta {bal_tri}")
    return (not motivos), motivos


# --------------------------------------------------------------------------
# Saída

def _git_sha() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        suja = subprocess.run(["git", "status", "--porcelain", "--", "factcheck_mvp"], cwd=RAIZ,
                              capture_output=True, text=True, timeout=5).stdout.strip()
        return sha + ("+dirty" if suja else "")
    except Exception:
        return "?"


def resumo_texto(meta: Dict[str, Any], m: Dict[str, Any], delta: Optional[Dict[str, Any]]) -> str:
    tri = m.get("baselines_triviais") or {}
    sa = (tri.get("sempre_alta") or {})
    sm = (tri.get("sempre_media") or {})
    gd = m.get("erro_grave_por_direcao") or {}
    L = [f"EVAL {meta['nome'] or ''} | modo={meta['modo']} split={meta['split']} n={m.get('n', 0)}"
         f" (não rodados {m.get('n_nao_rodados', 0)}, não reproduzidos {m.get('n_nao_reproduzidos', 0)})"
         f" | env={meta['env']}",
         f"  acerto={m.get('acerto')} acerto+parcial={m.get('acerto_com_parcial')} "
         f"erro_grave={m.get('erro_grave')} ({m.get('n_erro_grave')}) indeterminada={m.get('taxa_indeterminada')}",
         f"  balanceada={m.get('acuracia_balanceada')} (sempre_alta={sa.get('acuracia_balanceada')} "
         f"sempre_media={sm.get('acuracia_balanceada')}) auc={m.get('auc_ordinal')} cobertura={m.get('cobertura')} "
         f"grave_dir={gd.get('fe_k')}/{gd.get('fe_n')} FE->baixa,{gd.get('v_k')}/{gd.get('v_n')} V->alta",
         f"  trivial: sempre_alta acerto={sa.get('acerto')} grave={sa.get('n_erro_grave')} | "
         f"sempre_media acerto={sm.get('acerto')} grave={sm.get('n_erro_grave')}",
         f"  niveis={m.get('niveis')} descoberta={m.get('descoberta')}",
         f"  llm/caso={m.get('llm_chamadas_por_caso')} p50={_fmt_s(m.get('latencia_p50_ms'))} "
         f"p95={_fmt_s(m.get('latencia_p95_ms'))} erros_exec={m.get('n_erros_execucao')} "
         f"http_miss={m.get('http_miss_total')} llm_miss={m.get('llm_miss_total')}",
         f"  fallback(frac casos)={m.get('taxa_fallback_por_onde')}",
         f"  serpapi: {m.get('serpapi', {}).get('live_no_eval')} busca(s) live neste eval "
         f"({_saldo(m.get('serpapi', {}))})"]
    if m.get("cota_serpapi_esgotada"):
        L.append("  COTA SERPAPI ESGOTADA — troque SERPAPI_KEY; este eval NÃO julga busca/juiz.")
    if m.get("destaque_indice"):
        L.append("  por tag índice: " + ", ".join(f"{t}: acerto={b.get('acerto')} grave={b.get('n_erro_grave')} n={b.get('n')}"
                                                 for t, b in m["destaque_indice"].items()))
    if _busca_indisponivel(m) >= 0.5:
        L.append("  ATENÇÃO: busca web pulada/falhou na maioria dos casos (sem chave ou cota SerpAPI esgotada) "
                 "— o eval NÃO julga mudanças de busca/juiz.")
    if delta is not None:
        if delta.get("n_comuns"):
            L.append(f"  vs baseline '{delta.get('baseline_nome')}' ({delta['n_comuns']} comuns): "
                     f"acerto {delta['acerto_pp']:+}pp, erro_grave {delta['n_erro_grave_delta']:+}, "
                     f"indeterminada {delta['taxa_indeterminada_pp']:+}pp, {len(delta['mudaram'])} mudaram")
        else:
            L.append("  vs baseline: sem casos em comum")
    return "\n".join(L)


def relatorio_md(meta: Dict[str, Any], m: Dict[str, Any], res: List[Dict[str, Any]],
                 delta: Optional[Dict[str, Any]]) -> str:
    L = [f"# Eval {meta['nome'] or meta['timestamp']}", "",
         f"- data: {meta['timestamp']} · git: `{meta['git']}` · modo: `{meta['modo']}` · split: `{meta['split']}`",
         f"- env: `{meta['env']}` · casos: {meta['arquivos_casos']} · filtros: tags={meta['filtro_tag']} ids={meta['ids']}",
         f"- SerpAPI: {m['serpapi']['live_no_eval']} busca(s) live neste eval ({_saldo(m['serpapi'])})",
         "", "## Métricas", "", "| métrica | valor |", "|---|---|"]
    for k in ("n", "n_nao_rodados", "n_nao_reproduzidos", "acerto", "acerto_com_parcial", "acuracia_balanceada",
              "cobertura", "auc_ordinal", "erro_grave", "n_erro_grave",
              "taxa_indeterminada", "llm_chamadas_por_caso", "latencia_p50_ms", "latencia_p95_ms",
              "n_erros_execucao", "http_miss_total", "llm_miss_total"):
        L.append(f"| {k} | {m.get(k)} |")
    L += ["", f"- níveis: `{m.get('niveis')}`", f"- descoberta web: `{m.get('descoberta')}`",
          f"- fallback (fração de casos) por onde: `{m.get('taxa_fallback_por_onde')}`",
          f"- descartes por motivo: `{m.get('descartes_por_motivo')}`",
          f"- grave por direção: `{m.get('erro_grave_por_direcao')}`",
          f"- precisão por nível: `{m.get('precisao_por_nivel')}`",
          f"- recall por rótulo: `{m.get('recall_por_rotulo')}`",
          f"- Wilson: `{m.get('intervalos_wilson')}`"]
    tri = m.get("baselines_triviais") or {}
    if tri:
        L += ["", "### Trivial (mesmos casos) + lift", "", "| preditor | acerto | balanceada | grave | grave FE->baixa | grave V->alta |",
              "|---|---|---|---|---|---|"]
        for nome, chave in (("sistema", None), ("sempre_alta", "sempre_alta"), ("sempre_media", "sempre_media")):
            b = m if chave is None else tri.get(chave, {})
            gd2 = b.get("erro_grave_por_direcao") or {}
            L.append(f"| {nome} | {b.get('acerto')} | {b.get('acuracia_balanceada')} | {b.get('n_erro_grave')} | "
                     f"{gd2.get('fe_k')}/{gd2.get('fe_n')} | {gd2.get('v_k')}/{gd2.get('v_n')} |")
    if _busca_indisponivel(m) >= 0.5:
        L.append("- **ATENÇÃO:** busca web pulada/falhou na maioria dos casos (sem SERPAPI_KEY, orçamento ou cota esgotada): "
                 "este eval não serve para julgar mudanças de busca/juiz.")
    L += ["", "### Por rótulo", "", "| rótulo | n | acerto | erro grave | níveis |", "|---|---|---|---|---|"]
    for rot, b in m.get("por_rotulo", {}).items():
        L.append(f"| {rot} | {b['n']} | {b['acerto']} | {b['n_erro_grave']} | {b['niveis']} |")
    L += ["", "### Por tag", "", "| tag | n | acerto | erro grave | indeterminada |", "|---|---|---|---|---|"]
    for t, b in m.get("por_tag", {}).items():
        L.append(f"| {t} | {b['n']} | {b['acerto']} | {b['n_erro_grave']} | {b['taxa_indeterminada']} |")
    if delta is not None:
        L += ["", "## Delta vs baseline", ""]
        if delta.get("n_comuns"):
            L.append(f"{delta['n_comuns']} casos em comum com `{delta.get('baseline_nome')}`: acerto "
                     f"{delta['acerto_pp']:+}pp, balanceada {delta.get('acuracia_balanceada_pp'):+}pp, erro grave {delta['n_erro_grave_delta']:+}, "
                     f"grave-dir FE{delta.get('grave_dir_delta', {}).get('fe', 0):+} V{delta.get('grave_dir_delta', {}).get('v', 0):+}, "
                     f"indeterminada {delta['taxa_indeterminada_pp']:+}pp, cobertura {delta.get('cobertura_pp'):+}pp.")
            for x in delta["mudaram"]:
                L.append(f"- `{x['id']}`: {x['antes']} → {x['agora']} (ok {x['ok_antes']} → {x['ok_agora']})")
        else:
            L.append("Sem casos em comum.")
    L += ["", "## Casos", "", "| | id | rótulo | nível | esperado | fallbacks | descoberta | run |",
          "|---|---|---|---|---|---|---|---|"]
    for r in res:
        if r.get("nao_rodado"):
            L.append(f"| – | {r['id']} | {r['rotulo']} | não rodado | | | | {r.get('motivo')} |")
            continue
        if r.get("nao_reproduzido"):
            L.append(f"| ⊘ | {r['id']} | {r['rotulo']} | {r['nivel']} | {'/'.join(r['esperado'])} | "
                     f"{r['n_fallbacks']} | {r['descoberta']} | `{r['run_id']}` |")
            continue
        marca = "✅" if r["ok"] else ("🟡" if r["parcial"] else ("🛑" if r["grave"] else "❌"))
        L.append(f"| {marca} | {r['id']} | {r['rotulo']} | {r['nivel']} | {'/'.join(r['esperado'])} | "
                 f"{r['n_fallbacks']} | {r['descoberta']} | `{r['run_id']}` |")
    errados = [r for r in res if not r.get("nao_rodado") and not r.get("nao_reproduzido") and not r["ok"]]
    if errados:
        L += ["", "## Para investigar (errados)", ""]
        for r in errados:
            L.append(f"- `{r['id']}` ({r['rotulo']} → {r['nivel']}{', GRAVE' if r['grave'] else ''}): "
                     f"`python3 -m factcheck_mvp.cli trace {r['run_id']}`"
                     + (f" — erro: {r['erro']}" if r.get("erro") else ""))
    L += ["", "Legenda: ✅ acerto · 🟡 parcial (ex. falso→media) · ❌ erro · 🛑 erro grave "
          "(falso/enganoso→baixa, verdadeiro→alta)."]
    return "\n".join(L) + "\n"


def salvar(dir_saida: Path, meta: Dict[str, Any], m: Dict[str, Any], res: List[Dict[str, Any]],
           delta: Optional[Dict[str, Any]]) -> None:
    dir_saida.mkdir(parents=True, exist_ok=True)
    (dir_saida / "metricas.json").write_text(json.dumps({"meta": meta, "metricas": m, "delta": delta},
                                                        ensure_ascii=False, indent=1), encoding="utf-8")
    with open(dir_saida / "casos.jsonl", "w", encoding="utf-8") as f:
        for r in res:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (dir_saida / "relatorio.md").write_text(relatorio_md(meta, m, res, delta), encoding="utf-8")


def salvar_baseline(meta: Dict[str, Any], m: Dict[str, Any], res: List[Dict[str, Any]],
                    caminho: Path = BASELINE) -> None:
    caminho.write_text(json.dumps({"meta": meta, "metricas": m,
                                   "casos": [{k: r.get(k) for k in ("id", "rotulo", "nivel", "ok", "parcial",
                                                                    "grave", "nao_rodado", "nao_reproduzido", "llm_miss", "run_id")}
                                             for r in res]}, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------
# Orquestração

def rodar(casos: List[Dict[str, Any]], *, modo: str = "replay", paralelo: int = 1, timeout: float = 300.0,
          env: Optional[Dict[str, str]] = None, max_buscas: Optional[int] = None, nome: str = "",
          split: str = "dev", filtro_tag: Optional[List[str]] = None, ids: Optional[List[str]] = None,
          arquivos_casos: Optional[List[str]] = None, pipeline_factory: Optional[Callable[[], Any]] = None,
          dir_resultados: Path = RESULTADOS, baseline: Path = BASELINE, isolar_catalogo: bool = True,
          progresso: Callable[[str], None] = print) -> Dict[str, Any]:
    """Executa o eval e grava os artefatos. Retorna {meta, metricas, casos, delta, dir}."""
    from factcheck_mvp.cli import catalogo_isolado, construir_pipeline

    env = dict(ENV_PADRAO if env is None else env)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    meta = {"timestamp": ts, "nome": nome, "modo": modo, "split": split, "filtro_tag": filtro_tag, "ids": ids,
            "env": env, "paralelo": paralelo, "timeout_caso_s": timeout, "max_buscas_serpapi": max_buscas,
            "arquivos_casos": sorted({c["_arquivo"] for c in casos if c.get("_arquivo")}) or arquivos_casos,
            "git": _git_sha(), "n_casos": len(casos)}
    uso_antes = replay.uso_serpapi()
    replay.definir_teto_serpapi(max_buscas)
    replay.resetar_cota()
    try:
        with overrides_env(env):
            if isolar_catalogo and pipeline_factory is None:
                with catalogo_isolado():
                    pipe = construir_pipeline()
                    res = asyncio.run(executar_casos(pipe, casos, modo, paralelo, timeout, max_buscas, progresso))
            else:
                pipe = (pipeline_factory or construir_pipeline)()
                res = asyncio.run(executar_casos(pipe, casos, modo, paralelo, timeout, max_buscas, progresso))
    finally:
        uso_depois = replay.uso_serpapi()
        replay.definir_teto_serpapi(None)
    m = calcular_metricas(res)
    m["serpapi"] = {"live_no_eval": uso_depois["usadas_processo"],
                    "usadas_total": uso_depois["usadas"], "restantes": uso_depois["restantes"],
                    "orcamento": uso_depois["orcamento"], "usadas_antes": uso_antes["usadas"]}
    cota = replay.cota_esgotada()
    m["cota_serpapi_esgotada"] = cota
    if cota:
        m["aviso"] = ("COTA SERPAPI ESGOTADA — troque SERPAPI_KEY no .env. "
                      "Casos sem busca não medem busca/juiz; este eval é inválido.")
    delta = None
    if Path(baseline).exists():
        try:
            delta = comparar(res, json.loads(Path(baseline).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            delta = None
    dir_saida = Path(dir_resultados) / (ts + (f"-{nome}" if nome else ""))
    salvar(dir_saida, meta, m, res, delta)
    return {"meta": meta, "metricas": m, "casos": res, "delta": delta, "dir": dir_saida,
            "cota_esgotada": cota, "codigo_saida": (2 if cota else 0)}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m eval.run", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modo", choices=list(replay.MODOS), default="replay",
                    help="replay (padrão: sem rede, cassetes) | record (grava faltantes) | live")
    ap.add_argument("--split", choices=["dev", "holdout", "todos"], default="dev")
    ap.add_argument("--casos", nargs="+", default=[CASOS_PADRAO], help="arquivos/globs JSONL")
    ap.add_argument("--filtro-tag", help="tags separadas por vírgula (qualquer uma)")
    ap.add_argument("--ids", help="ids separados por vírgula (ignora --split)")
    ap.add_argument("--env", action="append", metavar="CHAVE=VALOR",
                    help=f"override de config (repetível). Padrão: {ENV_PADRAO}")
    ap.add_argument("--nome", default="")
    ap.add_argument("--paralelo", type=int, default=1)
    ap.add_argument("--timeout-caso", type=float, default=300.0)
    ap.add_argument("--max-buscas-serpapi", type=int, default=None,
                    help="teto de buscas SerpAPI live neste eval (não inicia caso que poderia estourar)")
    ap.add_argument("--salvar-baseline", action="store_true", help="grava eval/baseline.json com este resultado")
    ap.add_argument("--gate", action="store_true", help="exit 1 se erro grave subir ou acerto cair > --gate-pp")
    ap.add_argument("--gate-pp", type=float, default=5.0)
    ap.add_argument("--catalogo-real", action="store_true", help="não isola catalogo.json (padrão: cópia temporária)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    import logging
    logging.basicConfig(level=logging.INFO if a.verbose else logging.ERROR, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    for nome in ("httpx", "httpcore"):
        logging.getLogger(nome).setLevel(logging.WARNING)

    casos = carregar_casos(a.casos)
    tags = [t.strip() for t in a.filtro_tag.split(",")] if a.filtro_tag else None
    ids = [i.strip() for i in a.ids.split(",")] if a.ids else None
    sel = filtrar(casos, a.split, tags, ids)
    if not sel:
        print("nenhum caso selecionado", file=sys.stderr)
        return 2
    env = parse_env(a.env)
    uso = replay.uso_serpapi()
    print(f"{len(sel)} caso(s) | modo={a.modo} | env={env} | SerpAPI {_saldo(uso)}"
          + (f" | teto neste eval {a.max_buscas_serpapi}" if a.max_buscas_serpapi is not None else ""))
    out = rodar(sel, modo=a.modo, paralelo=a.paralelo, timeout=a.timeout_caso, env=env,
                max_buscas=a.max_buscas_serpapi, nome=a.nome, split=a.split, filtro_tag=tags, ids=ids,
                arquivos_casos=a.casos, isolar_catalogo=not a.catalogo_real)
    print(resumo_texto(out["meta"], out["metricas"], out["delta"]))
    print(f"  artefatos: {out['dir']}/relatorio.md")
    if out.get("cota_esgotada"):
        print("COTA SERPAPI ESGOTADA — troque SERPAPI_KEY no .env; "
              "este eval NÃO julga mudanças de busca/juiz.", file=sys.stderr)
        return 2
    if a.salvar_baseline:
        salvar_baseline(out["meta"], out["metricas"], out["casos"])
        print(f"  baseline salvo em {BASELINE}")
    if a.gate and out["delta"] is not None:
        ok, motivos = avaliar_gate(out["delta"], a.gate_pp)
        print("  GATE: " + ("passou" if ok else "FALHOU — " + "; ".join(motivos)))
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
