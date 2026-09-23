"""API compartilhada Telegram <-> futura web (RNF05). Tipada pelos schemas."""
from __future__ import annotations

import asyncio
import html
import logging
import threading
import time
from collections import defaultdict

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import config
from .catalogo import Catalogo
from .indice import Indice
from .pipeline import Pipeline
from .schemas import EntradaConsulta, RelatorioChecagem
from .serpapi_layer import SerpAPIClient

try:
    from .telegram_bot import classificar_entrada, formatar
except Exception:  # telegram opcional p/ API/web (bot não quebra API)
    def classificar_entrada(texto: str) -> EntradaConsulta:  # type: ignore
        t = (texto or "").strip()
        if t.startswith(("http://", "https://", "www.")):
            return EntradaConsulta(tipo="link", conteudo=t[:2000])
        if len(t) <= 140 and "\n" not in t:
            return EntradaConsulta(tipo="titulo", conteudo=t)
        return EntradaConsulta(tipo="texto", conteudo=t[:20000])

    def formatar(rel) -> str:  # type: ignore
        return f"{rel.header or rel.propensao} {rel.why_1linha or rel.justificativa}"

log = logging.getLogger("factcheck.api")
app = FastAPI(title="Checagem cruzada de fatos (MVP)", version="0.2.0")

_pipe: Pipeline | None = None
_lock = threading.Lock()
_hits: defaultdict = defaultdict(list)  # ip -> [timestamps] rate-limit simples


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
            "noticias": len(p.idx_not), "serpapi": p.serpapi.ativo,
            "serpapi_uso_hoje": p.serpapi.uso_hoje,
            "serpapi_cap": getattr(config, "SERPAPI_DAILY_CAP", 100),
            "versao": "mvp-0.2.0"}


@app.get("/portais")
def portais():
    p = pipeline()
    # Sem `responsavel` (nomes da equipe) na resposta pública.
    return [{"id": x["id"], "nome": x["nome"], "tipo": x["tipo"],
             "homepage": x["homepage"]} for x in p.catalogo.portais]


def _rate_ok(request: Request | None) -> bool:
    """Rate-limit por IP (sliding window 60s); True = pode prosseguir.

    Evita SerpAPI+LLM sem throttle; poda o mapa quando cresce demais
    (bot traffic não vira vazamento de memória).
    """
    try:
        limite = getattr(config, "API_RATE_LIMIT_PER_MIN", 30) or 30
        if limite <= 0 or request is None or not getattr(request, "client", None):
            return True
        ip = getattr(request.client, "host", "unknown") or "unknown"
        agora = time.time()
        if len(_hits) > 1000:  # evict: entradas sem hit recente
            for k in [k for k, v in _hits.items()
                      if not v or agora - v[-1] >= 120][: len(_hits) - 1000]:
                _hits.pop(k, None)
        janela = [t for t in _hits.get(ip, []) if agora - t < 60]
        if len(janela) >= limite:
            return False
        janela.append(agora)
        _hits[ip] = janela[-limite:]
        return True
    except Exception:
        return True


@app.post("/checar", response_model=RelatorioChecagem)
async def checar(entrada: EntradaConsulta, request: Request):
    if not _rate_ok(request):
        raise HTTPException(status_code=429, detail="muitas checagens; tente de novo em instantes")
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


def _render_html(entrada_txt: str, rel: RelatorioChecagem | None = None) -> str:
    esc = html.escape
    corpo = f"<form method='post' action='/checar-web'>" \
        f"<textarea name='conteudo' rows='4' cols='70' placeholder='Ouvi dizer que… (sem fonte, sem certeza — pode colar assim mesmo)'>{esc(entrada_txt)}</textarea><br/>" \
        f"<button type='submit'>Checar rumor</button></form><hr/>"
    if rel:
        fontes = "".join(
            f"<div style='border:1px solid #ccc;padding:8px;margin:6px'>"
            f"<b>{esc(f.portal_nome or 'web')}</b>"
            f"{' [selo: '+esc(f.veredito)+']' if f.veredito else ''} "
            f"{'📄 corpo lido' if f.corpo_lido else '📰 só título'}<br/>"
            f"{esc(f.titulo[:200])}<br/>"
            f"{'<i>“'+esc((f.quote or '')[:200])+'”</i><br/>' if f.quote else ''}"
            f"<a href=\"{esc(f.url)}\" target='_blank' rel='noopener'>{esc(f.url[:80])}</a></div>"
            for f in rel.fontes[:3])
        etapas = "; ".join(f"{e.nome}:{e.status}" for e in rel.etapas)
        lims = "".join(f"<li>{esc(l)}</li>" for l in rel.limitacoes[:5])
        corpo += (f"<h2>{esc(rel.header or rel.propensao.upper())}</h2>"
                  f"<p>{esc(rel.why_1linha or rel.justificativa)}</p>"
                  f"<p>{esc(rel.justificativa)}</p>"
                  f"<h3>Fontes lado a lado (2-3)</h3>{fontes or '<p>Sem fontes.</p>'}"
                  f"<p><b>Passo a passo:</b> {esc(etapas)}</p>"
                  f"<ul>{lims}</ul>")
    return ("<html><head><meta charset='utf-8'><title>CrossCheckBR — checar rumor</title></head>"
            f"<body style='font-family:sans-serif;max-width:800px;margin:20px'>"
            f"<h1>CrossCheckBR — ouvi dizer, e agora?</h1>"
            f"<p>Cole o rumor como ouviu (vago, sem fonte, sem certeza). Devolvo propensão + porquê + 2-3 fontes lado a lado.</p>"
            f"{corpo}</body></html>")


@app.get("/", response_class=HTMLResponse)
def home():
    return _render_html("")


@app.post("/checar-web", response_class=HTMLResponse)
async def checar_web(request: Request, conteudo: str = Form(...)):
    if not _rate_ok(request):
        return HTMLResponse(_render_html(conteudo)
                            + "<p style='color:#a00'><b>Muitas checagens; "
                              "tente de novo em instantes.</b></p>", status_code=429)
    try:
        entrada = classificar_entrada(conteudo)
    except Exception:
        return HTMLResponse(_render_html(conteudo), status_code=400)
    try:
        async with asyncio.timeout(180):
            rel = await pipeline().executar(entrada)
        return HTMLResponse(_render_html(conteudo, rel))
    except Exception:
        log.exception("falha no pipeline web")
        return HTMLResponse(_render_html(conteudo), status_code=502)


@app.get("/progresso.json")
def progresso_json():
    # Endpoint público: retorna só subconjunto seguro (sem notas internas).
    try:
        import json
        from pathlib import Path
        arq = Path(__file__).resolve().parent.parent / "PROGRESSO.json"
        if arq.exists():
            bruto = json.loads(arq.read_text(encoding="utf-8"))
            return {"loop": bruto.get("loop"), "status": bruto.get("status"),
                    "versao": bruto.get("versao")}
    except Exception:
        pass
    return {"loop": 1, "status": "em andamento"}
