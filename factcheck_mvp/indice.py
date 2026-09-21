"""Índice BM25 de vereditos + notícias (rank_bm25, sem dependências pesadas).

MVP: busca lexical com normalização PT. Troca futura por híbrido
BM25+vetorial sem mudar a interface `buscar()`."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List

try:
    from rank_bm25 import BM25Plus

    BM25_OK = True
except Exception:  # pragma: no cover
    import logging as _logging

    _logging.getLogger("factcheck.indice").warning("rank_bm25 ausente: buscas retornam vazio.")
    BM25Plus = None  # type: ignore
    BM25_OK = False

_STOP = frozenset(
    "a o os as um uma de do da dos das em no na nos nas por para com sem que se e ou mas como mais foi eram será são é esta este esta isto isso esse essa aquele aquela muito tão já ainda também pode podem tem têm há foi vão ser ter".split()
)


def tokenizar(texto: str) -> List[str]:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    toks = re.findall(r"[a-z0-9]{3,}", texto.lower())
    return [t for t in toks if t not in _STOP]


class Indice:
    """Corpus de ArtigosNoticia consultável por texto. RF04/RF05."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []
        self._bm25 = None
        self._matriz: List[List[str]] = []

    def adicionar(self, doc: Dict[str, Any]) -> None:
        self.docs.append(doc)
        self._bm25 = None  # invalida; reconstrói sob demanda

    def _garantir(self) -> None:
        if self._bm25 is not None or not self.docs:
            return
        self._matriz = [tokenizar(f"{d.get('titulo','')} {d.get('corpo_texto','') or ''}") for d in self.docs]
        if BM25_OK:
            # BM25Plus (não Okapi): evita IDF zerado em corpus pequenos —
            # no Okapi, termo em metade dos docs zera o score.
            self._bm25 = BM25Plus(self._matriz)

    def __len__(self) -> int:
        return len(self.docs)

    def buscar(self, consulta: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Retorna [{doc, score}] ordenado. Score BM25 bruto >= 0."""
        self._garantir()
        if not self.docs or not BM25_OK or self._bm25 is None:
            return []
        toks = tokenizar(consulta)
        if not toks:
            return []
        scores = self._bm25.get_scores(toks)
        ordem = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        saida = []
        for i in ordem[:top_k]:
            if scores[i] <= 0:
                continue
            saida.append({"doc": self.docs[i], "score": float(scores[i])})
        return saida

    @classmethod
    def de_artigos(cls, artigos: List[Dict[str, Any]]) -> "Indice":
        idx = cls()
        for a in artigos:
            idx.adicionar(a)
        return idx
