#!/usr/bin/env python3
"""Verifica a rastreabilidade dos documentos de docs/.

1. Links: toda âncora (no mesmo documento ou no outro) aponta para um título que existe.
2. Identificadores: todo RF, RNF, RN, HU, IND e P citado está definido; os definidos e nunca citados são listados.
3. Fonte única: os números do modelo e dos dados só aparecem na seção 4 dos requisitos.

    python3 docs/verificar_docs.py

Sai com código 1 se houver link quebrado, identificador sem definição ou número repetido.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
DOCS = ["ENGENHARIA_DE_REQUISITOS.md", "ENGENHARIA_DE_PRODUTO_DE_IA.md"]
# Valores que só podem aparecer na seção 4 dos requisitos.
# (0,839 fica de fora: é a revocação da classe desinformação e, por coincidência,
# também o acerto em textos de 1 a 15 palavras.)
NUMEROS_DA_SECAO_4 = ["0,816", "0,778", "0,870", "0,903", "0,630", "0,753", "0,890", "0,956",
                      "0,019", "0,575", "0,803", "0,740", "0,507", "13.661", "297.672", "91.080",
                      "66.772", "24.308"]


def ancora(titulo: str) -> str:
    """Âncora como o GitHub gera: minúsculas, sem pontuação, espaços viram hífens."""
    t = re.sub(r"[`*_]", "", titulo.strip().lower())
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    return t.replace(" ", "-")


def main() -> int:
    textos = {d: (AQUI / d).read_text(encoding="utf-8") for d in DOCS}
    ancoras = {d: {ancora(m.group(2)) for m in re.finditer(r"^(#{1,6})\s+(.+)$", t, re.M)} for d, t in textos.items()}
    erros = 0

    # 1. links
    n_links = 0
    for d, t in textos.items():
        for m in re.finditer(r"\]\(([^)\s]+)\)", t):
            alvo = m.group(1)
            if alvo.startswith(("http://", "https://")):
                continue
            n_links += 1
            arquivo, _, frag = alvo.partition("#")
            destino = arquivo or d
            if destino not in textos:
                print(f"LINK QUEBRADO em {d}: arquivo {destino} não existe"); erros += 1
            elif frag and frag not in ancoras[destino]:
                print(f"LINK QUEBRADO em {d}: #{frag} não existe em {destino}"); erros += 1
    print(f"links internos verificados: {n_links}")

    # 2. identificadores
    tudo = "\n".join(textos.values())
    definidos = set()
    definidos |= set(re.findall(r"^#{3,4}\s+(RN?F\d{2})\b", tudo, re.M))          # títulos RFxx e RNFxx
    definidos |= set(re.findall(r"^\|\s*(RN\d{2}|HU\d{2}|P\d{2})\s*\|", tudo, re.M))   # primeiras colunas
    definidos |= set(re.findall(r"(IND\d{2}) ·", tudo))                              # indicadores
    citados = set(re.findall(r"\b(RNF\d{2}|RF\d{2}|RN\d{2}|HU\d{2}|IND\d{2}|P\d{2})\b", tudo))
    for ident in sorted(citados - definidos):
        print(f"IDENTIFICADOR SEM DEFINIÇÃO: {ident}"); erros += 1
    por_tipo = {}
    for ident in definidos:
        por_tipo.setdefault(re.match(r"[A-Z]+", ident).group(0), []).append(ident)
    print("identificadores definidos:", ", ".join(f"{k} {len(v)}" for k, v in sorted(por_tipo.items())))
    # definidos e citados só na própria definição
    for ident in sorted(definidos):
        if len(re.findall(rf"\b{ident}\b", tudo)) < 2:
            print(f"aviso: {ident} é definido, mas nunca citado em outro ponto")

    # 3. fonte única dos números
    req = textos[DOCS[0]]
    i, j = req.index("\n## 4. "), req.index("\n## 5. ")
    fora = req[:i] + req[j:] + textos[DOCS[1]]
    for num in NUMEROS_DA_SECAO_4:
        if num in fora:
            print(f"NÚMERO REPETIDO fora da seção 4 dos requisitos: {num}"); erros += 1
        elif req[i:j].count(num) > 1:
            print(f"NÚMERO REPETIDO dentro da seção 4: {num} ({req[i:j].count(num)} vezes)"); erros += 1
    print("fonte única dos números: verificada" if not erros else f"{erros} problema(s)")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main())
