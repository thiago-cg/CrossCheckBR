"""Roda o pipeline inteiro no conjunto-ouro e mede o sistema (não só o modelo).

Leitura da propensão (o bot nunca dá binário):
  alta  → aponta desinformação      baixa → aponta confiável
  media → sinal misto               indeterminada → abstenção

Métricas:
  cobertura      — % de alegações em que o bot NÃO se absteve
  acerto_decidido — entre alta/baixa: alta p/ falso|enganoso, baixa p/ verdadeiro
  erro_grave     — baixa numa alegação falsa, ou alta numa verdadeira
  achou_checagem — a URL da agência apareceu entre as fontes do relatório

Uso:
    python avaliacao/avaliar_golden.py --offline --n 50     # sem LLM/SerpAPI (grátis)
    python avaliacao/avaliar_golden.py --n 30               # com as chaves do .env (custa)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

from factcheck_mvp.schemas import EntradaConsulta  # noqa: E402


def _dominio_path(url: str) -> str:
    from urllib.parse import urlparse
    u = urlparse(str(url or ""))
    return (u.netloc.lower().removeprefix("www.") + u.path.rstrip("/")).lower()


def avaliar_linha(prop: str, classe: str) -> dict:
    decidiu = prop in ("alta", "baixa")
    certo = (prop == "alta" and classe in ("falso", "enganoso")) or \
            (prop == "baixa" and classe == "verdadeiro")
    grave = (prop == "baixa" and classe == "falso") or (prop == "alta" and classe == "verdadeiro")
    return {"decidiu": decidiu, "certo": decidiu and certo, "erro_grave": grave}


def resumir(res: pd.DataFrame) -> dict:
    n = len(res)
    dec = res[res["decidiu"]]
    por_classe = {}
    for cl, g in res.groupby("classe"):
        por_classe[cl] = {"n": int(len(g)),
                          "propensao": {k: int(v) for k, v in Counter(g["propensao"]).items()},
                          "achou_checagem": round(float(g["achou_checagem"].mean()), 3)}
    return {
        "n": n,
        "cobertura": round(len(dec) / n, 3) if n else 0.0,
        "acerto_decidido": round(float(dec["certo"].mean()), 3) if len(dec) else None,
        "erro_grave": round(float(res["erro_grave"].mean()), 3) if n else 0.0,
        "achou_checagem": round(float(res["achou_checagem"].mean()), 3) if n else 0.0,
        "tempo_medio_s": round(float(res["segundos"].mean()), 2) if n else 0.0,
        "por_classe": por_classe,
    }


async def rodar(pipe, linhas: pd.DataFrame, usar_llm: bool) -> pd.DataFrame:
    saida = []
    for i, r in enumerate(linhas.itertuples(index=False), 1):
        t0 = time.time()
        texto = str(r.alegacao)
        tipo = "titulo" if len(texto) <= 140 and "\n" not in texto else "texto"
        try:
            rel = await pipe.executar(EntradaConsulta(tipo=tipo, conteudo=texto), usar_llm=usar_llm)
            prop = rel.propensao
            urls = {_dominio_path(f.url) for f in rel.fontes}
            erro = ""
        except Exception as e:  # executar não deveria levantar; registra se levantar
            prop, urls, erro = "erro", set(), str(e)[:200]
        alvo = _dominio_path(r.factcheck_url)
        linha = {"rid": r.rid, "alegacao": texto[:200], "classe": r.classe,
                 "propensao": prop, "achou_checagem": alvo in urls,
                 "segundos": round(time.time() - t0, 2), "erro": erro,
                 **avaliar_linha(prop, r.classe)}
        saida.append(linha)
        print(f"[{i}/{len(linhas)}] {r.classe:>10} → {prop:<13} {texto[:70]}", flush=True)
    return pd.DataFrame(saida)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(AQUI / "golden_set.csv"))
    ap.add_argument("--n", type=int, default=0, help="0 = todas")
    ap.add_argument("--offline", action="store_true",
                    help="sem LLM e sem SerpAPI (só índice local + regras)")
    ap.add_argument("--out", default=str(AQUI / "resultados"))
    args = ap.parse_args()

    g = pd.read_csv(args.golden)
    if args.n:
        # amostra estratificada: mantém a proporção das classes
        g = g.groupby("classe").sample(frac=min(1.0, args.n / len(g)), random_state=42)
    from factcheck_mvp import api
    pipe = api.pipeline()
    if args.offline:
        from factcheck_mvp.serpapi_layer import SerpAPIClient
        pipe.serpapi = SerpAPIClient(api_key="")

    res = asyncio.run(rodar(pipe, g, usar_llm=not args.offline))
    resumo = resumir(res)
    resumo["modo"] = "offline" if args.offline else "online"
    resumo["detector"] = getattr(pipe.detector, "nome", "?")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    carimbo = time.strftime("%Y%m%d-%H%M%S")
    res.to_csv(out / f"golden_{resumo['modo']}_{carimbo}.csv", index=False)
    (out / f"golden_{resumo['modo']}_{carimbo}.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
