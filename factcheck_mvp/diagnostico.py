"""Diagnóstico da decisão a partir do trace (complementa `telemetria.resumir_run`).

    python3 -m factcheck_mvp.diagnostico <run_id|last>

Mostra, por afirmação, cada fonte julgada (classe, cluster, curada, corpo lido, selo,
citação) e os votos/log-odds da `Decisao` — o suficiente para dizer se o erro está na
extração da afirmação, na busca, na leitura, no juiz ou na decisão.
"""
from __future__ import annotations

import sys
from typing import List

from . import telemetria


def resumir_juiz(run_id: str = "last") -> str:
    r = telemetria.ler_run(run_id)
    ev = r["eventos"]
    linhas: List[str] = [f"DIAGNÓSTICO {r['run_id']}"]
    for e in ev:
        if e.get("tipo") == "etapa" and e["dados"].get("nome") in ("afirmacoes", "descoberta-agente", "juiz",
                                                                     "deep-crawl", "agregacao"):
            linhas.append(f"  [{e['dados'].get('nome')}] {telemetria.truncar(str(e['dados'].get('detalhe')), 400)}")
    fontes = [e["dados"] for e in ev if e.get("tipo") == "fonte" and e["dados"].get("estagio") == "juiz"]
    if fontes:
        linhas.append(f"JUIZ ({len(fontes)} julgamentos)")
        for d in fontes:
            linhas.append(f"  af{d.get('afirmacao')} {str(d.get('classe')):<18} {d.get('decisao'):<11} "
                          f"cluster={d.get('cluster')} curada={d.get('curada')} corpo={d.get('corpo_lido')} "
                          f"selo={d.get('veredito')} {telemetria.truncar(str(d.get('url')), 90)}")
            linhas.append(f"      {telemetria.truncar(str(d.get('motivo')), 260)}")
    dec = next((e["dados"] for e in reversed(ev) if e.get("tipo") == "decisao"), None)
    if dec:
        t = dec.get("travas") or {}
        linhas.append(f"DECISÃO nivel={dec.get('nivel')} L={t.get('L')} p={t.get('p')} — {t.get('motivo')}")
        for v in t.get("votos") or []:
            linhas.append(f"  voto {v}")
        for v in t.get("vereditos_ignorados") or []:
            linhas.append(f"  selo ignorado {v}")
        linhas.append(f"  contagem {t.get('contagem')}")
    return "\n".join(linhas)


if __name__ == "__main__":  # pragma: no cover
    print(resumir_juiz(sys.argv[1] if len(sys.argv) > 1 else "last"))
