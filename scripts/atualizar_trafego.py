"""Gera factcheck_mvp/data/trafego_tranco.csv.gz a partir da lista Tranco.

A Tranco (https://tranco-list.eu) ranqueia os domínios mais acessados do mundo
combinando Chrome UX Report (acesso real de usuários do Chrome), Cloudflare
Radar, Farsight e Majestic, com média de 30 dias — padrão em pesquisa por ser
estável e difícil de manipular. Guardamos só o topo (padrão 200 mil) porque
`confiabilidade.py` só precisa saber se o site é muito acessado.

Uso:
    python3 scripts/atualizar_trafego.py                 # lista mais recente
    python3 scripts/atualizar_trafego.py --lista 647LX   # lista fixa (reprodutível)
    python3 scripts/atualizar_trafego.py --arquivo top-1m.csv --lista 647LX  # sem rede

Saídas: data/trafego_tranco.csv.gz (`dominio,posicao`) e data/trafego_tranco.json
(id da lista, data, corte) — cite o id da lista ao comparar resultados.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "factcheck_mvp" / "data"
API = "https://tranco-list.eu/api/lists/date/latest"
DOWNLOAD = "https://tranco-list.eu/download_daily/{lista}"


def _linhas_da_rede(lista: str | None) -> tuple[list[list[str]], str, str]:
    import curl_cffi.requests as _curl

    criada = ""
    if not lista:
        _m = _curl.get(API, timeout=30, impersonate="chrome")
        _m.raise_for_status()
        meta = _m.json()
        lista, criada = meta["list_id"], meta.get("created_on", "")[:10]
    r = _curl.get(DOWNLOAD.format(lista=lista), timeout=180, allow_redirects=True,
                  impersonate="chrome")
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        texto = z.read(z.namelist()[0]).decode("utf-8")
    return list(csv.reader(io.StringIO(texto))), lista, criada


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lista", help="id da lista Tranco (padrão: a mais recente)")
    ap.add_argument("--arquivo", help="top-1m.csv já baixado (dispensa a rede)")
    ap.add_argument("--corte", type=int, default=200_000, help="maior posição guardada (padrão 200000)")
    a = ap.parse_args(argv)

    if a.arquivo:
        linhas = list(csv.reader(open(a.arquivo, encoding="utf-8")))
        lista, criada = a.lista or "desconhecida", ""
    else:
        linhas, lista, criada = _linhas_da_rede(a.lista)

    topo = [(d.strip().lower(), int(p)) for p, d in linhas if p.isdigit() and int(p) <= a.corte]
    with gzip.open(DADOS / "trafego_tranco.csv.gz", "wt", encoding="utf-8", compresslevel=9) as f:
        for dominio, pos in topo:
            f.write(f"{dominio},{pos}\n")
    meta = {"fonte": "Tranco (https://tranco-list.eu)", "lista": lista, "criada_em": criada,
            "corte": a.corte, "dominios": len(topo),
            "citacao": f"https://tranco-list.eu/list/{lista}/1000000"}
    (DADOS / "trafego_tranco.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
    print(f"ok: {len(topo)} domínios (lista {lista}, corte {a.corte}) -> {DADOS / 'trafego_tranco.csv.gz'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
