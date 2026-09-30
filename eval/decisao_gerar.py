"""Backfill: validos.json -> snapshot JSONL (só para traces ANTIGOS, sem evento `evidencias`).

Runs novos já trazem `telemetria.evento("evidencias", **asdict(ev))`: aí gerar é só
copiar `e["dados"]` do primeiro evento tipo=evidencias. Este módulo existe para a
primeira geração; depois pode ser removido.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

AFIRM_RE = re.compile(r"\[(afirma|nega)\] (['\"])(.*?)\2 \(núcleo (['\"])(.*?)\4")
MOTOR_RE = re.compile(r"via\s+(\S+)")
TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")

RAIZ_RUNS: Path


def _bool(v: Any) -> bool:
    return v is True or v == "True"


def _ler_trace(run_id: str) -> List[Dict[str, Any]]:
    out = []
    assert RAIZ_RUNS is not None
    with open(RAIZ_RUNS / run_id / "trace.jsonl", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if linha:
                try:
                    out.append(json.loads(linha))
                except ValueError:
                    continue
    return out


def afirmacoes_do_trace(eventos: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    det = next((e["dados"].get("detalhe", "") for e in eventos
                if e.get("tipo") == "etapa" and e["dados"].get("nome") == "afirmacoes"), "")
    return [{"texto": m.group(3), "nucleo": m.group(5), "polaridade": m.group(1)}
            for m in AFIRM_RE.finditer(det or "")]


def evidencias_do_trace(eventos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for e in eventos:
        if e.get("tipo") == "evidencias":
            return dict(e.get("dados") or {})
    dec_ev = next((e for e in eventos if e.get("tipo") == "decisao"), None)
    if dec_ev is None:
        return None
    travas = (dec_ev["dados"].get("travas") or {})
    cont = (travas.get("contagem") or {})
    det_juiz = next((e["dados"].get("detalhe", "") for e in eventos
                     if e.get("tipo") == "etapa" and e["dados"].get("nome") == "juiz"), "")
    m = MOTOR_RE.search(det_juiz or "")
    motor_llm = m.group(1).rstrip(":.") if m else "llm-juiz:openrouter"
    rel = {f["dados"].get("url"): f["dados"] for f in eventos
           if f.get("tipo") == "fonte" and f["dados"].get("estagio") == "relatorio"}
    vistos: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for e in eventos:
        if not (e.get("tipo") == "fonte" and e["dados"].get("estagio") == "juiz"):
            continue
        f = e["dados"]
        cl = f.get("classe")
        ver = f.get("veredito")
        ver = None if ver in (None, "None", "null") else ver
        vistos[(f.get("url"), int(f.get("afirmacao") or 0))] = {
            "url": f.get("url"), "afirmacao": int(f.get("afirmacao") or 0),
            "cluster": (rel.get(f.get("url")) or {}).get("cluster")
                       or f.get("cluster") or f.get("url"),
            "classe": cl,
            "motor": motor_llm if cl is not None else "fallback-sem-juiz",
            "citacao_verificada": (False if f.get("decisao") == "rebaixada"
                                   else (True if cl in TRATA else None)),
            "curada": _bool(f.get("curada")), "corpo_lido": _bool(f.get("corpo_lido")),
            "veredito": ver, "origem_veredito": ("pagina" if ver else None), "veiculo": "",
        }
    return {"afirmacoes": afirmacoes_do_trace(eventos),
            "itens": list(vistos.values()),
            "vago": bool(travas.get("vago", False)),
            "opiniao": bool(travas.get("opiniao", False)),
            "rumor": bool(travas.get("rumor", False)),
            "juiz_disponivel": bool(travas.get("juiz_disponivel", True)),
            "n_lidas": int(cont.get("lidas") or 0),
            "n_consultadas": int(cont.get("consultadas") or 0)}


def gerar_snapshot(validos: Path, casos_globs: List[str], saida: Path) -> Tuple[int, int]:
    import glob as _glob
    global RAIZ_RUNS
    RAIZ = Path(__file__).resolve().parent.parent
    RAIZ_RUNS = RAIZ / "runs"
    val = json.loads(Path(validos).read_text(encoding="utf-8"))
    rot_esp: Dict[str, Dict[str, Any]] = {}
    for pat in casos_globs:
        for arq in (sorted(_glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]):
            try:
                fh = open(arq, encoding="utf-8")
            except OSError:
                continue
            with fh:
                for linha in fh:
                    linha = linha.strip()
                    if not linha or linha.startswith("#"):
                        continue
                    c = json.loads(linha)
                    if c["id"] not in rot_esp:
                        from eval.run import esperado_de
                        esp, ac = esperado_de(c)
                        rot_esp[c["id"]] = {"rotulo": c["rotulo"], "esperado": esp,
                                            "aceitavel": ac, "tags": c.get("tags") or []}
    n = pul = 0
    saida.parent.mkdir(parents=True, exist_ok=True)
    with open(saida, "w", encoding="utf-8") as out:
        for cid in sorted(val):
            v = val[cid]
            meta = rot_esp.get(cid, {"rotulo": v.get("rot"),
                                     "esperado": v.get("esp") or [],
                                     "aceitavel": [], "tags": []})
            try:
                evs = _ler_trace(v["run"])
            except OSError:
                pul += 1
                continue
            evd = evidencias_do_trace(evs)
            if evd is None:
                pul += 1
                continue
            out.write(json.dumps({"id": cid, "rotulo": meta["rotulo"],
                                  "esperado": meta["esperado"], "aceitavel": meta["aceitavel"],
                                  "tags": meta["tags"], "run_id": v["run"],
                                  "evidencias": evd}, ensure_ascii=False) + "\n")
            n += 1
    return n, pul
