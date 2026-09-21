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


def contar_independentes(pecas: List[Dict[str, Any]], limiar_dup: float = 0.85) -> Dict[str, Any]:
    """Agrupa republicações; retorna {n, grupos[[urls]]}."""
    grupos: List[List[Dict[str, Any]]] = []
    for p in pecas:
        texto = f"{p.get('titulo','')} {p.get('corpo_texto','') or ''}"
        achou = False
        for g in grupos:
            ref = f"{g[0].get('titulo','')} {g[0].get('corpo_texto','') or ''}"
            if _similaridade(texto, ref) >= limiar_dup:
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
