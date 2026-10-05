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
from .logs import configurar_logs, novo_id, resumo_texto
from .pipeline import Pipeline
from .schemas import EntradaConsulta, RelatorioChecagem
from .serpapi_layer import SerpAPIClient

try:
    from .telegram_bot import _texto_link, classificar_entrada, formatar
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

    _texto_link = None  # type: ignore

configurar_logs("api")
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
                from . import factcheck_api as _fc
                log.info("pipeline pronto: %d portais, índice %d checagens + %d notícias | "
                         "serpapi=%s openrouter=%s unsloth=%s fact_check=%s llm_cap=%d",
                         len(catalogo), len(idx_ver), len(idx_not), _pipe.serpapi.ativo,
                         bool(config.OPENROUTER_API_KEY), config.UNSLOTH_BASE_URL,
                         _fc.ativo(), config.LLM_DAILY_CAP)
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


def _ip(request: Request | None) -> str:
    try:
        return request.client.host if request and request.client else "?"
    except Exception:
        return "?"


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
    novo_id()
    log.info("POST /checar de %s: tipo=%s", _ip(request), entrada.tipo)
    if not _rate_ok(request):
        log.warning("rate limit: %s bloqueado (429)", _ip(request))
        raise HTTPException(status_code=429, detail="muitas checagens; tente de novo em instantes")
    try:
        async with asyncio.timeout(180):
            entrada_exp, lido = await _expandir_link(entrada)
            rel = await pipeline().executar(entrada_exp)
            if entrada.tipo == "link" and not lido:
                rel.limitacoes.append(_LIMITACAO_LINK)
            log.info("POST /checar respondido: %s", rel.propensao)
            return rel
    except (asyncio.TimeoutError, TimeoutError):
        log.warning("checagem excedeu 180s (504)")
        raise HTTPException(status_code=504, detail="checagem excedeu o tempo; tente um texto mais curto")
    except HTTPException:
        raise
    except Exception:
        log.exception("falha no pipeline")  # detalhe só no servidor, nunca no cliente
        raise HTTPException(status_code=502, detail="falha temporária, tente de novo em instantes")


def _render_html(entrada_txt: str, rel: RelatorioChecagem | None = None) -> str:
    from .agregador import NOMES_ETAPAS, STATUS_ETAPA, postura_fonte
    esc = html.escape
    corpo = f"<form method='post' action='/checar-web'>" \
        f"<textarea name='conteudo' rows='4' style='width:100%' placeholder='Cole o texto, o título ou o link da notícia (pode ser um “ouvi dizer que…”)'>{esc(entrada_txt)}</textarea><br/>" \
        f"<button type='submit'>Analisar</button></form><hr/>"
    if rel:
        # Só fontes julgadas sobre o assunto pelo LLM-juiz (mesma regra do bot).
        uteis = [f for f in rel.fontes if f.relevante is True][:5]
        cor = {"contesta o conteúdo": "#b42318", "noticia o conteúdo como fato": "#067647"}
        fontes = "".join(
            f"<div style='border:1px solid #ccc;border-radius:6px;padding:8px;margin:6px 0'>"
            f"<b>{esc(f.portal_nome or 'web')}</b> "
            f"<span style='color:{cor.get(postura_fonte(f), '#555')}'>{esc(postura_fonte(f))}</span>"
            f"{' · selo da agência: '+esc(f.veredito) if f.veredito else ''}"
            f" · {'📄 texto lido' if f.corpo_lido else '📰 só manchete'}<br/>"
            f"{esc(f.titulo[:200])}<br/>"
            f"{'<i>“'+esc((f.resumo_juiz or f.quote or '')[:300])+'”</i><br/>' if (f.resumo_juiz or f.quote) else ''}"
            f"<a href=\"{esc(f.url)}\" target='_blank' rel='noopener'>{esc(f.url[:80])}</a></div>"
            for f in uteis)
        etapas = "".join(f"<li>{STATUS_ETAPA.get(e.status, '')} <b>{esc(NOMES_ETAPAS.get(e.nome, e.nome))}</b>: "
                         f"{esc(e.detalhe)}</li>" for e in rel.etapas)
        lims = "".join(f"<li>{esc(l)}</li>" for l in rel.limitacoes[:5])
        guia = "".join(f"<li>{esc(p)}</li>" for p in rel.perguntas_guia)
        corpo += (f"<h2>{esc(rel.header or rel.propensao)}</h2>"
                  f"<p><b>{esc(rel.why_1linha or '')}</b></p>"
                  f"<p>{esc(rel.justificativa)}</p>"
                  f"<h3>O que as fontes dizem</h3>"
                  f"{fontes or '<p>Não encontramos fontes que tratem do assunto. Na dúvida, não compartilhe.</p>'}"
                  f"<h3>Para avaliar você mesmo</h3><ul>{guia}</ul>"
                  f"<details><summary>Como chegamos aqui</summary><ul>{etapas}</ul></details>"
                  f"<details><summary>Limitações desta análise</summary><ul>{lims}</ul></details>")
    return ("<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>CrossCheckBR</title></head>"
            f"<body style='font-family:sans-serif;max-width:800px;margin:20px auto;padding:0 16px'>"
            f"<h1>CrossCheckBR — isso pode ser fake news?</h1>"
            f"<p>Cole o que você recebeu. Estimamos a propensão de ser fake news e mostramos o que "
            f"os veículos e agências de checagem dizem. Não damos veredito: você decide.</p>"
            f"{corpo}</body></html>")


async def _expandir_link(entrada: EntradaConsulta) -> tuple[EntradaConsulta, bool]:
    """Link de portal do catálogo: lê o texto (mesma regra anti-SSRF do bot)."""
    if entrada.tipo != "link" or _texto_link is None:
        return entrada, False
    texto = await asyncio.to_thread(_texto_link, entrada.conteudo, pipeline().catalogo)
    if not texto:
        log.info("link não lido (fora do catálogo ou inacessível): %s", entrada.conteudo[:120])
        return entrada, False
    log.info("link lido: %d caracteres de %s", len(texto), entrada.conteudo[:120])
    return EntradaConsulta(tipo="texto", conteudo=f"{entrada.conteudo}\n\n{texto}"[:20000]), True


_LIMITACAO_LINK = ("Não consegui ler o link (fora dos portais monitorados ou inacessível): "
                   "analisei só o endereço. Cole o texto para uma análise completa.")


@app.get("/", response_class=HTMLResponse)
def home():
    return _render_html("")


@app.post("/checar-web", response_class=HTMLResponse)
async def checar_web(request: Request, conteudo: str = Form(...)):
    novo_id()
    log.info("POST /checar-web de %s: %d caracteres", _ip(request), len(conteudo or ""))
    if not _rate_ok(request):
        log.warning("rate limit: %s bloqueado (429)", _ip(request))
        return HTMLResponse(_render_html(conteudo)
                            + "<p style='color:#a00'><b>Muitas checagens; "
                              "tente de novo em instantes.</b></p>", status_code=429)
    try:
        entrada = classificar_entrada(conteudo)
    except Exception:
        return HTMLResponse(_render_html(conteudo), status_code=400)
    try:
        async with asyncio.timeout(180):
            entrada_exp, lido = await _expandir_link(entrada)
            rel = await pipeline().executar(entrada_exp)
            if entrada.tipo == "link" and not lido:
                rel.limitacoes.append(_LIMITACAO_LINK)
        log.info("POST /checar-web respondido: %s", rel.propensao)
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
