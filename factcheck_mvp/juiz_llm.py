"""LLM-juiz: 1 LLM resume cada notícia relevante (teto) + 1 juiz dá o termômetro.

Fluxo por afirmação:
  1. `resumir(fonte, afirmacao)` — 1 chat call por notícia: resumo factual em
     até 3 frases do que a peça diz SOBRE a afirmação (ou FORA_DO_TEMA).
  2. `termometro(resumos, afirmacao)` — 1 chat call em LOTE p/ todos os
     resumos: o juiz devolve por item {"score": -100..+100, "relevante": bool}.
     Termômetro: +100 sustenta totalmente … 0 neutro/não trata … -100 refuta.

Nunca levanta: sem chave/cap/falha, cada item degrada p/ irrelevante honesto
(erro preenchido) e o pipeline cai no caminho lexical provisório.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from . import config
from . import llm_openrouter

log = logging.getLogger("factcheck.juiz")

FORA_DO_TEMA = "FORA_DO_TEMA"

_RESUMO_INSTRUCAO = (
    "Resuma em até 3 frases, em português, o que a NOTÍCIA afirma sobre a AFIRMAÇÃO. "
    "Só fatos do texto, sem opinião e sem dar veredito. "
    f'Se o texto NÃO trata do tema, responda EXATAMENTE: {FORA_DO_TEMA}.'
)

_JUIZ_INSTRUCAO = (
    "Você é o juiz de fact-check. Para cada item, leia o RESUMO da notícia e avalie "
    "frente à AFIRMAÇÃO num termômetro: +100 sustenta totalmente como fato verdadeiro; "
    "0 neutro ou não trata do tema; -100 refuta/desmente totalmente. "
    'Responda SÓ o JSON, sem markdown: {"0": {"score": N, "relevante": true}, ...}.'
)


def resumir(fonte_texto: str, afirmacao: str) -> Dict[str, Any]:
    """Resume 1 notícia frente à afirmação. Retorna {resumo, erro, motor}."""
    saida: Dict[str, Any] = {"resumo": "", "erro": None, "motor": "llm-juiz"}
    afirmacao = (afirmacao or "").strip()[:500]
    fonte = (fonte_texto or "").strip()[:2000]
    if len(afirmacao) < 10 or len(fonte) < 30:
        saida["erro"] = "texto insuficiente"
        return saida
    try:
        texto, modelo = llm_openrouter.chat(
            [{"role": "user",
              "content": f"{_RESUMO_INSTRUCAO}\n\nAFIRMAÇÃO:\n{afirmacao}\n\nNOTÍCIA:\n{fonte}"}],
            max_tokens=config.JUIZ_RESUMO_MAX_TOKENS or 300,
            timeout_s=config.OPENROUTER_TIMEOUT_S)
        texto = (texto or "").strip()
        if not texto:
            raise RuntimeError("resumo vazio")
        saida["resumo"] = texto[:600]
        if modelo:
            saida["modelo"] = modelo
    except Exception as e:  # sem chave/cap/rede: caller decide o fallback
        saida["erro"] = str(e)[:200]
        log.warning("juiz resumir falhou: %s", str(e)[:120])
    return saida


def _parse_juiz(texto: str, n: int) -> Dict[int, Dict[str, Any]]:
    """Extrai {idx: {score, relevante}} do JSON do juiz. Rigoroso mas tolerante."""
    out: Dict[int, Dict[str, Any]] = {}
    try:
        m = re.search(r"\{.*\}", texto or "", re.S)
        bruto = json.loads(m.group(0)) if m else {}
    except (ValueError, AttributeError):
        return out
    if not isinstance(bruto, dict):
        return out
    for i in range(n):
        item = bruto.get(str(i), bruto.get(i))
        if not isinstance(item, dict):
            continue
        try:
            score = int(float(item.get("score", 0)))
        except (TypeError, ValueError):
            continue
        score = max(-100, min(100, score))
        rel = item.get("relevante")
        if not isinstance(rel, bool):
            rel = abs(score) >= (config.JUIZ_RELEVANCIA_MIN or 30)
        out[i] = {"score": score, "relevante": bool(rel)}
    return out


def termometro(resumos: List[str], afirmacao: str) -> List[Dict[str, Any]]:
    """Juiz em lote: 1 call p/ N resumos. Retorna por item
    {score, sustenta, refuta, relevante, erro, motor}. Nunca levanta."""
    afirmacao = (afirmacao or "").strip()[:500]
    itens: List[Dict[str, Any]] = []
    for r in resumos:
        r = (r or "").strip()
        if not r or r == FORA_DO_TEMA:
            itens.append({"score": 0, "sustenta": 0.0, "refuta": 0.0,
                          "relevante": False, "erro": None, "motor": "llm-juiz"})
        else:
            itens.append(None)  # type: ignore[list-item]
    pend = [i for i, it in enumerate(itens) if it is None]
    if not pend:
        return itens  # type: ignore[return-value]
    # Rótulos sequenciais no prompt (o modelo devolve {"0":…}) remapeados p/
    # o índice original — conjunto esparso (ex FORA_DO_TEMA no meio) não perde
    # nem troca scores.
    seq_de_orig = {s: i for s, i in enumerate(pend)}
    try:
        bloco = "\n".join(f"[{s}] {resumos[i][:600]}" for s, i in seq_de_orig.items())
        texto, _ = llm_openrouter.chat(
            [{"role": "user",
              "content": f"{_JUIZ_INSTRUCAO}\n\nAFIRMAÇÃO:\n{afirmacao}\n\nRESUMOS:\n{bloco}"}],
            max_tokens=800,
            timeout_s=config.OPENROUTER_TIMEOUT_S)
        parsed = _parse_juiz(texto, len(pend))
        for s, i in seq_de_orig.items():
            p = parsed.get(s)
            if p is None:
                itens[i] = {"score": 0, "sustenta": 0.0, "refuta": 0.0,
                            "relevante": False, "erro": "juiz sem score p/ item",
                            "motor": "llm-juiz"}
                continue
            s = p["score"]
            itens[i] = {"score": s,
                        "sustenta": round(max(0, s) / 100, 4),
                        "refuta": round(max(0, -s) / 100, 4),
                        "relevante": p["relevante"], "erro": None, "motor": "llm-juiz"}
    except Exception as e:
        for i in pend:
            itens[i] = {"score": 0, "sustenta": 0.0, "refuta": 0.0,
                        "relevante": False, "erro": str(e)[:200], "motor": "llm-juiz"}
        log.warning("juiz termômetro falhou: %s", str(e)[:120])
    return itens  # type: ignore[return-value]
