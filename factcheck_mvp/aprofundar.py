"""Deep crawl: prova que o CORPO foi lido, não só manchete.

Extração em cascata (`extracao.extrair`: JSON-LD -> trafilatura -> seletor do
catálogo -> regex -> [ReaderLM] -> falha). `corpo_lido=True` só se um nível
venceu com ≥ 500 chars.

Sem allow-list de catálogo (o núcleo é busca aberta): qualquer URL pública que
passe no anti-SSRF é lida — bloqueio de IP privado, só http/https 80/443,
redirect revalidado a cada hop, teto de bytes/tempo. Nunca levanta (falha ->
corpo_lido=False + erro).

Teto de bytes: `DEEP_CRAWL_MAX_BYTES` (env), default LOCAL de 5 MB. G1/Estadão
têm 0,6–1,2 MB e a BBC 9 MB; o teto antigo de 500 KB reduzia a BBC a 355 chars
(review A4). TODO(config.py): `config.DEEP_CRAWL_MAX_BYTES` ainda tem default
500000 — atualizar para 5_000_000 lá; enquanto isso este módulo lê a env direto.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, replace
from typing import Dict, List, Optional
from urllib.parse import urlparse

from . import config, extracao, replay, telemetria

log = logging.getLogger("factcheck.aprofundar")
_cache: Dict[str, tuple] = {}  # url -> (expira, CorpoLido)

MAX_BYTES_PADRAO = 5_000_000
TRECHO_MAX_CHARS = 3000  # trecho p/ o juiz (review C3: trecho_corpo[:3000])
# UA de navegador: UOL (Akamai) e TSE devolvem 403 para "factcheck-mvp/0.1" (review A4).
HEADERS_NAVEGADOR = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
}


def max_bytes_padrao(env: str = "DEEP_CRAWL_MAX_BYTES") -> int:
    """Teto de bytes: env se definida, senão 5 MB (ignora o default antigo do config)."""
    try:
        v = int(os.getenv(env, "") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else MAX_BYTES_PADRAO


@dataclass
class CorpoLido:
    url: str
    final_url: str = ""
    trecho_corpo: str = ""          # início + parágrafos relevantes (≤ ~3000 chars), p/ o juiz
    corpo_lido: bool = False        # True só se metodo != "falha" e texto ≥ 500 chars
    erro: Optional[str] = None
    texto_completo: str = ""        # corpo inteiro extraído (vazio se não lido)
    metodo: str = ""                # jsonld|trafilatura|seletor|regex|readerlm|falha ("" = nem baixou)
    titulo: str = ""
    data_pub: Optional[str] = None
    veredito_pagina: Optional[dict] = None  # ClaimReview da própria página (jsonld.veredito_da_pagina)


def _ip_privado(host: str) -> bool:
    if replay.modo_atual() == "replay":
        return False  # replay não resolve DNS: o cassete só existe se passou no SSRF ao gravar
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True  # não resolveu: trata como inseguro
    for fam, _, _, _, sockaddr in infos:
        ip_txt = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_txt)
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
                return True
        except ValueError:
            return True
    return False


def _url_segura(url: str) -> bool:
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    if "@" in (u.netloc or ""):
        return False
    if u.username or u.password:
        return False
    porta = u.port
    if porta is not None and porta not in (80, 443):
        return False
    host = (u.hostname or "").lower()
    if not host or host in ("localhost",):
        return False
    if _ip_privado(host):
        return False
    return True


def extrair_corpo(html: str) -> str:
    """Extrator ANTIGO (título + até 12 parágrafos <p>), mantido por compatibilidade.

    O deep crawl usa a cascata de `extracao.extrair` (JSON-LD -> trafilatura ->
    seletor -> regex -> ReaderLM -> falha); este é só o nível regex com o
    formato antigo. Página só-boilerplate volta curta.
    """
    titulo = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    corpo = re.sub(r"\s+", " ", " ".join(extracao.paragrafos_regex(html, 12))).strip()[:8000]
    cabeca = (re.sub(r"\s+", " ", extracao.limpar_texto(titulo.group(1))).strip()[:200] + "\n\n") if titulo else ""
    return (cabeca + corpo).strip()


# ----------------------------------------------------------------------------- trecho p/ o juiz
_STOP = {"que", "para", "com", "uma", "por", "dos", "das", "nos", "nas", "não", "nao", "mais",
         "como", "sobre", "foi", "são", "sao", "ser", "está", "esta", "isso", "essa", "esse",
         "pelo", "pela", "entre", "após", "apos", "também", "tambem", "quando", "onde", "seu", "sua"}
# Conclusão/selo da checagem costuma estar no FIM da matéria: parágrafo com estes
# marcadores ganha prioridade no trecho mesmo sem termo da afirmação.
_MARCADOR_CONCLUSAO = re.compile(
    r"conclus|classifica|verific|checag|#fake|#fato|é fake|é fato|falso|enganos|"
    r"verdadeir|distorcid|sem evid|sem comprova|não é verdade|nao e verdade|boato|montagem|"
    r"fora de contexto|descontextualizad|investigou e concluiu", re.I)


def _termos(texto: str) -> set:
    import unicodedata
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]{4,}", t) if w not in _STOP}


def montar_trecho(texto: str, afirmacao: str = "", limite: int = 0) -> str:
    """Trecho do corpo para o juiz: início + parágrafos com termos da afirmação
    ou marcadores de conclusão, em ordem original, até `limite` chars.

    Não corta cegamente em N chars: a conclusão da checagem costuma estar no fim.
    """
    limite = limite or TRECHO_MAX_CHARS
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    paras = [p.strip() for p in re.split(r"\n+", texto) if p.strip()]
    if len(paras) <= 1:  # sem parágrafos: início + fim
        meio = limite // 2
        return texto[:meio].rstrip() + " […] " + texto[-(limite - meio - 5):].lstrip()
    alvo = _termos(afirmacao)
    escolhidos: set = set()
    usado = 0
    # 1) início (lide): ~1/3 do orçamento
    for i, p in enumerate(paras):
        if usado >= limite // 3:
            break
        escolhidos.add(i)
        usado += len(p) + 1
    # 2) resto por relevância: termos da afirmação + marcador de conclusão (+ leve viés p/ o fim)
    pont = []
    for i, p in enumerate(paras):
        if i in escolhidos:
            continue
        n_termos = len(alvo & _termos(p)) if alvo else 0
        conclusao = 1 if _MARCADOR_CONCLUSAO.search(p) else 0
        if n_termos or conclusao:
            pont.append((n_termos * 2 + conclusao * 3 + i / len(paras), i))
    for _, i in sorted(pont, reverse=True):
        if usado + len(paras[i]) + 1 > limite:
            continue
        escolhidos.add(i)
        usado += len(paras[i]) + 1
    saida, anterior = [], -1
    for i in sorted(escolhidos):
        if anterior >= 0 and i != anterior + 1:
            saida.append("[…]")
        saida.append(paras[i])
        anterior = i
    return "\n".join(saida)[: limite + 20]


def _seletor_corpo(catalogo, url: str) -> Optional[str]:
    try:
        if catalogo is None:
            return None
        # por_url: domínio + aliases (Lupa em agencialupa.org); por_dominio p/ catálogos antigos
        achar = getattr(catalogo, "por_url", None) or getattr(catalogo, "por_dominio")
        portal = achar(url)
        return ((portal or {}).get("css_selectors") or {}).get("corpo") or None
    except Exception:
        return None


async def corpo_de_html(url: str, final_url: str, html: str, seletor: Optional[str] = None,
                        afirmacao: str = "", truncado: bool = False,
                        estagio: str = "extracao") -> CorpoLido:
    """HTML já baixado -> CorpoLido via cascata (em thread: trafilatura/bs4 são CPU).

    `corpo_lido=True` só se a cascata venceu (metodo != "falha") com ≥ 500 chars.
    Emite evento `fonte` (estagio="extracao"). Compartilhado com descoberta_site.
    """
    try:
        ex = await asyncio.to_thread(extracao.extrair, html, final_url or url, seletor)
    except Exception as e:  # extrair não levanta, mas to_thread pode (loop fechando)
        telemetria.fallback("extracao", f"{type(e).__name__}: {e}", url=url)
        return CorpoLido(url=url, final_url=final_url, erro=f"extracao: {e}"[:120], metodo="falha")
    lido = ex.metodo != "falha" and len(ex.texto) >= extracao.MIN_CHARS
    erro = None
    if not lido:
        erro = "corpo curto/JS/paywall (" + "; ".join(ex.avisos)[:160] + ")"
        if truncado:
            erro += " [html truncado no teto de bytes]"
    corpo = CorpoLido(url=url, final_url=final_url,
                      trecho_corpo=montar_trecho(ex.texto, afirmacao) if lido else "",
                      corpo_lido=lido, erro=erro, texto_completo=ex.texto if lido else "",
                      metodo=ex.metodo, titulo=ex.titulo, data_pub=ex.data_pub,
                      veredito_pagina=ex.veredito_pagina)
    telemetria.evento("fonte", url=url, estagio=estagio,
                      decisao="mantida" if lido else "descartada",
                      motivo=(f"corpo lido via {ex.metodo}" if lido else erro),
                      metodo=ex.metodo, n_chars=len(ex.texto), truncado=truncado,
                      veredito_pagina=(ex.veredito_pagina or {}).get("veredito"),
                      avisos=ex.avisos[:6])
    return corpo


async def _baixar(url: str, catalogo, timeout_pag: float, max_bytes: int,
                  afirmacao: str = "") -> CorpoLido:
    # Sem allow-list de catálogo: o núcleo é busca aberta. Qualquer URL pública que
    # passe no anti-SSRF é lida; o peso de fonte não-curada é do agregador.
    if not _url_segura(url):
        return CorpoLido(url=url, erro="url insegura (SSRF)")
    try:
        atual = url
        for _ in range(3):  # max 3 redirects, revalida cada hop
            # Streaming real (replay.ahttp_get): corpo nunca passa de max_bytes em memória.
            r = await replay.ahttp_get(atual, max_bytes=max_bytes, timeout=timeout_pag,
                                       headers=dict(HEADERS_NAVEGADOR))
            if r.status_code in (301, 302, 303, 307, 308):
                nxt = r.headers.get("location", "")
                if not nxt.startswith("http"):
                    from urllib.parse import urljoin
                    nxt = urljoin(atual, nxt)
                if not _url_segura(nxt):
                    return CorpoLido(url=url, erro="redirect inseguro (SSRF)")
                atual = nxt
                continue
            r.raise_for_status()
            # Acima do teto: lê o prefixo (streaming já cortou) e tenta extrair
            # mesmo assim; o evento registra truncado=True.
            html = r.content[:max_bytes].decode("utf-8", errors="ignore")
            truncado = bool(getattr(r, "truncado", False))
            return await corpo_de_html(url, atual, html, _seletor_corpo(catalogo, atual),
                                       afirmacao, truncado)
        return CorpoLido(url=url, erro="redirects demais")
    except Exception as e:
        return CorpoLido(url=url, erro=str(e)[:120])


async def aprofundar(cands: List[dict], catalogo, por_afirm: int = 0,
                     timeout_pag: float = 5.0, budget_total: float = 0,
                     max_bytes: int = 0) -> Dict[str, CorpoLido]:
    """Baixa corpo top-N. Retorna {url: CorpoLido}. Nunca levanta."""
    por_afirm = por_afirm or config.DEEP_CRAWL_MAX_PAGES
    budget_total = budget_total or config.DEEP_CRAWL_TIMEOUT_S
    max_bytes = max_bytes or max_bytes_padrao()
    # ordena por relevância laya, top-N por afirmação já fatiado pelo caller
    alvos = cands[: max(por_afirm * 3, por_afirm)]
    agora = time.time()
    pend, saida = [], {}
    for d in alvos:
        url = d.get("url", "")
        hit = _cache.get(url)
        if hit and hit[0] > agora:
            c = hit[1]
            if c.corpo_lido and c.texto_completo:  # trecho depende da afirmação de quem pede
                c = replace(c, trecho_corpo=montar_trecho(
                    c.texto_completo, d.get("_afirmacao") or d.get("titulo") or ""))
            saida[url] = c
        elif url:
            pend.append(d)
    if not pend:
        return saida

    async def _um(d):
        try:
            return d["url"], await asyncio.wait_for(
                _baixar(d["url"], catalogo, timeout_pag, max_bytes,
                        d.get("_afirmacao") or d.get("titulo") or ""),
                timeout=timeout_pag + 10)  # +10s: extração de HTML grande roda em thread
        except Exception as e:
            return d["url"], CorpoLido(url=d["url"], erro=str(e)[:120])

    try:
        # asyncio.wait NÃO cancela: downloads concluídos no budget sobrevivem
        # (wait_for(gather) descartaria resultados prontos ao expirar).
        tarefas = [asyncio.ensure_future(_um(d)) for d in pend[: por_afirm * 2]]
        concluidas, pendentes = await asyncio.wait(tarefas, timeout=budget_total)
        if pendentes:
            log.warning("aprofundar budget_total=%.0fs: %d/%d concluídos",
                        budget_total, len(concluidas), len(tarefas))
            for t in pendentes:
                t.cancel()
        for t in concluidas:
            try:
                url, corpo = t.result()
            except Exception:
                continue  # falha já virou CorpoLido(erro) em _um; nunca levanta
            saida[url] = corpo
            telemetria.evento("fonte", url=url, estagio="deep-crawl",
                              decisao="mantida" if corpo.corpo_lido else "descartada",
                              motivo=corpo.erro or f"corpo lido via {corpo.metodo} ({len(corpo.texto_completo)} chars)",
                              metodo=corpo.metodo, n_chars=len(corpo.texto_completo))
            if len(_cache) >= 200:
                velha = min(_cache, key=lambda k: _cache[k][0])
                del _cache[velha]
            _cache[url] = (agora + 3600, corpo)
    except Exception as e:
        log.exception("aprofundar falhou (retorna parcial)")
        telemetria.fallback("aprofundar", f"{type(e).__name__}: {e}")
    return saida
