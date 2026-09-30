"""Mini-benchmark reproduzível do `Indice.buscar_checagens` (sem rede, sem LLM).

Uso: python3 scripts/bench_indice_checagens.py [--grade] [--verbose]

Conjuntos (todos sobre data/checagens.jsonl atual):
* POSITIVOS  = casos `no_indice` de eval/casos_claimreview.jsonl (paráfrases escritas à
  mão; alvo = `urls_checagem`). Mede recall@1 e recall@3.
* NEGATIVOS  = casos `fora_do_indice` (a checagem e o cluster foram excluídos do índice:
  qualquer retorno é falso positivo) + FORA_DO_TEMA abaixo (inclui negativos difíceis
  que compartilham entidades como Lula/Flávio/Moraes). Mede a taxa de falso positivo
  (consultas com ≥1 retorno).

Escolha do limiar (`indice._LIMIAR`): a grade (--grade) varia fator_mediana,
fracao_top1, min_termos e cobertura_min; o padrão escolhido é o de menor FP com
recall@3 máximo — preferimos perder um atalho (a busca aberta cobre) a devolver
checagem errada como "caminho feliz".
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from factcheck_mvp import indice as mod  # noqa: E402
from factcheck_mvp.indice import Indice  # noqa: E402
from factcheck_mvp.ingestor import chave_url  # noqa: E402

FORA_DO_TEMA = [
    "ibuprofeno piora a dengue", "café cura câncer", "vacina da covid deixa mulheres estéreis",
    "receita de bolo de cenoura com cobertura", "o dólar vai chegar a 10 reais em dezembro",
    "Neymar vai voltar pro Santos em 2027", "beber água com limão em jejum emagrece",
    "a Terra é plana e a NASA esconde", "o Pix vai ser taxado a partir de janeiro",
    "5G causa câncer e matou pássaros", "prefeitura de Curitiba vai cobrar pedágio urbano",
    "Lula inaugurou hospital no Piauí ontem", "Bolsonaro recebeu alta do hospital hoje",
    "Flávio Bolsonaro lidera pesquisa em São Paulo", "Moraes mandou prender o Elon Musk",
    "Galípolo vai baixar os juros para 5%", "o STF liberou o porte de maconha",
    "Lula vai visitar a China em novembro", "Vorcaro fechou delação premiada com a PF",
    "vacina da gripe causa Alzheimer", "Anitta anunciou que vai se aposentar",
]


def _casos():
    arq = RAIZ / "eval" / "casos_claimreview.jsonl"
    linhas = [l for l in arq.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    return [json.loads(l) for l in linhas]


def avaliar(idx: Indice, casos, limiar=None, verbose=False):
    pos = [c for c in casos if "no_indice" in c["tags"]]
    neg = [(c["id"], c["entrada"]["conteudo"]) for c in casos if "fora_do_indice" in c["tags"]]
    neg += [(f"tema:{i}", q) for i, q in enumerate(FORA_DO_TEMA)]
    r1 = r3 = 0
    for c in pos:
        alvos = {chave_url(u) for u in c["urls_checagem"]}
        hits = idx.buscar_checagens(c["entrada"]["conteudo"], k=3, limiar=limiar)
        chaves = [chave_url(h["url"]) for h in hits]
        r1 += bool(chaves[:1] and chaves[0] in alvos)
        r3 += bool(alvos & set(chaves[:3]))
        if verbose and not (alvos & set(chaves[:3])):
            print(f"  MISS {c['id']}: {[h['afirmacao_checada'][:60] for h in hits]}")
    fp = 0
    for cid, q in neg:
        hits = idx.buscar_checagens(q, k=3, limiar=limiar)
        if hits:
            fp += 1
            if verbose:
                print(f"  FP {cid}: '{q[:50]}' -> {[(h['agencia'], h['afirmacao_checada'][:60], h['score'], h['score_rel']) for h in hits]}")
    return {"n_pos": len(pos), "recall@1": r1 / max(1, len(pos)), "recall@3": r3 / max(1, len(pos)),
            "n_neg": len(neg), "fp": fp, "taxa_fp": fp / max(1, len(neg))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    idx = Indice.de_checagens()
    casos = _casos()
    print(f"checagens no índice: {len(idx.checagens)} | stemming RSLP: {mod.radicalizacao_ativa()}")
    res = avaliar(idx, casos, verbose=a.verbose)
    print(f"limiar padrão {mod._LIMIAR}\n  -> {res}")
    base = avaliar(idx, casos, limiar={"piso_abs": 0.0, "fator_mediana": 0.0, "fracao_top1": 0.0,
                                       "min_termos": 0, "cobertura_min": 0.0})
    print(f"sem limiar (só score>0)\n  -> {base}")
    if a.grade:
        linhas = []
        for fm, ft, mt, cm in itertools.product([1.5, 2.0, 3.0], [0.0, 0.5], [2, 3], [0.4, 0.5, 0.55, 0.6, 0.65, 0.7]):
            lim = {"fator_mediana": fm, "fracao_top1": ft, "min_termos": mt, "cobertura_min": cm}
            r = avaliar(idx, casos, limiar=lim)
            linhas.append((r["recall@3"], -r["taxa_fp"], r["recall@1"], lim, r))
        for rec3, nfp, rec1, lim, r in sorted(linhas, key=lambda x: (x[1], x[0], x[2]), reverse=True)[:12]:
            print(f"  {lim} -> r@1={r['recall@1']:.2f} r@3={r['recall@3']:.2f} fp={r['taxa_fp']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
