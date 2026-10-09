"""Eval nível 0: reexecuta só decidir() a partir de um snapshot JSONL.

  Gerar (backfill a partir dos traces atuais):
    python3 -m eval.decisao --gerar-snapshot \
      --validos docs/review/review2/sondas/validos.json \
      --saida eval/snapshots/a3-dev.jsonl

  Avaliar (sem rede, determinístico):
    python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl
    python3 -m eval.decisao --snapshot eval/snapshots/a3-dev.jsonl \
      --validos docs/review/review2/sondas/validos.json   # confere 69/69 vs trace

Formato do snapshot (1 linha por caso):
  {id, rotulo, esperado, aceitavel, run_id,
   evidencias: {afirmacoes: [{texto, nucleo, polaridade}],
                itens: [{url, afirmacao, cluster, classe, motor,
                         citacao_verificada, curada, corpo_lido,
                         veredito, origem_veredito, veiculo}],
                vago, opiniao, rumor, juiz_disponivel, n_lidas, n_consultadas}}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from factcheck_mvp import decisao  # noqa: E402
from eval.run import calcular_metricas, eh_grave  # noqa: E402


def evidencias_de_dict(d: Dict[str, Any]) -> decisao.Evidencias:
    afs = [decisao.AfirmacaoDecisao(
        texto=a.get("texto", ""),
        nucleo=a.get("nucleo", ""),
        polaridade=a.get("polaridade", "afirma"),
    ) for a in (d.get("afirmacoes") or [])]
    itens = [decisao.ItemEvidencia(
        url=i.get("url", ""),
        afirmacao=int(i.get("afirmacao") or 0),
        cluster=i.get("cluster") or i.get("url", ""),
        classe=i.get("classe"),
        motor=i.get("motor") or "",
        citacao_verificada=i.get("citacao_verificada"),
        curada=bool(i.get("curada")),
        corpo_lido=bool(i.get("corpo_lido")),
        veredito=i.get("veredito"),
        origem_veredito=i.get("origem_veredito"),
        veiculo=i.get("veiculo") or "",
        data_pub=i.get("data_pub"),
    ) for i in (d.get("itens") or [])]
    return decisao.Evidencias(
        afirmacoes=afs, itens=itens,
        vago=bool(d.get("vago", False)),
        opiniao=bool(d.get("opiniao", False)),
        rumor=bool(d.get("rumor", False)),
        juiz_disponivel=bool(d.get("juiz_disponivel", True)),
        n_lidas=int(d.get("n_lidas") or 0),
        n_consultadas=int(d.get("n_consultadas") or 0),
        texto_usuario=d.get("texto_usuario") or "",
        data_referencia=d.get("data_referencia"),
    )


def carregar_snapshot(caminho: Path) -> List[Dict[str, Any]]:
    linhas = []
    with open(caminho, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                c = json.loads(raw)
            except ValueError as e:
                raise ValueError(f"{caminho}:{n}: JSON inválido: {e}")
            for campo in ("id", "rotulo", "esperado", "evidencias"):
                if campo not in c:
                    raise ValueError(f"{caminho}:{n}: sem campo {campo!r} (id={c.get('id')})")
            c.setdefault("aceitavel", [])
            linhas.append(c)
    return linhas


def avaliar_snapshot(linhas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    res = []
    for c in linhas:
        esp, ac = list(c.get("esperado") or []), list(c.get("aceitavel") or [])
        ev = evidencias_de_dict(c["evidencias"])
        d = decisao.decidir(ev)
        nivel = d.nivel
        ok = nivel in esp
        r = {"id": c["id"], "rotulo": c["rotulo"], "tags": c.get("tags") or [],
             "esperado": esp, "aceitavel": ac, "run_id": c.get("run_id"),
             "nivel": nivel, "log_odds": d.log_odds, "prob": d.prob, "motivo": d.motivo,
             "ok": ok, "parcial": (not ok) and nivel in ac,
             "grave": eh_grave(c["rotulo"], nivel),
             "n_afirmacoes": len(ev.afirmacoes), "n_itens": len(ev.itens),
             "n_llm": 0, "n_fallbacks": 0, "fallbacks_por_onde": {},
             "descartes_por_motivo": {}, "descoberta": "snapshot", "dur_ms": 0,
             "serpapi_live": 0, "http_miss": 0}
        res.append(r)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m eval.decisao", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", help="snapshot JSONL para avaliar")
    ap.add_argument("--gerar-snapshot", action="store_true", help="gera snapshot a partir de validos.json")
    ap.add_argument("--validos", default="docs/review/review2/sondas/validos.json")
    ap.add_argument("--casos", nargs="+", default=["eval/casos.jsonl", "eval/casos_claimreview.jsonl"])
    ap.add_argument("--saida", help="arquivo JSONL de saída de --gerar-snapshot")
    a = ap.parse_args(argv)

    if a.gerar_snapshot:
        from eval.decisao_gerar import gerar_snapshot
        if not a.saida:
            ap.error("--gerar-snapshot exige --saida")
        n, pulados = gerar_snapshot(Path(a.validos), [str(c) for c in a.casos], Path(a.saida))
        print(f"snapshot: {n} linha(s) em {a.saida} ({pulados} pulado(s) sem evento decisao, ex. oreo timeout)")
        return 0

    if not a.snapshot:
        ap.error("informe --snapshot ou --gerar-snapshot")
    linhas = carregar_snapshot(Path(a.snapshot))
    res = avaliar_snapshot(linhas)
    m = calcular_metricas(res)
    print(f"DECISAO {a.snapshot} | n={m.get('n')} "
          f"acerto={m.get('acerto')} acerto+parcial={m.get('acerto_com_parcial')} "
          f"erro_grave={m.get('erro_grave')} ({m.get('n_erro_grave')}) "
          f"indeterminada={m.get('taxa_indeterminada')} niveis={m.get('niveis')}")
    for r in res:
        if not r["ok"] and not r["parcial"]:
            print(f"  x  {r['id']:<42} rot={r['rotulo']:<13} nivel={r['nivel']:<13} "
                  f"esperado={'/'.join(r['esperado'])} L={r['log_odds']:+.2f} run={r['run_id']}"
                  + (" GRAVE" if r["grave"] else ""))
    if a.validos and Path(a.validos).exists():
        val = json.loads(Path(a.validos).read_text(encoding="utf-8"))
        ok = bad = 0
        for r in res:
            v = val.get(r["id"])
            if not v or not v.get("run") or v.get("nivel") is None:
                continue
            if r["nivel"] == v["nivel"]:
                ok += 1
            else:
                bad += 1
                print(f"  DIVERGE trace {r['id']}: snapshot={r['nivel']} trace={v['nivel']}")
        print(f"  reproduz {ok}/{ok + bad} vs validos.json (oreo timeout fora)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
