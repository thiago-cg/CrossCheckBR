"""Testes de implicação via LLM-juiz: o texto SUSTENTA ou REFUTA?

Fluxo (juiz_llm): 1 LLM resume a peça frente à afirmação + 1 juiz em lote dá
o termômetro (-100..+100). Jev NÃO é usado aqui — ficou restrito à
classificação de schema em descoberta_site. Sem chave/falha: fallback lexical
honesto (nunca 0/1, teto < 0.6).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from . import config
from . import juiz_llm

log = logging.getLogger("factcheck.implicacao")


def _fallback_lexico(fonte: str, afirmacao: str) -> Dict[str, Any]:
    """Fallback honesto sem LLM: overlap lexical, nunca 0/1."""
    import re as _re
    import unicodedata as _ud

    def _toks(s: str):
        s = _ud.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
        toks = set(_re.findall(r"[a-z0-9]{4,}", s))
        stop = {"para", "como", "mais", "muito", "sobre", "entre", "quando",
                "foram", "serao", "sera", "pode", "podem", "isso", "esta", "este"}
        return {t for t in toks if t not in stop}

    tf, ta = _toks(fonte), _toks(afirmacao)
    if not tf or not ta:
        return {"sustenta": 0.0, "refuta": 0.0, "relevante": False, "erro": "fallback-lexico vazio"}
    inter = len(tf & ta) / max(1, len(ta))
    if inter < 0.34:  # loop 2: barra matches vagos (ex gravidez vs bets)
        return {"sustenta": 0.0, "refuta": 0.0, "relevante": False,
                "erro": "fallback-lexico overlap baixo"}
    neg = bool(_re.search(r"\b(falso|falsa|fake|enganoso|mentira|desmente|nega|boato|golpe)\b",
                          fonte or "", _re.I))
    # Loop 3/4: lexical NUNCA crava veredito >=0.6 (evita veredito fantasma
    # sem LLM): sus teto 0.5, ref teto 0.55 — nenhum cruza a barreira 0.6.
    sus = round(min(0.5, 0.15 + 0.5 * inter), 4)
    ref = round(min(0.55, 0.5 * inter + 0.2), 4) if neg else 0.0  # inter>=0.34 garantido
    relevante = max(sus, ref) >= config.RELEVANCIA_MIN
    return {"sustenta": sus, "refuta": ref, "relevante": relevante,
            "erro": None, "motor": "lexico-fallback"}


def implicacao(texto_fonte: str, afirmacao: str) -> Dict[str, Any]:
    """Retorna {sustenta, refuta, relevante, resumo, score, erro, motor}.

    1 resumo LLM + juiz-termômetro (lote de 1). Nunca levanta exceção.
    """
    saida: Dict[str, Any] = {"sustenta": 0.0, "refuta": 0.0, "relevante": False,
                             "resumo": "", "score": None, "erro": None}
    afirmacao = (afirmacao or "").strip()[:500]
    fonte = (texto_fonte or "").strip()[:2000]
    if len(afirmacao) < 10 or len(fonte) < 30:
        saida["erro"] = "texto insuficiente"
        return saida
    r = juiz_llm.resumir(fonte, afirmacao)
    saida["resumo"] = r.get("resumo", "")
    if r.get("erro") and not r.get("resumo"):
        fb = _fallback_lexico(fonte, afirmacao)
        saida.update(sustenta=fb["sustenta"], refuta=fb["refuta"],
                     relevante=fb["relevante"], score=None, motor=fb.get("motor"))
        saida["erro"] = (r.get("erro") or "") + " | fallback-lexico"
        return saida
    t = juiz_llm.termometro([r.get("resumo", "")], afirmacao)[0]
    saida.update(sustenta=t["sustenta"], refuta=t["refuta"],
                 relevante=t["relevante"], score=t["score"], motor="llm-juiz")
    erros = [e for e in (r.get("erro"), t.get("erro")) if e]
    saida["erro"] = " | ".join(erros) if erros else None
    if t.get("erro"):  # juiz falhou: lexical honesto no lugar do termômetro
        fb = _fallback_lexico(fonte, afirmacao)
        saida.update(sustenta=fb["sustenta"], refuta=fb["refuta"],
                     relevante=fb["relevante"], score=None, motor=fb.get("motor"))
        saida["erro"] = ((saida["erro"] or "") + " | fallback-lexico").strip(" |")
    return saida
