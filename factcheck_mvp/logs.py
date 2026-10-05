"""Logs do projeto: terminal + arquivo rotativo, com id por checagem.

Cada checagem recebe um id curto (contextvar) que aparece em todas as linhas
dela — inclusive nas threads (asyncio.to_thread copia o contexto) — para
separar checagens simultâneas no mesmo arquivo.

Nunca logar chaves/headers. Texto do usuário só truncado (`resumo_texto`).
"""
from __future__ import annotations

import contextvars
import logging
import os
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

id_consulta: contextvars.ContextVar[str] = contextvars.ContextVar("id_consulta", default="-")

_FORMATO = "%(asctime)s %(levelname)-7s [%(id_consulta)s] %(name)s: %(message)s"
_configurado = False


class _FiltroId(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.id_consulta = id_consulta.get()
        return True


def novo_id() -> str:
    """Gera e ativa um id para a checagem corrente."""
    i = uuid.uuid4().hex[:8]
    id_consulta.set(i)
    return i


def resumo_texto(texto: str, n: int = 80) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= n else t[:n] + "…"


def configurar_logs(nome_arquivo: str = "api") -> None:
    """Terminal + logs/<nome>.log (5 MB x 5). Idempotente.

    LOG_LEVEL (padrão INFO) e LOG_DIR (padrão <projeto>/logs) via ambiente.
    """
    global _configurado
    if _configurado:
        return
    nivel = getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO)
    pasta = Path(os.getenv("LOG_DIR") or Path(__file__).resolve().parent.parent / "logs")
    fmt = logging.Formatter(_FORMATO)
    filtro = _FiltroId()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        pasta.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(pasta / f"{nome_arquivo}.log", maxBytes=5_000_000,
                                            backupCount=5, encoding="utf-8"))
    except OSError:
        pass  # sem permissão de escrita: segue só no terminal
    # Só o namespace do projeto em INFO; bibliotecas (httpx etc.) ficam em WARNING.
    app = logging.getLogger("factcheck")
    app.setLevel(nivel)
    app.propagate = False
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(filtro)
        app.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _configurado = True
