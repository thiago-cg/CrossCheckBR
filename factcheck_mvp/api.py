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
    from .telegram_bot import (LINK_SEM_TEXTO_MSG, _texto_link, _urls_nao_analisadas,
                               classificar_entrada, formatar)
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

    def _urls_nao_analisadas(rel) -> set:  # type: ignore
        try:
            dec = getattr(rel, "decisao", None) or {}
            return {n.get("url") for n in dec.get("nao_analisadas", []) or [] if n.get("url")}
        except Exception:
            return set()

    _texto_link = None  # type: ignore
    LINK_SEM_TEXTO_MSG = "Não consegui ler esse link. Cole aqui o título e o texto da notícia."

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
                # Índice de checagens (ClaimReview/RSS): atalho opcional atrás de INDICE_CHECAGENS
                idx_ver = Indice.de_checagens()
                idx_not = Indice()
                _pipe = Pipeline(catalogo, idx_ver, idx_not, SerpAPIClient())
    return _pipe


@app.get("/saude")
def saude():
    p = pipeline()
    return {"ok": True, "portais": len(p.catalogo), "vereditos": len(p.idx_ver),
            "noticias": len(p.idx_not), "serpapi": p.serpapi.ativo,
            "serpapi_engine": p.serpapi.engine_default,
            "serpapi_estrategia": getattr(config, "SERP_ESTRATEGIA", "agente"),
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


async def _ler_link(entrada: EntradaConsulta) -> EntradaConsulta | None:
    """Link -> texto da página (mesma regra do bot: só portais do catálogo, anti-SSRF).

    Retorna a entrada pronta para o pipeline, ou None se o link não pôde ser lido —
    aí, como no bot, pede-se o texto em vez de checar só o endereço."""
    if entrada.tipo != "link":
        return entrada
    if _texto_link is None:
        return None
    texto = await asyncio.to_thread(_texto_link, entrada.conteudo, pipeline().catalogo)
    if not texto:
        return None
    return EntradaConsulta(tipo="texto", conteudo=f"{entrada.conteudo}\n\n{texto}"[:20000])


@app.post("/checar", response_model=RelatorioChecagem)
async def checar(entrada: EntradaConsulta, request: Request):
    if not _rate_ok(request):
        raise HTTPException(status_code=429, detail="muitas checagens; tente de novo em instantes")
    try:
        async with asyncio.timeout(180):
            pronta = await _ler_link(entrada)
            if pronta is None:
                raise HTTPException(status_code=422, detail=LINK_SEM_TEXTO_MSG)
            return await pipeline().executar(pronta)
    except (asyncio.TimeoutError, TimeoutError):
        raise HTTPException(status_code=504, detail="checagem excedeu o tempo; tente um texto mais curto")
    except HTTPException:
        raise
    except Exception:
        log.exception("falha no pipeline")  # detalhe só no servidor, nunca no cliente
        raise HTTPException(status_code=502, detail="falha temporária, tente de novo em instantes")


def _render_html(entrada_txt: str, rel: RelatorioChecagem | None = None) -> str:
    from .agregador import (NOMES_ETAPAS, STATUS_ETAPA, data_publicacao_legivel, direcoes_por_url,
                            linha_raciocinio, postura_legivel)
    from .confiabilidade import ROTULO
    esc = html.escape
    corpo = f"<form method='post' action='/checar-web'>" \
        f"<textarea name='conteudo' rows='4' style='width:100%' placeholder='Cole o texto, o título ou o link da notícia (pode ser um “ouvi dizer que…”)'>{esc(entrada_txt)}</textarea><br/>" \
        f"<button type='submit'>Analisar</button></form><hr/>"
    if rel:
        # Mesma regra do bot: julgadas como fora do tema não aparecem como evidência.
        direcoes = direcoes_por_url(rel.decisao)
        nao_lidas = _urls_nao_analisadas(rel)
        uteis = [f for f in rel.fontes if f.relevante is not False][:5]
        cor = {"contesta o que o texto afirma": "#b42318", "confirma o que o texto afirma": "#067647"}
        try:
            _dec = rel.decisao or {}
            _descontadas = {x.get("url") for x in _dec.get("descontos_temporais", []) or [] if x.get("url")}
        except Exception:
            _descontadas = set()

        def _cartao(f) -> str:
            post = postura_legivel(f, direcoes)
            if bool(getattr(f, "corpo_lido", False)) and f.url not in nao_lidas:
                leitura = "📄 texto lido"
            else:
                # E3: só título/snippet não foi analisada integralmente (não vota).
                leitura = "📰 só manchete — não analisada integralmente"
            # E4 Task 6: aviso neutro de data por fonte (sobre DATAS, nunca veracidade; sem bits).
            _r = getattr(f, "relevancia_temporal", None)
            _linha_data = ""
            if (_r is not None and _r < 1.0) or (f.url in _descontadas):
                _dtxt = data_publicacao_legivel(getattr(f, "data_pub", None), getattr(f, "data_pub_precisao", None))
                if _dtxt:
                    _linha_data = f"<br/>📅 publicada em {esc(_dtxt)} · anterior ao período do texto"
                else:
                    _linha_data = "<br/>📅 anterior ao período do texto"
            # B1a: porquê do avaliador, atribuído e escapado; só quando há raciocínio.
            _rac = linha_raciocinio(getattr(f, "raciocinio", None))
            _html_rac = f"<span style='color:#444'>{esc(_rac)}</span><br/>" if _rac else ""
            return (f"<div style='border:1px solid #ccc;border-radius:6px;padding:8px;margin:6px 0'>"
                    f"<b>{esc(f.portal_nome or 'web')}</b> "
                    f"<span style='color:{cor.get(post, '#555')}'>{esc(post)}</span>"
                    f"{' · selo da agência: ' + esc(f.veredito) if f.veredito else ''}"
                    f" · {leitura}"
                    f"{' · ' + esc(ROTULO[f.confiabilidade]) if f.confiabilidade in ROTULO else ''}<br/>"
                    f"{esc(f.titulo[:200])}<br/>"
                    f"{'<i>“' + esc((f.quote or '')[:300]) + '”</i><br/>' if f.quote else ''}"
                    f"{_html_rac}"
                    f"{_linha_data}"
                    f"<a href=\"{esc(f.url)}\" target='_blank' rel='noopener'>{esc(f.url[:80])}</a></div>")
        fontes = "".join(_cartao(f) for f in uteis)
        etapas = "".join(f"<li>{STATUS_ETAPA.get(e.status, '')} <b>{esc(NOMES_ETAPAS.get(e.nome, e.nome))}</b>: "
                         f"{esc(e.detalhe)}</li>" for e in rel.etapas)
        lims = "".join(f"<li>{esc(l)}</li>" for l in rel.limitacoes[:5])
        guia = "".join(f"<li>{esc(p)}</li>" for p in rel.perguntas_guia)
        aviso = ("<p><i>Não encontrado na base de checagens; resultado de outras fontes</i></p>"
                 if rel.onde_encontrado == "web" else "")
        corpo += (f"<h2>{esc(rel.header or rel.propensao)}</h2>"
                  f"<p><b>{esc(rel.why_1linha or '')}</b></p>"
                  f"{aviso}"
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
            pronta = await _ler_link(entrada)
            if pronta is None:
                return HTMLResponse(_render_html(conteudo) + "<p style='color:#a00'><b>"
                                    + html.escape(LINK_SEM_TEXTO_MSG) + "</b></p>", status_code=422)
            rel = await pipeline().executar(pronta)
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
