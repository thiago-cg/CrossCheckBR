"""Corroboração: em quantos veículos INDEPENDENTES a informação aparece?

Republicação da mesma agência/texto conta 1x. Divergências saem campo a campo.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List


def _norm(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", texto).strip()


def _similaridade(a: str, b: str) -> float:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_SUFIXO_DUPLO = (".com.br", ".org.br", ".net.br", ".gov.br", ".edu.br",
                  ".co.uk", ".org.uk", ".com.ar", ".com.mx", ".com.pt")


def dominio_base(url: str) -> str:
    """Domínio registrável aprox. (grupo econômico): folha.uol.com.br e
    uol.com.br -> uol.com.br; g1.globo.com e globo.com -> globo.com."""
    try:
        from urllib.parse import urlparse as _up
        host = (_up(url or "").hostname or "").lower()
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


def contar_independentes(pecas: List[Dict[str, Any]], limiar_dup: float = 0.85) -> Dict[str, Any]:
    """Agrupa republicações E mesmo grupo econômico; retorna {n, grupos[[urls]]}.

    Mesmo domínio-base (ex: UOL + Folha) conta 1x — independência real, não só
    texto diferente (round B, loop 7).
    """
    grupos: List[List[Dict[str, Any]]] = []
    for p in pecas:
        texto = f"{p.get('titulo','')} {p.get('corpo_texto','') or ''}"
        base = dominio_base(p.get("url", ""))
        achou = False
        for g in grupos:
            ref = f"{g[0].get('titulo','')} {g[0].get('corpo_texto','') or ''}"
            mesma_base = base and base == dominio_base(g[0].get("url", ""))
            if mesma_base or _similaridade(texto, ref) >= limiar_dup:
                g.append(p)
                achou = True
                break
        if not achou:
            grupos.append([p])
    return {"n": len(grupos), "grupos": [[q.get("url", "") for q in g] for g in grupos]}


def divergencias(pecas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compara campos normalizados entre peças (datas, vereditos, atores)."""
    achados: List[Dict[str, Any]] = []
    datas = {(p.get("data_publicacao") or "")[:10] for p in pecas if p.get("data_publicacao")}
    if len(datas) > 1:
        achados.append({"campo": "data", "tipo": "divergencia", "valores": sorted(datas),
                        "leitura": "fontes apontam datas diferentes para o fato"})
    vereditos = {(p.get("veredito") or "") for p in pecas if p.get("veredito")}
    if len(vereditos) > 1:
        achados.append({"campo": "veredito", "tipo": "divergencia", "valores": sorted(vereditos),
                        "leitura": "checadores discordam entre si — pesa contra conclusão"})
    # Convergência exige 2+ peças COM veredito: 1 selo + N peças sem selo não é
    # convergência (evita contar o mesmo selo duas vezes, junto de "cobertura ampla").
    n_com_veredito = sum(1 for p in pecas if p.get("veredito"))
    if len(vereditos) == 1 and n_com_veredito >= 2:
        achados.append({"campo": "veredito", "tipo": "convergencia", "valores": sorted(vereditos),
                        "leitura": "checadores convergem para o mesmo veredito"})
    return achados
