"""Corroboração: dedupe canônico e clusters de INDEPENDÊNCIA (um voto por cluster).

Ordem no pipeline (antes do juiz):
1. `url_canonica` — sem esquema/www/amp/fragmento/query de tracking: a mesma URL
   vinda do índice e da web vira UMA peça (`fundir_por_url`).
2. `agrupar` — clusters de independência (union-find):
   - assinatura de agência no corpo ("Estadão Conteúdo", "Agência Brasil",
     "Reuters", "AFP", "Folhapress", "Agência O Globo") → cluster da agência;
   - mesmo domínio-base (grupo econômico: folha.uol + uol) → mesmo cluster;
   - corpos quase iguais (contenção de shingles de 5 palavras >= 0,8) → mesmo
     cluster (republicação editada em outro domínio);
   - sem corpo: título quase igual (Jaccard >= 0,85).
3. `divergencias` — datas (`data_pub`) e vereditos diferentes ENTRE clusters:
   informação para o recibo, nunca sinal de decisão.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

# ----------------------------------------------------------------------------- texto
def _norm(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", texto).strip()


def _similaridade(a: str, b: str) -> float:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def shingles(texto: str, k: int = 5) -> set:
    toks = re.findall(r"[a-z0-9]+", _norm(texto))
    if len(toks) < k:
        return set()
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def similaridade_corpo(a: str, b: str) -> float:
    """Contenção de shingles (|A∩B| / min(|A|,|B|)): robusta a corte/rodapé diferente."""
    sa, sb = shingles(a), shingles(b)
    if len(sa) < 8 or len(sb) < 8:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


# ----------------------------------------------------------------------------- URL
_SUFIXO_DUPLO = (".com.br", ".org.br", ".net.br", ".gov.br", ".edu.br", ".jus.br", ".leg.br",
                 ".co.uk", ".org.uk", ".com.ar", ".com.mx", ".com.pt")
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|dclid|msclkid|mc_[a-z]+|ref|ref_src|ref_url|source|"
                       r"cmpid|cmp|origin|origem|amp|outputtype|output|__twitter_impression|"
                       r"s|share|at_[a-z]+|ito|xtor|ncid|ocid|_ga|igshid|spm|from)$", re.I)


def dominio_base(url: str) -> str:
    """Domínio registrável aprox. (grupo econômico): folha.uol.com.br e
    uol.com.br -> uol.com.br; g1.globo.com e globo.com -> globo.com."""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    for suf in _SUFIXO_DUPLO:
        if host.endswith(suf):
            nucleo = host[: -len(suf)]
            return (nucleo.split(".")[-1] + suf) if "." in nucleo else host
    partes = host.split(".")
    return ".".join(partes[-2:]) if len(partes) >= 2 else host


def dominio(url: str) -> str:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def url_canonica(url: str) -> str:
    """https + host sem www/amp + caminho sem /amp e sem barra final + query sem tracking,
    ordenada; sem fragmento. Plataformas (youtube ?v=) preservam o id."""
    try:
        u = urlparse((url or "").strip())
    except Exception:
        return (url or "").strip()
    host = (u.hostname or "").lower()
    if not host:
        return (url or "").strip()
    for pre in ("www.", "amp.", "m."):
        if host.startswith(pre) and host.count(".") >= 2:
            host = host[len(pre):]
    caminho = re.sub(r"/+", "/", u.path or "/")
    caminho = re.sub(r"(/amp|\.amp|/amp\.html)$", "", caminho, flags=re.I)
    caminho = re.sub(r"^/amp/", "/", caminho, flags=re.I)
    caminho = caminho.rstrip("/") or "/"
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=False) if not _TRACKING.match(k)]
    query = urlencode(sorted(q))
    porta = f":{u.port}" if u.port and u.port not in (80, 443) else ""
    return f"https://{host}{porta}{caminho}" + (f"?{query}" if query else "")


# ----------------------------------------------------------------------------- agências
AGENCIAS = {
    "estadao-conteudo": r"estad[ãa]o conte[úu]do",
    "agencia-brasil": r"ag[êe]ncia brasil",
    "reuters": r"reuters",
    "afp": r"\bafp\b|france[- ]presse",
    "folhapress": r"folhapress",
    "agencia-o-globo": r"ag[êe]ncia o globo",
}
# Assinatura = nome da agência em posição de crédito (parênteses, "Fonte:", "Com
# informações da", "Por", linha própria), no começo ou no fim do corpo. Menção no
# meio do texto ("disse à Reuters") não conta. Folhapress/Estadão Conteúdo são
# sempre crédito.
_SEMPRE_CREDITO = ("estadao-conteudo", "folhapress")


# Crédito de foto/imagem ("Foto: Folhapress", "Imagem: X", "Divulgação") NÃO é
# assinatura de agência: indica só a origem da imagem, não republicação do texto.
# Vale p/ todas as agências (regra geral, não só Folhapress).
_FOTO_MARCA_RE = re.compile(
    r"\b(fotos?|imagem(ns)?|ilustra[çc][ãa]o|cr[ée]ditos?|divulga[çc][ãa]o|reprodu[çc][ãa]o|arquivo)\b", re.I)


def _e_credito_foto(t: str, ini: int) -> bool:
    """True se o match em t[ini:] está num crédito de foto: a linha (ou a anterior,
    p/ "Foto:" quebrado em duas linhas) traz marca de foto/imagem/crédito antes."""
    ini_linha = t.rfind("\n", 0, ini) + 1
    antes = t[ini_linha:ini][-80:]
    if _FOTO_MARCA_RE.search(antes):
        return True
    if len(antes.strip()) < 12 and ini_linha > 0:  # agência logo no início da linha
        fim_prev = ini_linha - 1
        ini_prev = t.rfind("\n", 0, fim_prev) + 1
        if _FOTO_MARCA_RE.search(t[ini_prev:fim_prev][-80:]):
            return True
    return False


def agencia_assinada(corpo: str) -> Optional[str]:
    t = corpo or ""
    if not t.strip():
        return None
    janelas = [(t[:500], 0), (t[-700:], max(0, len(t) - 700))]
    for chave, rx in AGENCIAS.items():
        if chave in _SEMPRE_CREDITO:
            for m in re.finditer(rx, t, re.I):
                if not _e_credito_foto(t, m.start()):
                    return chave
            continue
        # "Investigado por: Reuters, AFP…" (rodapé do Comprova) NÃO é crédito de republicação:
        # só parênteses, "Fonte:", "Com informações da", "Conteúdo da", linha própria ou "— Agência" no fim.
        credito = (rf"(\(\s*(com\s+)?({rx})[^)]{{0,40}}\)|(fonte|com informa[çc][õo]es d[aeo]s?|"
                   rf"conte[úu]do d[ae]|texto d[ae])\s*:?\s*(a |o )?({rx})|^\s*({rx})\s*$|"
                   rf"[—–-]\s*({rx})\s*$)")
        for j, base in janelas:
            for m in re.finditer(credito, j, re.I | re.M):
                if not _e_credito_foto(t, base + m.start()):
                    return chave
    return None


# ----------------------------------------------------------------------------- peças
def fundir_por_url(pecas: Iterable[Dict[str, Any]]) -> tuple:
    """Funde peças com a mesma URL canônica (índice + web, ou duas afirmações).
    Retorna (unicas, fusoes[(url_mantida, url_fundida)]). Campos fundidos: `afs`
    (união), `origens` (união); veredito/corpo/trecho/data: o primeiro não vazio."""
    por: Dict[str, Dict[str, Any]] = {}
    ordem: List[str] = []
    fusoes: List[tuple] = []
    for p in pecas:
        k = url_canonica(p.get("url", ""))
        if not k:
            continue
        p.setdefault("afs", set())
        p.setdefault("origens", set())
        if k not in por:
            p["url_canonica"] = k
            por[k] = p
            ordem.append(k)
            continue
        a = por[k]
        a["afs"] = set(a.get("afs") or set()) | set(p.get("afs") or set())
        a["origens"] = set(a.get("origens") or set()) | set(p.get("origens") or set())
        for campo in ("veredito", "selo_original", "agencia", "afirmacao_checada", "corpo", "trecho",
                      "data_pub", "snippet", "titulo", "veiculo"):
            if not a.get(campo) and p.get(campo):
                a[campo] = p[campo]
        fusoes.append((a.get("url"), p.get("url")))
    return [por[k] for k in ordem], fusoes


def _texto(p: Dict[str, Any]) -> str:
    return p.get("corpo") or p.get("corpo_texto") or p.get("trecho_corpo") or ""


def agrupar(pecas: List[Dict[str, Any]], limiar_corpo: float = 0.8,
            limiar_titulo: float = 0.85) -> Dict[str, Any]:
    """Clusters de independência. Grava `cluster` e `motivo_cluster` em cada peça.
    Retorna {n, clusters: {id: [urls]}, motivos: {url: motivo}}."""
    n = len(pecas)
    pai = list(range(n))
    motivo: Dict[int, str] = {}

    def achar(i: int) -> int:
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    def unir(i: int, j: int, por: str) -> None:
        ri, rj = achar(i), achar(j)
        if ri != rj:
            pai[max(ri, rj)] = min(ri, rj)
            motivo.setdefault(max(i, j), por)

    agencias = [agencia_assinada(_texto(p)) for p in pecas]
    bases = [dominio_base(p.get("url", "")) for p in pecas]
    for i in range(n):
        for j in range(i):
            if agencias[i] and agencias[i] == agencias[j]:
                unir(i, j, f"mesma agência ({agencias[i]})")
            elif bases[i] and bases[i] == bases[j]:
                unir(i, j, f"mesmo grupo/domínio ({bases[i]})")
            else:
                ti, tj = _texto(pecas[i]), _texto(pecas[j])
                if ti and tj:
                    s = similaridade_corpo(ti, tj)
                    if s >= limiar_corpo:
                        unir(i, j, f"corpo quase idêntico ({s:.2f})")
                elif _similaridade(pecas[i].get("titulo", ""), pecas[j].get("titulo", "")) >= limiar_titulo:
                    unir(i, j, "título quase idêntico")
    grupos: Dict[int, List[int]] = {}
    for i in range(n):
        grupos.setdefault(achar(i), []).append(i)
    clusters: Dict[str, List[str]] = {}
    for raiz, membros in grupos.items():
        ag = next((agencias[m] for m in membros if agencias[m]), None)
        cid = f"agencia:{ag}" if ag else (bases[raiz] or f"url:{raiz}")
        if cid in clusters:  # colisão improvável (ex.: base vazia): desambigua
            cid = f"{cid}#{raiz}"
        clusters[cid] = [pecas[m].get("url", "") for m in membros]
        for m in membros:
            pecas[m]["cluster"] = cid
            pecas[m]["motivo_cluster"] = motivo.get(m, "" if m == raiz else "mesmo cluster")
    return {"n": len(clusters), "clusters": clusters,
            "motivos": {pecas[m].get("url", ""): mo for m, mo in motivo.items()}}


def contar_independentes(pecas: List[Dict[str, Any]], limiar_dup: float = 0.85) -> Dict[str, Any]:
    """Compat: {n, grupos[[urls]]} usando os mesmos clusters de `agrupar` (não muta)."""
    copia = [dict(p) for p in pecas]
    r = agrupar(copia, limiar_titulo=limiar_dup)
    return {"n": r["n"], "grupos": list(r["clusters"].values())}


def divergencias(pecas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Datas/vereditos diferentes ENTRE clusters (informação para o recibo)."""
    achados: List[Dict[str, Any]] = []
    por_cluster_data: Dict[str, str] = {}
    por_cluster_ver: Dict[str, str] = {}
    for p in pecas:
        c = p.get("cluster") or p.get("url", "")
        d = (p.get("data_pub") or "")[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}", d):
            por_cluster_data.setdefault(c, d)
        v = p.get("veredito")
        if v:
            por_cluster_ver.setdefault(c, v)
    datas = sorted(set(por_cluster_data.values()))
    if len(datas) > 1 and datas[0][:4] != datas[-1][:4]:
        achados.append({"campo": "data", "tipo": "divergencia", "valores": datas,
                        "leitura": "fontes apontam datas de anos diferentes: o conteúdo pode ser reciclado"})
    vers = sorted(set(por_cluster_ver.values()))
    if len(vers) > 1:
        achados.append({"campo": "veredito", "tipo": "divergencia", "valores": vers,
                        "leitura": "checadores diferentes deram selos diferentes"})
    return achados
