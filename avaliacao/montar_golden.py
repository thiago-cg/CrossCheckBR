"""Monta o conjunto-ouro: alegações que agências de checagem brasileiras já
verificaram, com o veredito conhecido — a "prova" do sistema inteiro.

Fonte: FakenewsBR_factchecked.csv (Release `modelo-v6-R0` do FakenewsBR),
linhas com `factcheck_rating` + `factcheck_url`.

Uso:
    python avaliacao/montar_golden.py --csv /caminho/FakenewsBR_factchecked.csv \
        --n-falso 100 --n-enganoso 50 --n-verdadeiro 50
"""
from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

import pandas as pd

AQUI = Path(__file__).resolve().parent

# Rating textual das agências → classe esperada da checagem.
FALSO = {"falso", "errado", "fake", "insustentavel", "sem registro", "boato", "mentira"}
ENGANOSO = {"enganoso", "enganador", "distorcido", "sem contexto", "exagerado",
            "impreciso", "esticado", "nao_e_bem_assim", "descontextualizado",
            "verdadeiro, mas", "subestimado", "contraditorio"}
VERDADEIRO = {"verdadeiro", "certo", "praticamente certo", "fato", "correto"}
# Portal pt-PT: o bot mira o público brasileiro.
DOMINIOS_FORA = ("observador.pt", "poligrafo.sapo.pt", "publico.pt")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().strip().split())


def classe(rating: str) -> str | None:
    r = _norm(rating)
    if r in FALSO:
        return "falso"
    if r in ENGANOSO:
        return "enganoso"
    if r in VERDADEIRO:
        return "verdadeiro"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="FakenewsBR_factchecked.csv")
    ap.add_argument("--out", default=str(AQUI / "golden_set.csv"))
    ap.add_argument("--n-falso", type=int, default=100)
    ap.add_argument("--n-enganoso", type=int, default=50)
    ap.add_argument("--n-verdadeiro", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    d = pd.read_csv(args.csv, low_memory=False)
    d = d[d["factcheck_rating"].notna() & d["factcheck_url"].notna()].copy()
    d = d[~d["factcheck_url"].str.contains("|".join(DOMINIOS_FORA), case=False, na=False)]
    d["classe"] = d["factcheck_rating"].map(classe)
    d = d[d["classe"].notna()]
    d["alegacao"] = d["text"].astype(str).str.strip()
    d = d[d["alegacao"].str.len().between(20, 600)]
    d = d.drop_duplicates(subset=["alegacao"])

    partes = []
    for cl, n in (("falso", args.n_falso), ("enganoso", args.n_enganoso),
                  ("verdadeiro", args.n_verdadeiro)):
        g = d[d["classe"] == cl]
        partes.append(g.sample(min(n, len(g)), random_state=args.seed))
        print(f"{cl}: {min(n, len(g))} de {len(g)} disponíveis")
    out = pd.concat(partes).sample(frac=1, random_state=args.seed)
    cols = ["rid", "alegacao", "classe", "factcheck_rating", "factcheck_url",
            "date_iso", "dataset_name"]
    out[cols].to_csv(args.out, index=False)
    print(f"golden: {len(out)} linhas → {args.out}")


if __name__ == "__main__":
    main()
