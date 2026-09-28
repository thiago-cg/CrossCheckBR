"""Agente de descoberta: roteador + ondas de busca + crítico, tudo com teto.

Nada de framework: um loop ReAct mínimo e determinístico (sem LLM extra —
o julgamento continua do LLM-juiz; o roteamento é por regras + laya onde
houver). Cada decisão vai para o traço, que o pipeline exibe no recibo.

Onda 1 (sempre): orgânico cru (recall) + filtro hosts de checagem.
Onda 1 (condicional): Scholar p/ saúde/ciência; News p/ tema quente.
Onda 2 (crítico): se nada de seção-checagem nem overlap forte, reformula
  com `related_searches` colhido de graça na onda 1 (1 busca extra).
Teto global: AGENTE_MAX_BUSCAS por /checar (nunca estoura o bolso).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import config
from . import serpapi_layer as camada

log = logging.getLogger("factcheck.agente")

_SAUDE = re.compile(
    r"\b(sa[úu]de|doen[çc]a|v[íi]rus|vacina|medicamento|rem[ée]dio|ibuprofeno|"
    r"paracetamol|dengue|covid|tratamento|cura|sintoma|m[ée]dico|hospital|"
    r"anvisa|sus|autismo|c[âa]ncer|diabetes|antibiotico|cloroquina|ivermectina)\b", re.I)
_QUENTE = re.compile(
    r"\b(hoje|ontem|agora|urgente|breaking|acaba de|últimas? horas?|elei[çc][ãa]o|"
    r"jogo|final|aprovad[oa]|sancionad[oa]|operação|pris[ãa]o|morte|acidente)\b", re.I)
_TOK = re.compile(r"[a-zà-ú0-9]{4,}")
_STOP = {"para", "como", "mais", "muito", "sobre", "entre", "quando", "foram",
         "serao", "sera", "será", "pode", "podem", "isso", "esta", "este", "está",
         "também", "ainda", "onde", "qual", "quais", "todo", "todos", "cada",
         "pela", "pelo"}


@dataclass
class BuscaPlanejada:
    engine: str
    q: Dict[str, Any]
    motivo: str
    wave: int


@dataclass
class TracoAgente:
    afirmacoes: List[str] = field(default_factory=list)
    buscas_usadas: int = 0
    ondas: int = 0
    decisoes: List[str] = field(default_factory=list)
    unicos: int = 0
    n_secao_checagem: int = 0

    def resumo(self) -> str:
        return (f"{self.buscas_usadas} busca(s) em {self.ondas} onda(s): "
                + "; ".join(self.decisoes[:6]))


def _toks(s: str) -> set:
    return {t for t in _TOK.findall((s or "").lower()) if t not in _STOP}


def classificar(afirmacao: str) -> Dict[str, bool]:
    """Roteador determinístico: saúde/ciência? tema quente?"""
    return {"saude": bool(_SAUDE.search(afirmacao or "")),
            "quente": bool(_QUENTE.search(afirmacao or ""))}


def planejar(afirmacao: str) -> Tuple[List[BuscaPlanejada], Dict[str, bool]]:
    """Plano onda 1 para UMA afirmação (onda 2 nasce do crítico)."""
    curta = (afirmacao or "").strip()[:200]
    rotulos = classificar(afirmacao)
    plano = [
        BuscaPlanejada("google",
                       {"q": curta, "hl": "pt-br", "gl": "br", "num": 10, "engine": "google"},
                       "recall máximo (snippets + datas)", 1),
        BuscaPlanejada("google",
                       camada.construir_queries_avancada(curta)[1],
                       "recall em hosts de checagem", 1),
    ]
    if rotulos["saude"]:
        plano.append(BuscaPlanejada(
            "google_scholar",
            {"q": curta, "hl": "pt", "num": 5, "engine": "google_scholar"},
            "literatura p/ afirmação de saúde/ciência (cited_by = autoridade)", 1))
    if rotulos["quente"]:
        plano.append(BuscaPlanejada(
            "google_news",
            {"q": curta, "hl": "pt-br", "gl": "br", "engine": "google_news"},
            "frescor p/ tema quente", 1))
    return plano, rotulos


def _colher(pload: Dict, af: str, engine: str, wave: int,
            sugestoes: List[str]) -> List[Dict[str, Any]]:
    """Normaliza resultados + top_stories; colhe related_searches (grátis)."""
    saidas = []
    chave = camada.RESULT_KEY.get(engine, "organic_results")
    for item in (pload.get(chave, []) or [])[:6]:
        n = camada.normalizar_item(item, engine=engine)
        n["_afirmacao"], n["_wave"] = af, wave
        saidas.append(n)
    if engine == "google":
        for item in (pload.get("top_stories", []) or [])[:4]:
            n = camada.normalizar_item(item, engine=engine, top_story=True)
            n["_afirmacao"], n["_wave"] = af, wave
            saidas.append(n)
        for rs in (pload.get("related_searches", []) or [])[:8]:
            q = (rs.get("query") or "").strip()
            if q and q not in sugestoes:
                sugestoes.append(q)
    return saidas


def _overlap(af: str, titulo: str) -> float:
    a, t = _toks(af), _toks(titulo)
    return len(a & t) / max(1, len(a)) if a else 0.0


def _critico(af: str, docs: List[Dict], sugestoes: List[str],
             feitas: set) -> Optional[str]:
    """Onda 2? Só se: zero seção-checagem E zero overlap forte E sugestão nova.

    Escolhe a related_search com mais tokens novos sobre a afirmação.
    """
    if any(d.get("_secao_checagem") for d in docs):
        return None
    if any(_overlap(af, d.get("titulo", "")) >= 0.6 for d in docs):
        return None
    base = _toks(af)
    melhor, melhor_novos = None, 0
    for s in sugestoes:
        if s in feitas:
            continue
        novos = len(_toks(s) - base)
        if novos >= 2 and novos > melhor_novos:
            melhor, melhor_novos = s, novos
    return melhor


def descobrir(afirmacoes: List[str], cliente, catalogo=None,
              teto: Optional[int] = None, cancel=None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Executa o agente (síncrono; pipeline chama via to_thread).

    Round-robin: onda 1 em todas as afirmações antes da onda 2 de qualquer
    uma (teto global não esfomeia as últimas). `cancel` (threading.Event):
    checado entre buscas — a thread órfã pós-timeout para sem gastar cota.
    Retorna (descobertas deduplicadas por URL, traço serializável).
    Nunca levanta: sem cliente ativo, volta vazio com decisão registrada.
    """
    teto = teto if teto is not None else getattr(config, "AGENTE_MAX_BUSCAS", 8)
    traco = TracoAgente(afirmacoes=list(afirmacoes))
    if cliente is None or not getattr(cliente, "ativo", False):
        traco.decisoes.append("sem SerpAPI: só índice local")
        return [], traco.__dict__

    def _cancelado() -> bool:
        try:
            return bool(cancel and cancel.is_set())
        except Exception:
            return False

    descobertas: List[Dict[str, Any]] = []
    unicas: List[Dict[str, Any]] = []  # antes do try: exceção no meio não vaza UnboundLocalError
    feitas: set = set()
    estourou_cap = False

    def _buscar(q: Dict[str, Any]) -> Dict[str, Any]:
        """1 busca contando gasto REAL (delta de uso_hoje: cache-hit e cap não gastam)."""
        nonlocal estourou_cap
        try:
            antes = int(cliente.uso_hoje)
        except Exception:
            antes = None
        bruto = cliente.buscar(q) or {}
        try:
            traco.buscas_usadas += max(0, int(cliente.uso_hoje) - (antes or 0))
        except Exception:
            traco.buscas_usadas += 1
        if getattr(cliente, "ultimo_motivo", "") == "cap" and not estourou_cap:
            estourou_cap = True
            traco.decisoes.append("teto diário SerpAPI: ondas restantes canceladas")
        return bruto

    try:
        # Passe 1: onda 1 intercalada por posição (nenhuma afirmação
        # esfomeia as outras: todas recebem a busca 1 antes de qualquer
        # busca 2).
        frentes: List[Tuple[str, List[BuscaPlanejada], Dict[str, bool], List[str]]] = []
        for af in afirmacoes:
            plano, rotulos = planejar(af)
            frentes.append((af, plano, rotulos, []))
        for af, plano, rotulos, _ in frentes:
            tags = [k for k, v in rotulos.items() if v]
            traco.decisoes.append(
                f"onda 1 [{af[:50]}…]: {len(plano)} buscas"
                + (f" (temas: {','.join(tags)})" if tags else ""))
        max_len = max([len(plano) for _, plano, _, _ in frentes] + [0])
        for i in range(max_len):
            if estourou_cap:
                break
            for af, plano, _, sugestoes in frentes:
                if i >= len(plano):
                    continue
                if estourou_cap:
                    break
                if _cancelado():
                    traco.decisoes.append(f"cancelado: {af[:40]}… (busca {i + 1} da onda 1)")
                    continue
                if traco.buscas_usadas >= teto:
                    traco.decisoes.append(f"teto {teto}: {af[:40]}… (busca {i + 1} da onda 1)")
                    continue
                b = plano[i]
                bruto = _buscar(b.q)
                feitas.add(b.q.get("q", ""))
                descobertas += _colher(bruto, af, b.engine, 1, sugestoes)
                traco.ondas = max(traco.ondas, 1)
        # Passe 2: crítico por afirmação (1 reformulação a partir do grátis).
        for af, _, _, sugestoes in frentes:
            if estourou_cap or _cancelado() or traco.buscas_usadas >= teto:
                break
            docs_af = [d for d in descobertas if d.get("_afirmacao") == af]
            if not docs_af:
                continue  # sem onda 1, sem onda 2
            nova_q = _critico(af, docs_af, sugestoes, feitas)
            if nova_q:
                traco.decisoes.append(f"onda 2: crítico reformulou → {nova_q[:60]}…")
                bruto = _buscar({"q": nova_q, "hl": "pt-br", "gl": "br",
                                 "num": 10, "engine": "google"})
                descobertas += _colher(bruto, af, "google", 2, sugestoes)
                traco.ondas = max(traco.ondas, 2)
            else:
                traco.decisoes.append(f"onda 2 dispensada [{af[:40]}…] (checagem ou overlap forte)")
        # Roteamento p/ catálogo (deep crawl com schema próprio) + dedupe.
        vistos, unicas = set(), []
        for d in descobertas:
            url = d.get("url", "")
            if url and url not in vistos:
                vistos.add(url)
                if catalogo is not None:
                    try:
                        camada.rotear_fonte(d, catalogo)
                    except Exception:
                        pass
                unicas.append(d)
        traco.unicos = len(unicas)
        traco.n_secao_checagem = sum(1 for d in unicas if d.get("_secao_checagem"))
    except Exception as e:  # agente nunca quebra o pipeline
        log.warning("agente falhou (parcial): %s", str(e)[:120])
        traco.decisoes.append(f"falha parcial: {str(e)[:80]}")
    return unicas, traco.__dict__
