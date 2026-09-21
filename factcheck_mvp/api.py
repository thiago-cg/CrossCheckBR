"""API compartilhada Telegram <-> futura web (RNF05). Tipada pelos schemas."""
from __future__ import annotations

import asyncio
import logging
import threading

from fastapi import FastAPI, HTTPException

from . import config
from .catalogo import Catalogo
from .indice import Indice
from .pipeline import Pipeline
from .schemas import EntradaConsulta, RelatorioChecagem
from .serpapi_layer import SerpAPIClient

log = logging.getLogger("factcheck.api")
app = FastAPI(title="Checagem cruzada de fatos (MVP)", version="0.1.0")

_pipe: Pipeline | None = None
_lock = threading.Lock()


def pipeline() -> Pipeline:
    global _pipe
    if _pipe is None:
        with _lock:
            if _pipe is None:
                try:
                    catalogo = Catalogo.carregar()
                except RuntimeError as e:
                    log.error("catálogo indisponível: %s", e)
                    raise HTTPException(status_code=503, detail="catálogo indisponível, tente mais tarde")
                idx_ver = Indice()
                idx_not = Indice()
                try:
                    import json
                    from pathlib import Path

                    base = Path(__file__).resolve().parent / "data"
                    for nome in ("amostra_checagem.json", "amostra_geral.json"):
                        arq = base / nome
                        if arq.exists():
                            for a in json.loads(arq.read_text(encoding="utf-8")):
                                (idx_ver if (a.get("tipo_conteudo") == "checagem") else idx_not).adicionar(a)
                except Exception:
                    log.exception("falha ao carregar amostras do índice")
                _pipe = Pipeline(catalogo, idx_ver, idx_not, SerpAPIClient())
    return _pipe


@app.get("/saude")
def saude():
    p = pipeline()
    return {"ok": True, "portais": len(p.catalogo), "vereditos": len(p.idx_ver),
            "noticias": len(p.idx_not), "serpapi": p.serpapi.ativo}


@app.get("/portais")
def portais():
    p = pipeline()
    # Sem `responsavel` (nomes da equipe) na resposta pública.
    return [{"id": x["id"], "nome": x["nome"], "tipo": x["tipo"],
             "homepage": x["homepage"]} for x in p.catalogo.portais]


@app.post("/checar", response_model=RelatorioChecagem)
async def checar(entrada: EntradaConsulta):
    try:
        async with asyncio.timeout(180):
            return await pipeline().executar(entrada)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(status_code=504, detail="checagem excedeu o tempo; tente um texto mais curto")
    except HTTPException:
        raise
    except Exception:
        log.exception("falha no pipeline")  # detalhe só no servidor, nunca no cliente
        raise HTTPException(status_code=502, detail="falha temporária, tente de novo em instantes")
