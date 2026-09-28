"""RF08/RF09: adapter do modelo próprio de detecção.

Enquanto o modelo treinado não é ligado (FAKE_MODEL_PATH vazio), um mock
EXPLÍCITO responde — sempre marcado `mock: True` e citado nas limitações.
Interface estável: trocar o mock pelo real não muda o pipeline.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Protocol

from . import config

log = logging.getLogger("factcheck.modelo")


class DetectorFake(Protocol):
    def analisar(self, texto: str) -> Dict[str, Any]:
        """Retorna {prob_fake 0..1, modelo, mock bool}."""


_SINAIS_LEXICOS = [
    (re.compile(r"\b(urgente|compartilhe|repasse|não deixe de|acabou de sair)\b", re.I), 0.12),
    (re.compile(r"[A-ZÁÉÍÓÚÇ ]{15,}"), 0.08),
    (re.compile(r"[!?]{2,}"), 0.06),
    (re.compile(r"\b(mídia esconde|não querem que você saiba|vazou|bomba)\b", re.I), 0.15),
]


class MockDetector:
    """PLACEHOLDER determinístico. Nunca confundir com o modelo real (RF09)."""

    nome = "mock-heuristico-v0"

    def analisar(self, texto: str) -> Dict[str, Any]:
        t = texto or ""
        bonus = sum(peso for rx, peso in _SINAIS_LEXICOS if rx.search(t))
        if not bonus:
            return {"prob_fake": 0.5, "modelo": self.nome, "mock": True}  # neutro: sem indício, sem direção
        score = 0.5 + bonus
        palavras = len(t.split())
        if palavras < 30:
            score += 0.05  # texto curto demais p/ avaliar
        return {"prob_fake": round(min(0.95, max(0.05, score)), 3), "modelo": self.nome, "mock": True}


def _curto_demais(texto: str) -> bool:
    """Manchete/frase solta: os modelos treinados no FakenewsBR v6 erram muito
    aqui (atalho de formato: curto → fake). Abaixo do piso, o modelo não opina."""
    return len((texto or "").split()) < max(0, config.FAKE_MODEL_MIN_PALAVRAS)


def _nao_aplicavel(nome: str) -> Dict[str, Any]:
    return {"prob_fake": None, "modelo": nome, "mock": False, "aplicavel": False,
            "motivo": f"texto com menos de {config.FAKE_MODEL_MIN_PALAVRAS} palavras"}


class ModeloTreinadoDetector:
    """Hook p/ o modelo real da equipe. Formato esperado: joblib/sklearn com
    `.predict_proba([texto])[0][1]`. Falha -> exceção clara no boot, não no chat."""

    def __init__(self, caminho: str):
        import joblib

        self._modelo = joblib.load(caminho)
        self.nome = f"custom:{caminho.rsplit('/', 1)[-1]}"

    def analisar(self, texto: str) -> Dict[str, Any]:
        if _curto_demais(texto):
            return _nao_aplicavel(self.nome)
        proba = float(self._modelo.predict_proba([texto or ""])[0][1])
        return {"prob_fake": round(proba, 3), "modelo": self.nome, "mock": False}


class BertimbauDetector:
    """BERTimbau fine-tunado do FakenewsBR v6 (R0): pasta com `config.json`,
    `model.safetensors`, tokenizer e `calibration.json` (Platt).

    p_fake = sigmoid(a·(logit1 − logit0) + b), classe 1 = fake — idêntico a
    `apply_platt` do treino (models/v6/train_bertimbau_v6.py).
    """

    def __init__(self, pasta: str):
        import json
        from pathlib import Path

        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        p = Path(pasta)
        cal = json.loads((p / "calibration.json").read_text(encoding="utf-8"))
        self._a = float(cal["platt_a"])
        self._b = float(cal["platt_b"])
        self._max_len = int(cal.get("max_length", 192))
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(str(p))
        self._modelo = AutoModelForSequenceClassification.from_pretrained(str(p)).eval()
        self.nome = f"bertimbau:{p.name}"

    def analisar(self, texto: str) -> Dict[str, Any]:
        import math

        if _curto_demais(texto):
            return _nao_aplicavel(self.nome)
        enc = self._tok([texto or ""], truncation=True, max_length=self._max_len,
                        return_tensors="pt")
        with self._torch.no_grad():
            lg = self._modelo(**enc).logits[0].tolist()
        z = self._a * (lg[1] - lg[0]) + self._b
        proba = 1.0 / (1.0 + math.exp(-max(-700.0, min(700.0, z))))
        return {"prob_fake": round(proba, 3), "modelo": self.nome, "mock": False}


def carregar_detector() -> DetectorFake:
    if config.FAKE_MODEL_PATH:
        try:
            from pathlib import Path

            if (Path(config.FAKE_MODEL_PATH) / "calibration.json").exists():
                det = BertimbauDetector(config.FAKE_MODEL_PATH)
            else:
                det = ModeloTreinadoDetector(config.FAKE_MODEL_PATH)
            log.info("modelo fake real carregado: %s", det.nome)
            return det
        except Exception as e:
            log.error("falha ao carregar FAKE_MODEL_PATH (%s); usando mock. %s", config.FAKE_MODEL_PATH, e)
    log.warning("FAKE_MODEL_PATH vazio: usando MOCK explícito (não é o modelo real).")
    return MockDetector()
