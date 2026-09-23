"""Descoberta de catálogo: crawl profundo de site NÃO catalogado + proposta de schema.

Antes o deep crawl só lia URLs do catálogo (allow-list anti-SSRF). Agora sites
novos relevantes ganham leitura em MODO DESCOBERTA — mesmas proteções SSRF
(IP privado, http/https 80/443, redirect revalidado, teto bytes/tempo; tetos
MENORES que o crawl catalogado) — e viram PROPOSTA pendente em
`data/catalogo_propostas.json`. Nunca há auto-merge em `catalogo.json`:
aprovação é manual (curadoria), como numa agência de checagem.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from . import config
from .aprofundar import CorpoLido, _url_segura, extrair_corpo

log = logging.getLogger("factcheck.descoberta")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PROPOSTAS = BASE_DIR / "data" / "catalogo_propostas.json"
MAX_PROPOSTAS = 200

_RSS_RE = re.compile(
    r'<link[^>]+rel=["\']alternate["\'][^>]*>', re.I)
_TIPO_RSS_RE = re.compile(r'type=["\']application/(rss|atom)\+xml["\']', re.I)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_OG_SITE_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_A_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\']', re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_CHECAGEM_HINTS = re.compile(
    r"checagem|fato.?ou.?fake|verifica|farsas|boatos\.org|e-farsas|lupa|aos.?fatos", re.I)
_SELETOR_CORPO_CANDIDATOS = [
    "article",
    "div[itemprop='articleBody']",
    "[itemprop='articleBody']",
    "main",
    ".post-content",
    ".entry-content",
    ".mc-article-body",
]
_SELETOR_TITULO_CANDIDATOS = [
    "h1",
    "h1[itemprop='headline']",
    "h1.content-head__title",
]
_SEGMENTO_RUIDO = {
    "login", "cadastro", "contato", "sobre", "about", "privacidade", "privacy",
    "termos", "terms", "cookies", "assine", "assinatura", "publicidade",
    "anuncie", "trabalhe", "contato", "ajuda", "help", "faq", "sitemap",
    "feed", "rss", "wp-content", "wp-includes", "static", "assets",
}


def dominio_de(url: str) -> str:
    """Domínio normalizado (sem www, minúsculo)."""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def homepage_de(url: str) -> str:
    """Homepage do mesmo esquema+domínio da URL."""
    try:
        u = urlparse(url or "")
        esquema = u.scheme if u.scheme in ("http", "https") else "https"
        host = u.hostname or ""
        if not host:
            return ""
        return f"{esquema}://{host}/"
    except Exception:
        return ""


async def _fetch_aberto(url: str, timeout: float, max_bytes: int) -> tuple[str, str, Optional[str]]:
    """GET seguro SEM gate de catálogo (modo descoberta). Retorna (html, final, erro).

    Mantém TODAS as proteções SSRF do crawl catalogado; só remove a exigência
    de domínio conhecido. Teto de bytes/tempo menor por padrão.
    """
    if not _url_segura(url):
        return "", url, "url insegura (SSRF)"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as cli:
            atual = url
            for _ in range(3):
                async with cli.stream("GET", atual,
                                      headers={"User-Agent": "factcheck-mvp/0.1-descoberta"}) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        nxt = r.headers.get("location", "")
                        if not nxt.startswith("http"):
                            nxt = urljoin(atual, nxt)
                        if not _url_segura(nxt):
                            return "", url, "redirect inseguro (SSRF)"
                        atual = nxt
                        continue
                    r.raise_for_status()
                    buf = b""
                    async for chunk in r.aiter_bytes(65536):
                        buf += chunk
                        if len(buf) > max_bytes:
                            break
                    return buf[:max_bytes].decode("utf-8", errors="ignore"), atual, None
            return "", url, "redirects demais"
    except Exception as e:
        return "", url, str(e)[:120]


def _abs_mesmo_dominio(base_home: str, href: str, dominio: str) -> Optional[str]:
    try:
        abs_url = urljoin(base_home, href or "")
        if dominio_de(abs_url) == dominio and abs_url.startswith(("http://", "https://")):
            return abs_url
    except Exception:
        pass
    return None


def extrair_schema(url_artigo: str, html_artigo: str, html_home: str = "",
                   corpo_lido: bool = False) -> Dict:
    """Schema no formato de `catalogo.json` (puro, sem rede — testável)."""
    dominio = dominio_de(url_artigo)
    home = homepage_de(url_artigo)
    slug = re.sub(r"[^a-z0-9]+", "-", dominio).strip("-") or "site-novo"

    og = _OG_SITE_RE.search(html_artigo or "")
    titulo_m = _TITLE_RE.search(html_artigo or "")
    nome = (og.group(1).strip() if og else
            (re.sub(r"\s+", " ", titulo_m.group(1)).strip()[:80] if titulo_m else dominio))

    rss: List[str] = []
    for tag in _RSS_RE.findall(html_home or ""):
        if _TIPO_RSS_RE.search(tag):
            m = _HREF_RE.search(tag)
            if m:
                abs_r = _abs_mesmo_dominio(home, m.group(1), dominio)
                if abs_r and abs_r not in rss:
                    rss.append(abs_r)

    # Editorias: primeiros segmentos de links internos da home, por frequência
    freq: Dict[str, int] = {}
    for href in _A_HREF_RE.findall(html_home or "")[:500]:
        abs_u = _abs_mesmo_dominio(home, href, dominio)
        if not abs_u:
            continue
        segs = [s for s in urlparse(abs_u).path.strip("/").split("/") if s]
        if segs and segs[0].lower() not in _SEGMENTO_RUIDO and len(segs[0]) >= 3:
            freq[segs[0].lower()] = freq.get(segs[0].lower(), 0) + 1
    editorias = [s for s, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:8]]

    # Padrão de URL do artigo: 1º (e 1º+2º) segmento do path
    patterns: List[str] = []
    try:
        segs_art = [s for s in urlparse(url_artigo).path.strip("/").split("/") if s]
        if segs_art:
            patterns.append(f"/{segs_art[0]}/")
            if len(segs_art) >= 2:
                patterns.append(f"/{segs_art[0]}/{segs_art[1]}/")
    except Exception:
        pass

    corpoBaixo = (html_artigo or "").lower()
    # detecção simples por presença do marcador no HTML
    corpo_sel = [s for s in _SELETOR_CORPO_CANDIDATOS
                 if (s.startswith(".") and s in corpoBaixo)
                 or (s.startswith("[") and s.strip("[]").split("=")[0] in corpoBaixo)
                 or (s in ("article", "main") and f"<{s}" in corpoBaixo)]
    titulo_sel = [s for s in _SELETOR_TITULO_CANDIDATOS
                  if (s == "h1" and "<h1" in corpoBaixo)
                  or (s != "h1" and s in corpoBaixo)]
    h1_m = _H1_RE.search(html_artigo or "")

    try:
        path_art = urlparse(url_artigo).path or ""
    except Exception:
        path_art = ""
    tipo = "checagem" if _CHECAGEM_HINTS.search(f"{dominio} {nome} {path_art}") else "geral"

    return {
        "id": slug,
        "nome": nome,
        "tipo": tipo,
        "homepage": home,
        "dominio": dominio,
        "rss": rss,
        "sitemap": None,  # preenchido por detectar_sitemap()
        "editorias": editorias,
        "article_url_patterns": patterns,
        "css_selectors": {
            "titulo": ", ".join(titulo_sel) or "h1",
            "corpo": ", ".join(corpo_sel) or "article",
        },
        "titulo_exemplo": (re.sub(r"\s+", " ", h1_m.group(1)).strip()[:160] if h1_m else ""),
        "estrategia_recomendada": "JsonCssExtractionStrategy (barato) + LLM local como fallback/validação",
        "observacoes": ("Proposta automática do modo descoberta — curadoria pendente. "
                        f"Evidência na origem: corpo_lido={corpo_lido}."),
        "exemplo_urls": [url_artigo],
        "responsavel": None,
        "subsecoes": [],
        "status": "pendente",
        "coletado_em": datetime.now(timezone.utc).isoformat(),
    }


async def detectar_sitemap(homepage: str, timeout: float = 8.0) -> Optional[str]:
    """Confere /sitemap.xml e /sitemap_index.xml (leve, 50KB)."""
    for nome in ("sitemap.xml", "sitemap_index.xml"):
        url = urljoin(homepage, nome)
        html, _, erro = await _fetch_aberto(url, timeout=timeout, max_bytes=50000)
        if not erro and ("<urlset" in html or "<sitemapindex" in html):
            return url
    return None


async def descobrir(urls: List[str], timeout: float = 0,
                    max_bytes: int = 0) -> Dict[str, Dict]:
    """Crawl profundo em modo descoberta. Retorna {url: {corpo, proposta, erro}}.

    Nunca levanta: cada URL falha isolada (corpo None + erro).
    """
    timeout = timeout or getattr(config, "DISCOVERY_TIMEOUT_S", 8)
    max_bytes = max_bytes or getattr(config, "DISCOVERY_MAX_BYTES", 200000)
    saida: Dict[str, Dict] = {}
    for url in urls:
        try:
            html_art, final_art, erro_art = await _fetch_aberto(url, timeout, max_bytes)
            corpo = None
            if not erro_art and html_art:
                texto = extrair_corpo(html_art)
                if len(texto) >= 200:
                    corpo = CorpoLido(url=url, final_url=final_art,
                                      trecho_corpo=texto[:2000], corpo_lido=True)
                else:
                    erro_art = "corpo curto/JS/paywall"
            home = homepage_de(url)
            html_home, _, _ = await _fetch_aberto(home, timeout, min(max_bytes, 200000)) if home else ("", "", None)
            proposta = None
            if html_art:
                proposta = extrair_schema(url, html_art, html_home,
                                          corpo_lido=bool(corpo and corpo.corpo_lido))
                if proposta:
                    try:
                        tipo_jev = _classificar_tipo_jev(
                            proposta.get("nome", ""), proposta.get("homepage", ""),
                            proposta.get("editorias", []) or [])
                    except Exception:
                        tipo_jev = None  # nunca quebra a descoberta
                    if tipo_jev:
                        proposta["tipo"] = tipo_jev
                try:
                    proposta["sitemap"] = await detectar_sitemap(home, timeout=timeout)
                except Exception:
                    pass
            saida[url] = {"corpo": corpo, "proposta": proposta,
                          "erro": erro_art if not corpo else None}
        except Exception as e:  # isolamento por URL
            log.warning("descoberta falhou %s: %s", (url or "")[:80], str(e)[:100])
            saida[url] = {"corpo": None, "proposta": None, "erro": str(e)[:120]}
    return saida


def carregar_propostas(caminho: Optional[str] = None) -> List[Dict]:
    """Lista propostas pendentes (vazio se arquivo ausente). Nunca levanta."""
    try:
        bruto = json.loads(Path(caminho or DEFAULT_PROPOSTAS).read_text(encoding="utf-8"))
        props = bruto.get("propostas", [])
        return props if isinstance(props, list) else []
    except (OSError, ValueError):
        return []


def salvar_proposta(proposta: Dict, caminho: Optional[str] = None) -> bool:
    """Insere/atualiza proposta por domínio (dedupe). Retorna True se salvou."""
    if not proposta or not proposta.get("dominio"):
        return False
    arq = Path(caminho or DEFAULT_PROPOSTAS)
    try:
        atual = carregar_propostas(str(arq))
    except Exception:
        atual = []
    atual = [p for p in atual if p.get("dominio") != proposta["dominio"]]
    atual.append(proposta)
    # teto: mantém as 200 mais recentes
    try:
        atual.sort(key=lambda p: p.get("coletado_em", ""))
    except Exception:
        pass
    atual = atual[-MAX_PROPOSTAS:]
    try:
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(json.dumps({"versao_schema": "1.0.0",
                                   "propostas": atual}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        return True
    except OSError:
        log.exception("falha ao salvar proposta de catálogo")
        return False


# Campos da proposta que NÃO entram no catalogo.json (metadados de descoberta)
_CHAVES_DESCOBERTA = {"dominio", "titulo_exemplo", "status", "coletado_em"}

_TIPOS_VALIDOS = ("geral", "checagem")


# Jev é usado SÓ aqui (classificação de schema): o julgamento de notícias é
# do LLM-juiz (juiz_llm.py). Uma chamada Decisions com 1 noul (~ms).
JEV_MODEL = "~typesafe/jev-latest"
_JEV_PERGUNTA_TIPO = {"e_checagem": {
    "type": "noul",
    "instructions": "O site descrito em `site` é um serviço de checagem de fatos (fact-checking)?",
    "criteria": {
        "true": "Veículo/agência dedicado a verificar fatos e desmentir boatos",
        "false": "Portal de notícias geral, blog, loja ou outro tipo de site",
    },
}}


def _classificar_tipo_jev(nome: str, homepage: str, editorias: List[str]) -> Optional[str]:
    """Reclassifica tipo via Jev/OpenRouter Decisions. None se falhar.

    Nunca levanta: sem chave/rede, mantém o tipo heurístico da proposta.
    """
    try:
        from . import llm_openrouter
        texto = f"SITE: {nome} {homepage} editorias: {', '.join(editorias[:8])}"
        res = llm_openrouter.decisions(
            model=JEV_MODEL, state={"site": texto},
            questions=_JEV_PERGUNTA_TIPO, timeout_s=config.JEV_TIMEOUT_S)
        ans = (res.get("answers") or {}).get("e_checagem") or {}
        try:
            score = float(ans.get("noul", 0.0) or 0.0)
        except (TypeError, ValueError):
            return None
        return "checagem" if score >= 0.5 else "geral"
    except Exception as e:  # sem chave/rede: mantém tipo da proposta
        log.warning("jev indisponível p/ reclassificar tipo: %s", str(e)[:100])
        return None


def inserir_no_catalogo(prop: Dict, caminho_catalogo: Optional[str] = None,
                        tipo: Optional[str] = None,
                        origem: str = "descoberta-automatica") -> Dict:
    """Grava proposta no catalogo.json imediatamente. Retorna {"ok", "portal"} ou erro.

    Carimba origem + data (auditoria) — nunca levanta.
    """
    from .catalogo import DEFAULT_CATALOGO
    if tipo is not None and tipo not in _TIPOS_VALIDOS:
        return {"ok": False, "erro": f"tipo inválido: {tipo} (use geral|checagem)"}
    for campo in ("id", "nome", "homepage"):
        if not prop.get(campo):
            return {"ok": False, "erro": f"proposta sem campo obrigatório: {campo}"}
    if not str(prop["homepage"]).startswith(("http://", "https://")):
        return {"ok": False, "erro": "homepage fora de http(s)"}
    tipo_final = tipo or prop.get("tipo") or "geral"
    if tipo_final not in _TIPOS_VALIDOS:
        tipo_final = "geral"

    hoje = datetime.now(timezone.utc).date().isoformat()
    portal = {k: v for k, v in prop.items() if k not in _CHAVES_DESCOBERTA}
    portal["tipo"] = tipo_final
    portal["origem_catalogo"] = origem
    portal["adicionado_em"] = hoje
    portal["observacoes"] = (
        (portal.get("observacoes") or "")
        + (f" [promovido de proposta em {hoje}, curadoria manual]"
           if origem == "curadoria-manual"
           else f" [adicionado ao catálogo em {hoje} (descoberta automática), curadoria posterior]")
    ).strip()

    arq_cat = Path(caminho_catalogo or DEFAULT_CATALOGO)
    try:
        dados = json.loads(arq_cat.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "erro": f"catalogo.json ilegível: {e}"}
    portais = dados.get("portais")
    if not isinstance(portais, list):
        return {"ok": False, "erro": "catalogo.json sem lista `portais`"}
    if any(p.get("id") == portal["id"] for p in portais):
        return {"ok": False, "erro": f"id {portal['id']} já existe no catálogo"}
    if any(dominio_de(p.get("homepage", "")) == prop.get("dominio") for p in portais):
        return {"ok": False, "erro": f"domínio {prop.get('dominio')} já existe no catálogo"}
    portais.append(portal)
    try:
        arq_cat.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    except OSError as e:
        return {"ok": False, "erro": f"falha ao gravar catalogo.json: {e}"}
    return {"ok": True, "portal": portal}


def promover_proposta(dominio: str, caminho_propostas: Optional[str] = None,
                      caminho_catalogo: Optional[str] = None,
                      tipo: Optional[str] = None, usar_jev: bool = False) -> Dict:
    """Promove proposta pendente -> catalogo.json. Determinístico e rápido.

    Precedência do tipo: `tipo` explícito > `--jev` > tipo da proposta.
    Retorna {"ok", "portal"} ou {"ok": False, "erro"} — nunca levanta.
    """
    dom = (dominio or "").strip().lower()
    if dom.startswith("www."):
        dom = dom[4:]
    if not dom:
        return {"ok": False, "erro": "domínio vazio"}
    props = carregar_propostas(caminho_propostas)
    prop = next((p for p in props if p.get("dominio") == dom), None)
    if prop is None:
        return {"ok": False, "erro": f"sem proposta pendente p/ {dom} (veja `listar`)"}

    tipo_final = tipo or prop.get("tipo") or "geral"
    if usar_jev and tipo is None:
        try:
            sugestao = _classificar_tipo_jev(prop.get("nome", ""), prop.get("homepage", ""),
                                             prop.get("editorias", []) or [])
        except Exception:
            sugestao = None  # nunca levanta: sem Jev, mantém tipo da proposta
        if sugestao:
            tipo_final = sugestao

    res = inserir_no_catalogo(prop, caminho_catalogo, tipo=tipo_final,
                                origem="curadoria-manual")
    if not res.get("ok"):
        return res
    # Baixa da fila de pendentes (promovida, não pendente)
    restantes = [p for p in props if p.get("dominio") != dom]
    try:
        Path(caminho_propostas or DEFAULT_PROPOSTAS).write_text(
            json.dumps({"versao_schema": "1.0.0", "propostas": restantes},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        log.warning("promovido mas fila de propostas não atualizou (edite manual)")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: listar | promover <dominio> [--tipo T] [--laya]."""
    import argparse
    ap = argparse.ArgumentParser(prog="descoberta_site",
                                 description="Fila de propostas do catálogo (curadoria manual).")
    ap.add_argument("--propostas", default=None, help="caminho alternativo de propostas")
    ap.add_argument("--catalogo", default=None, help="caminho alternativo de catalogo.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar", help="lista propostas pendentes")
    pr = sub.add_parser("promover", help="promove proposta pendente -> catalogo.json")
    pr.add_argument("dominio", help="domínio da proposta (ex: exemplo.com)")
    pr.add_argument("--tipo", choices=["geral", "checagem"], default=None)
    pr.add_argument("--jev", action="store_true", help="reclassifica tipo via Jev (~ms)")
    pr.add_argument("--laya", action="store_true", help="(legado: alias de --jev)")
    args = ap.parse_args(argv)

    if args.cmd == "listar":
        for p in carregar_propostas(args.propostas):
            print(f"{p.get('dominio')} | {p.get('nome')} | tipo:{p.get('tipo')} "
                  f"| rss:{len(p.get('rss') or [])} | {p.get('homepage')}")
        return 0
    res = promover_proposta(args.dominio, caminho_propostas=args.propostas,
                            caminho_catalogo=args.catalogo,
                            tipo=args.tipo, usar_jev=bool(args.jev or args.laya))
    if res.get("ok"):
        print(f"promovido: {res['portal']['id']} ({res['portal']['tipo']})")
        return 0
    print(f"erro: {res.get('erro')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
