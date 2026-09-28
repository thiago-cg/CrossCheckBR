"""A/B da descoberta SerpAPI: news (legado) vs google simples vs google avançada.

Roda os 3 modos sobre as MESMAS afirmações (só descoberta — sem LLM-juiz,
sem deep crawl: a comparação é honesta no nível da busca) e imprime métricas
+ top-5 de cada modo, salvando JSON em output_ab/.

Uso:
    export SERPAPI_KEY=...
    python3 scripts/ab_descoberta.py
    python3 scripts/ab_descoberta.py --modos avancada,simples --afirmacao "vacina causa autismo"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

SYS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SYS_ROOT))

from factcheck_mvp import serpapi_layer as camada  # noqa: E402

AFIRMACOES_PADRAO = [
    "bets são proibidas no brasil",
    "ibuprofeno cura dengue",
]

CHECAGEM_HOSTS = {s["host"] for s in camada.sites_checagem()}


def _tokens(s: str) -> set:
    return set(w for w in re.findall(r"[a-zà-ú0-9]{3,}", (s or "").lower()))


def _overlap(afirmacao: str, titulo: str) -> float:
    a, t = _tokens(afirmacao), _tokens(titulo)
    return (len(a & t) / max(1, len(a))) if a else 0.0


def _host(url: str) -> str:
    try:
        h = (urlparse(url or "").hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _eh_checagem(url: str) -> bool:
    return camada.eh_secao_checagem(url)


def rodar_modo(cli: camada.SerpAPIClient, afirmacao: str, modo: str) -> dict:
    if modo == "agente":
        from factcheck_mvp import agente as _ag
        antes = cli.uso_hoje
        docs, traco = _ag.descobrir([afirmacao], cli, None)
        for d in docs:
            d["_overlap"] = round(_overlap(afirmacao, d.get("titulo", "")), 3)
            d["_checagem"] = _eh_checagem(d.get("url", ""))
        docs.sort(key=lambda d: (d["_checagem"], d["_overlap"]), reverse=True)
        return {
            "modo": modo,
            "afirmacao": afirmacao,
            "chamadas": cli.uso_hoje - antes,
            "queries": [d for d in traco.get("decisoes", [])],
            "unicos": len(docs),
            "dominios": len({_host(d.get("url", "")) for d in docs}),
            "n_checagem": sum(1 for d in docs if d["_checagem"]),
            "overlap_medio_top5": round(sum(d["_overlap"] for d in docs[:5]) / max(1, min(5, len(docs))), 3),
            "top5": [{"titulo": d.get("titulo", "")[:100], "url": d.get("url", ""),
                      "checagem": d["_checagem"], "overlap": d["_overlap"],
                      "top_story": d.get("_top_story", False),
                      "snippet": bool(d.get("_snippet"))} for d in docs[:5]],
        }
    if modo == "news":
        queries = camada.construir_queries(afirmacao, engine="google_news")
    elif modo == "simples":
        queries = camada.construir_queries(afirmacao, engine="google", estrategia="simples")
    elif modo == "avancada":
        queries = camada.construir_queries(afirmacao, engine="google", estrategia="avancada")
    else:
        raise SystemExit(f"modo desconhecido: {modo} (use news|simples|avancada|agente)")
    antes = cli.uso_hoje
    unicos: dict = {}
    for q in queries:
        bruto = cli.buscar(q) or {}
        eng = cli.engine_de(q)
        itens = list((bruto.get(cli.result_key(q), []) or [])[:6])
        if eng == "google":
            for ts in (bruto.get("top_stories", []) or [])[:4]:
                itens.append({**ts, "_eh_top_story": True})
        for it in itens:
            n = camada.normalizar_item(it, engine=eng,
                                       top_story=bool(it.get("_eh_top_story")))
            if n.get("url") and n["url"] not in unicos:
                unicos[n["url"]] = n
    docs = list(unicos.values())
    for d in docs:
        d["_overlap"] = round(_overlap(afirmacao, d.get("titulo", "")), 3)
        d["_checagem"] = _eh_checagem(d.get("url", ""))
    docs.sort(key=lambda d: (d["_checagem"], d["_overlap"]), reverse=True)
    return {
        "modo": modo,
        "afirmacao": afirmacao,
        "chamadas": cli.uso_hoje - antes,
        "queries": [q["q"] for q in queries],
        "unicos": len(docs),
        "dominios": len({_host(d.get("url", "")) for d in docs}),
        "n_checagem": sum(1 for d in docs if d["_checagem"]),
        "overlap_medio_top5": round(sum(d["_overlap"] for d in docs[:5]) / max(1, min(5, len(docs))), 3),
        "top5": [{"titulo": d.get("titulo", "")[:100], "url": d.get("url", ""),
                  "checagem": d["_checagem"], "overlap": d["_overlap"],
                  "top_story": d.get("_top_story", False),
                  "snippet": bool(d.get("_snippet"))} for d in docs[:5]],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B da descoberta SerpAPI.")
    ap.add_argument("--modos", default="news,simples,avancada")
    ap.add_argument("--afirmacao", action="append", default=None)
    ap.add_argument("--saida", default=None)
    args = ap.parse_args()

    cli = camada.SerpAPIClient()
    if not cli.ativo:
        print("ERRO: SERPAPI_KEY ausente (export SERPAPI_KEY=...). Sem rede, sem A/B.", file=sys.stderr)
        return 2
    afirmacoes = args.afirmacao or AFIRMACOES_PADRAO
    modos = [m.strip() for m in args.modos.split(",") if m.strip()]
    rel = {"quando": datetime.now(timezone.utc).isoformat(), "resultados": []}
    for af in afirmacoes:
        for modo in modos:
            r = rodar_modo(cli, af, modo)
            rel["resultados"].append(r)
            print(f"\n== [{modo}] {af} ==")
            print(f"   chamadas={r['chamadas']} unicos={r['unicos']} "
                  f"dominios={r['dominios']} checagem={r['n_checagem']} "
                  f"overlap_top5={r['overlap_medio_top5']}")
            for t in r["top5"]:
                flag = "CHECAGEM " if t["checagem"] else ""
                print(f"   - [{flag}ov={t['overlap']}] {t['titulo'][:90]} | {t['url'][:80]}")
    print(f"\nchamadas totais na sessão: {cli.uso_hoje}")
    saida = Path(args.saida or f"output_ab/{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json")
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(rel, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"relatório: {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
