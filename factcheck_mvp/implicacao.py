"""Testes de implicação com laya (noul): o texto SUSTENTA ou REFUTA a afirmação?

Uma chamada `predict` avalia as 2 perguntas num único forward pass (~ms).
Preferência laya: decisão calibrada, sem geração de texto.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from . import config

log = logging.getLogger("factcheck.implicacao")

try:
    import laya as _laya

    LAYA_OK = True
except Exception:  # pragma: no cover
    _laya = None  # type: ignore
    LAYA_OK = False

_ROUTER_INST = None
_PREDICT_LOCK = None  # inferência torch raramente é thread-safe; serializa


def _get_router():
    global _ROUTER_INST, _PREDICT_LOCK
    if _ROUTER_INST is None:
        if not LAYA_OK:
            raise RuntimeError("laya não instalado")
        import threading

        # Default CPU: seguro em toda máquina (MPS/CUDA quebram ou variam);
        # LAYA_DEVICE=cuda|mps só com teste local. Ver README.
        dispositivo = config.LAYA_DEVICE or "cpu"
        _ROUTER_INST = _laya.Router(preload=False, device=dispositivo)
        _PREDICT_LOCK = threading.Lock()
        log.info("laya Router pronto (device=%s)", dispositivo)
    return _ROUTER_INST, _PREDICT_LOCK


_PERGUNTAS = {
    "sustenta": {"type": "noul", "instructions": "O texto confirma a afirmação como verdadeira?"},
    "refuta": {"type": "noul", "instructions": "O texto nega a afirmação ou a declara falsa?"},
}


def implicacao(texto_fonte: str, afirmacao: str) -> Dict[str, Any]:
    """Retorna {sustenta, refuta, relevante, erro}. Nunca levanta exceção."""
    saida: Dict[str, Any] = {"sustenta": 0.0, "refuta": 0.0, "relevante": False, "erro": None}
    afirmacao = (afirmacao or "").strip()[:500]
    fonte = (texto_fonte or "").strip()[:2000]
    estado = f"AFIRMAÇÃO: {afirmacao}\nTEXTO: {fonte}"
    if len(afirmacao) < 10 or len(fonte) < 30:
        saida["erro"] = "texto insuficiente"
        return saida
    try:
        roteador, trava = _get_router()
        model_override = None if config.LAYA_MODEL == "auto" else config.LAYA_MODEL
        with trava:
            res = roteador.predict({"afirmacao": afirmacao, "texto": estado}, _PERGUNTAS,
                                   model=model_override)
        ans = res.get("answers", {})
        sus = float((ans.get("sustenta") or {}).get("noul", 0.0) or 0.0)
        ref = float((ans.get("refuta") or {}).get("noul", 0.0) or 0.0)
        saida.update(sustenta=round(sus, 4), refuta=round(ref, 4))
        saida["relevante"] = max(sus, ref) >= config.RELEVANCIA_MIN
    except Exception as e:  # inferência não pode quebrar o pipeline
        saida["erro"] = str(e)[:200]
        log.warning("laya implicação falhou: %s", e)
    return saida
