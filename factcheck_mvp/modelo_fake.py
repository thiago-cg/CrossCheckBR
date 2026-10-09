"""RF08/RF09: adapter do modelo próprio de detecção.

Enquanto o modelo treinado não é ligado (FAKE_MODEL_PATH vazio), um mock
EXPLÍCITO responde — sempre marcado `mock: True` e citado nas limitações.
Interface estável: trocar o mock pelo real não muda o pipeline.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Protocol

from . import config

log = logging.getLogger("factcheck.modelo")

# T5 (B1b/D1): modelo treinado com texto curto → credibilidade da página em
# blocos de max_length tokens (192), até 4 blocos, com média.
BERT_MAX_LENGTH = 192
BERT_MAX_BLOCOS = 4


def _blocos(texto: str, max_length: int = BERT_MAX_LENGTH,
            max_blocos: int = BERT_MAX_BLOCOS) -> List[str]:
    toks = (texto or "").split()
    return [" ".join(toks[i:i + max_length])
            for i in range(0, len(toks), max_length)][:max_blocos]


def _combinar(titulo: str, corpo: str) -> str:
    return "\n".join(t for t in (titulo or "", corpo or "") if t.strip())


class DetectorFake(Protocol):
    def analisar(self, texto: str) -> Dict[str, Any]:
        """Retorna {prob_fake 0..1, modelo, mock bool}."""

    def analisar_pagina(self, titulo: str, corpo: str) -> Dict[str, Any]:
        """Credibilidade da página: {prob_fake 0..1, modelo, mock bool}."""


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

    def analisar_pagina(self, titulo: str, corpo: str) -> Dict[str, Any]:
        """Média do mock por bloco de 192 tokens (máx 4)."""
        blocos = _blocos(_combinar(titulo, corpo)) or [""]
        probs = [self.analisar(b)["prob_fake"] for b in blocos]
        return {"prob_fake": round(sum(probs) / len(probs), 3),
                "modelo": self.nome, "mock": True}


class ModeloTreinadoDetector:
    """Hook p/ o modelo real da equipe. Formato esperado: joblib/sklearn com
    `.predict_proba([texto])[0][1]`. Falha -> exceção clara no boot, não no chat."""

    def __init__(self, caminho: str):
        import joblib

        self._modelo = joblib.load(caminho)
        self.nome = f"custom:{caminho.rsplit('/', 1)[-1]}"

    def analisar(self, texto: str) -> Dict[str, Any]:
        proba = float(self._modelo.predict_proba([texto or ""])[0][1])
        return {"prob_fake": round(proba, 3), "modelo": self.nome, "mock": False}

    def analisar_pagina(self, titulo: str, corpo: str) -> Dict[str, Any]:
        """sklearn de texto curto: 1 chamada no texto combinado (sem blocos)."""
        return self.analisar(_combinar(titulo, corpo))


class BertimbauDetector:
    """Modelo real BERTimbau (transformers) com calibração Platt.

    Diretório esperado: config.json + model.safetensors + tokenizer.json +
    calibration.json {"platt_a", "platt_b", "max_length"}. Prob calibrada:
    sigmoid(a * margem + b), margem = logit[fake] - logit[true].
    Falha -> exceção clara no boot, não no chat.
    """

    def __init__(self, caminho: str):
        import json
        from pathlib import Path

        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as e:
            raise RuntimeError(f"transformers ausente p/ modelo BERTimbau: {e}") from e
        try:
            import torch
        except ImportError as e:
            raise RuntimeError(f"torch ausente p/ modelo BERTimbau: {e}") from e

        base = Path(caminho)
        with open(base / "calibration.json", encoding="utf-8") as f:
            calib = json.load(f)
        self._a = float(calib["platt_a"])
        self._b = float(calib["platt_b"])
        self._max_len = int(calib.get("max_length", 192))
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(caminho, local_files_only=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(
            caminho, local_files_only=True)
        self._model.eval()
        self.nome = f"bertimbau:{base.name}"

    def analisar(self, texto: str) -> Dict[str, Any]:
        import math

        torch = self._torch
        enc = self._tok((texto or "")[:5000], return_tensors="pt",
                        truncation=True, max_length=self._max_len)
        with torch.no_grad():
            logits = self._model(**enc).logits[0].tolist()
        margem = logits[1] - logits[0] if len(logits) > 1 else logits[0]
        proba = 1.0 / (1.0 + math.exp(-(self._a * margem + self._b)))
        return {"prob_fake": round(min(0.99, max(0.01, proba)), 3),
                "modelo": self.nome, "mock": False}

    def analisar_pagina(self, titulo: str, corpo: str) -> Dict[str, Any]:
        """Média calibrada por bloco de max_length tokens (máx 4)."""
        ids = self._tok(_combinar(titulo, corpo), add_special_tokens=False).get("input_ids") or []
        blocos = [ids[i:i + self._max_len]
                  for i in range(0, len(ids), self._max_len)][:BERT_MAX_BLOCOS] or [[]]
        probs = [self.analisar(self._tok.decode(b))["prob_fake"] for b in blocos]
        return {"prob_fake": round(sum(probs) / len(probs), 3),
                "modelo": self.nome, "mock": False}


def carregar_detector() -> DetectorFake:
    if config.FAKE_MODEL_PATH:
        from pathlib import Path

        caminho = Path(config.FAKE_MODEL_PATH)
        try:
            if caminho.is_dir() and (caminho / "config.json").exists():
                det = BertimbauDetector(config.FAKE_MODEL_PATH)
            else:
                det = ModeloTreinadoDetector(config.FAKE_MODEL_PATH)
            log.info("modelo fake real carregado: %s", det.nome)
            return det
        except Exception as e:
            log.error("falha ao carregar FAKE_MODEL_PATH (%s); usando mock. %s", config.FAKE_MODEL_PATH, e)
    log.warning("FAKE_MODEL_PATH vazio: usando MOCK explícito (não é o modelo real).")
    return MockDetector()
