"""Deep crawl top-3: prova que o CORPO foi lido, não só manchete.

Segurança: allow-list do catálogo pré+pós redirect, bloqueio IP privado,
só http/https 80/443, teto bytes/tempo, nunca levanta (falha -> corpo_lido=False).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse

from . import config

log = logging.getLogger("factcheck.aprofundar")
_cache: Dict[str, tuple] = {}  # url -> (expira, CorpoLido)


@dataclass
class CorpoLido:
    url: str
    final_url: str = ""
    trecho_corpo: str = ""
    corpo_lido: bool = False
    erro: Optional[str] = None


def _ip_privado(host: str) -> bool:
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
    """Texto do artigo, sem boilerplate de navegação (round A, loop 7).

    Remove blocos estruturais (header/nav/footer/aside/form) e descarta
    parágrafos-menu (densidade de links > 0.5 ou curtos < 40 chars).
    Página só-boilerplate volta curta -> erro corpo-curto no _baixar.
    """
    txt = html or ""
    txt = re.sub(r"<(header|nav|footer|aside|form|noscript)[^>]*>.*?</\1>",
                 " ", txt, flags=re.I | re.S)
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", txt, flags=re.I | re.S)
    titulo = re.search(r"<title[^>]*>(.*?)</title>", txt, re.I | re.S)
    uteis = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", txt, re.S)[:30]:
        links = re.findall(r"<a[^>]*>.*?</a>", p, flags=re.S)
        limpo = re.sub(r"<[^>]+>", " ", p)
        limpo = re.sub(r"\s+", " ", limpo).strip()
        if not limpo or len(limpo) < 40:
            continue
        dens = (sum(len(re.sub(r"<[^>]+>", " ", a).strip()) for a in links)
                / max(1, len(limpo)))
        if dens > 0.5:  # parágrafo-menu: texto quase todo dentro de links
            continue
        uteis.append(limpo)
        if len(uteis) >= 12:
            break
    corpo = re.sub(r"\s+", " ", " ".join(uteis)).strip()[:8000]
    cabeca = (re.sub(r"\s+", " ", titulo.group(1)).strip()[:200] + "\n\n") if titulo else ""
    return (cabeca + corpo).strip()


async def _baixar(url: str, catalogo, timeout_pag: float, max_bytes: int) -> CorpoLido:
    if not catalogo.por_dominio(url):
        return CorpoLido(url=url, erro="fora do catalogo")
    if not _url_segura(url):
        return CorpoLido(url=url, erro="url insegura (SSRF)")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout_pag, follow_redirects=False) as cli:
            atual = url
            for _ in range(3):  # max 3 redirects, revalida cada hop
                # Streaming real: corpo nunca passa de max_bytes em memória.
                async with cli.stream("GET", atual,
                                      headers={"User-Agent": "factcheck-mvp/0.1"}) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        nxt = r.headers.get("location", "")
                        if not nxt.startswith("http"):
                            from urllib.parse import urljoin
                            nxt = urljoin(atual, nxt)
                        if not catalogo.por_dominio(nxt) or not _url_segura(nxt):
                            return CorpoLido(url=url, erro="redirect fora do catalogo/inseguro")
                        atual = nxt
                        continue
                    r.raise_for_status()
                    cl = r.headers.get("content-length")
                    if cl and int(cl) > max_bytes:
                        return CorpoLido(url=url, final_url=atual, erro="content-length acima do teto")
                    buf = b""
                    async for chunk in r.aiter_bytes(65536):
                        buf += chunk
                        if len(buf) > max_bytes:
                            break
                    html = buf[:max_bytes].decode("utf-8", errors="ignore")
                    corpo = extrair_corpo(html)
                    if len(corpo) < 200:
                        return CorpoLido(url=url, final_url=atual, erro="corpo curto/JS/paywall")
                    return CorpoLido(url=url, final_url=atual,
                                     trecho_corpo=corpo[:2000], corpo_lido=True)
            return CorpoLido(url=url, erro="redirects demais")
    except Exception as e:
        return CorpoLido(url=url, erro=str(e)[:120])


async def aprofundar(cands: List[dict], catalogo, por_afirm: int = 0,
                     timeout_pag: float = 5.0, budget_total: float = 0,
                     max_bytes: int = 0) -> Dict[str, CorpoLido]:
    """Baixa corpo top-N. Retorna {url: CorpoLido}. Nunca levanta."""
    por_afirm = por_afirm or config.DEEP_CRAWL_MAX_PAGES
    budget_total = budget_total or config.DEEP_CRAWL_TIMEOUT_S
    max_bytes = max_bytes or config.DEEP_CRAWL_MAX_BYTES
    # ordena por relevância laya, top-N por afirmação já fatiado pelo caller
    alvos = cands[: max(por_afirm * 3, por_afirm)]
    agora = time.time()
    pend, saida = [], {}
    for d in alvos:
        url = d.get("url", "")
        hit = _cache.get(url)
        if hit and hit[0] > agora:
            saida[url] = hit[1]
        elif url:
            pend.append(d)
    if not pend:
        return saida

    async def _um(d):
        try:
            return d["url"], await asyncio.wait_for(
                _baixar(d["url"], catalogo, timeout_pag, max_bytes), timeout=timeout_pag + 2)
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
            if len(_cache) >= 200:
                velha = min(_cache, key=lambda k: _cache[k][0])
                del _cache[velha]
            _cache[url] = (agora + 3600, corpo)
    except Exception:
        log.exception("aprofundar falhou (retorna parcial)")
    return saida
