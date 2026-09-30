"""Agente de descoberta (busca aberta via SerpAPI) com o JUIZ no laço.

Fluxo (orquestrado por `pipeline.Pipeline`; este módulo não chama extração nem juiz):

  Onda 1 (determinística, por afirmação):
    (a) `consulta` da afirmação (palavras-chave do LLM de afirmações; fallback: termos
        de conteúdo do núcleo), ≤ 32 palavras;
    (b) a mesma consulta + `(site:… OR …)` das agências de checagem CURADAS do catálogo
        (`serpapi_layer.agencias_checagem`, domínio + aliases; nada de lista fixa).
    Ordem de gasto round-robin: (a) de todas as afirmações antes de qualquer (b). Buscas
    de uma onda correm em paralelo; a absorção dos resultados é na ordem do plano
    (determinística em replay).
  Pipeline: dedupe → seleção → extração → clusters → juiz (4 classes).
  Crítico (depois do juiz, por afirmação), com o resumo do julgamento:
    parar      se há veredito de checagem aplicável (selo com direção numa página que o
               juiz disse tratar da afirmação) ou ≥ 2 clusters independentes com postura
               (SUSTENTA/REFUTA); ou se o juiz não está disponível, o teto de buscas acabou
               ou a afirmação já gastou AGENTE_MAX_ONDAS_EXTRAS;
    nova_onda  caso contrário: a consulta é REESCRITA pelo LLM (finalidade "reformular")
               vendo a afirmação, as consultas já feitas e as páginas julgadas NAO_TRATA
               (o que evitar); fallback determinístico marcado + telemetria.fallback.
    As fontes novas passam por extração + juiz e somam às anteriores antes do `decidir()`.

Estado POR REQUISIÇÃO (`EstadoBusca`, criado a cada execução do pipeline): contador de
buscas próprio (nada de delta de contador compartilhado do cliente), dedupe por URL
canônica que ACUMULA afirmações (url → {afirmações}), corte por afirmação (round-robin
preservado), resultado parcial preservado em timeout.

Telemetria: evento `etapa` (nome `agente-onda`) por busca de cada onda — afirmação, query,
n resultados, n novos — e evento `agente` por decisão do crítico (parar|nova_onda, motivo,
contagens usadas). Todo I/O passa por `serpapi_layer.SerpAPIClient` (replay) e `llm`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from . import config, corroboracao, llm, selos, telemetria
from . import serpapi_layer as camada

log = logging.getLogger("factcheck.agente")

COM_POSTURA = ("SUSTENTA", "REFUTA")
TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")
ORGANICOS_POR_BUSCA = 8
TOP_STORIES_POR_BUSCA = 3
MOTOR_REFORMULAR_FALLBACK = "fallback-regex"


# ----------------------------------------------------------------------------- alvos
@dataclass
class Alvo:
    """Uma afirmação a buscar: `texto` é o núcleo (o que o juiz avalia)."""
    indice: int
    texto: str
    consulta: str


def consulta_padrao(texto: str) -> str:
    """Fallback da consulta: termos de conteúdo do núcleo (mesma regra do fallback de afirmações)."""
    from .afirmacoes import palavras_chave
    return palavras_chave(texto, 8) or (texto or "").strip()


def alvos_de(afs: Sequence[Any]) -> List[Alvo]:
    """Afirmacao (schema) ou str -> Alvo, preservando o índice da afirmação."""
    out = []
    for i, af in enumerate(afs):
        if isinstance(af, str):
            texto, consulta = af.strip(), ""
        else:
            texto = (af.alvo() if hasattr(af, "alvo") else str(af)).strip()
            consulta = (getattr(af, "consulta", "") or "").strip()
        out.append(Alvo(indice=i, texto=texto, consulta=consulta or consulta_padrao(texto)))
    return out


# ----------------------------------------------------------------------------- estado
@dataclass
class EstadoBusca:
    """Estado de UMA requisição. Nunca compartilhado entre execuções do pipeline."""
    teto: int
    max_extras: int = 1
    max_por_afirmacao: int = 12
    timeout_s: float = 60.0
    buscas: int = 0            # buscas emitidas por esta requisição (inclui cache do cliente)
    buscas_cache: int = 0      # das quais vieram do cache em memória do cliente
    ondas: int = 0
    parar_motivo: str = ""     # cap | timeout (nada mais é lançado)
    queries: Dict[int, List[str]] = field(default_factory=dict)
    extras: Dict[int, int] = field(default_factory=dict)
    docs: Dict[str, Dict[str, Any]] = field(default_factory=dict)       # url canônica -> doc
    ordem: Dict[int, List[str]] = field(default_factory=dict)           # afirmação -> [canônicas]
    onda_de: Dict[Tuple[str, int], int] = field(default_factory=dict)   # (canônica, af) -> onda
    resultados_q: Dict[str, "camada.ResultadoBusca"] = field(default_factory=dict)
    decisoes: List[str] = field(default_factory=list)
    criticas: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def novo(cls, teto: Optional[int] = None, max_extras: Optional[int] = None,
             timeout_s: Optional[float] = None) -> "EstadoBusca":
        return cls(teto=int(teto if teto is not None else getattr(config, "AGENTE_MAX_BUSCAS", 9)),
                   max_extras=int(max_extras if max_extras is not None
                                  else getattr(config, "AGENTE_MAX_ONDAS_EXTRAS", 1)),
                   max_por_afirmacao=int(getattr(config, "AGENTE_MAX_POR_AFIRMACAO", 12) or 12),
                   timeout_s=float(timeout_s if timeout_s is not None
                                   else getattr(config, "AGENTE_TIMEOUT_S", 60) or 60))

    def restante(self) -> int:
        return max(0, self.teto - self.buscas)

    def reservar(self, limite: Optional[int] = None) -> bool:
        """Consome 1 busca do teto da requisição (e do `limite` da onda, se houver)."""
        if self.parar_motivo or self.buscas >= self.teto:
            return False
        if limite is not None and self.buscas >= limite:
            return False
        self.buscas += 1
        return True

    def absorver(self, ai: int, onda: int, q: Dict[str, Any],
                 payload: Optional[Dict[str, Any]]) -> Tuple[int, int]:
        """Normaliza os resultados de 1 busca para a afirmação `ai`. -> (n resultados, n novos p/ `ai`)."""
        engine = str(q.get("engine") or "google")
        chave = camada.RESULT_KEY.get(engine, "organic_results")
        itens = [(it, False) for it in ((payload or {}).get(chave) or [])[:ORGANICOS_POR_BUSCA]]
        if engine == "google":
            itens += [(it, True) for it in ((payload or {}).get("top_stories") or [])[:TOP_STORIES_POR_BUSCA]]
        n, novos = 0, 0
        lista = self.ordem.setdefault(ai, [])
        for item, top in itens:
            if not isinstance(item, dict):
                continue
            d = camada.normalizar_item(item, engine=engine, top_story=top)
            canon = corroboracao.url_canonica(d.get("url", "")) if d.get("url") else ""
            if not canon:
                continue
            n += 1
            atual = self.docs.get(canon)
            if atual is None:
                d["_afirmacoes"], d["_queries"] = set(), []
                self.docs[canon] = atual = d
            if q.get("q") and q["q"] not in atual["_queries"]:
                atual["_queries"].append(q["q"])
            if ai not in atual["_afirmacoes"]:
                atual["_afirmacoes"].add(ai)
                self.onda_de[(canon, ai)] = onda
                lista.append(canon)
                novos += 1
        return n, novos

    def pecas(self, onda: Optional[int] = None) -> List[Dict[str, Any]]:
        """Docs únicos com `_afirmacoes` = afirmações para as quais o doc está dentro do corte
        POR AFIRMAÇÃO (`max_por_afirmacao`). Ordem round-robin entre afirmações.
        `onda`: só os pares (doc, afirmação) que entraram naquela onda."""
        filas: Dict[int, List[str]] = {}
        for ai, lista in sorted(self.ordem.items()):
            sel = [c for c in lista if onda is None or self.onda_de.get((c, ai)) == onda]
            filas[ai] = sel[: self.max_por_afirmacao]
        afs_de: Dict[str, set] = {}
        ordem: List[str] = []
        k = 0
        while any(k < len(f) for f in filas.values()):
            for ai, f in filas.items():
                if k < len(f):
                    c = f[k]
                    if c not in afs_de:
                        afs_de[c] = set()
                        ordem.append(c)
                    afs_de[c].add(ai)
            k += 1
        out = []
        for c in ordem:
            d = dict(self.docs[c])
            d["_afirmacoes"] = set(afs_de[c])
            d["_url_canonica"] = c
            out.append(d)
        return out

    def traco(self) -> Dict[str, Any]:
        return {"buscas": self.buscas, "buscas_cache": self.buscas_cache, "teto": self.teto,
                "ondas": self.ondas, "unicos": len(self.docs), "parar_motivo": self.parar_motivo,
                "por_afirmacao": {ai: len(v) for ai, v in sorted(self.ordem.items())},
                "queries": {ai: list(v) for ai, v in sorted(self.queries.items())},
                "extras": dict(self.extras), "decisoes": list(self.decisoes),
                "criticas": list(self.criticas)}

    def resumo(self) -> str:
        return (f"{self.buscas}/{self.teto} busca(s) em {self.ondas} onda(s), {len(self.docs)} URL(s) única(s)"
                + (f" [{self.parar_motivo}]" if self.parar_motivo else ""))


# ----------------------------------------------------------------------------- busca
def _buscar(cliente: Any, q: Dict[str, Any]) -> "camada.ResultadoBusca":
    """1 busca com motivo DESTA chamada. Fakes que sobrescrevem só `buscar` continuam valendo."""
    try:
        if (hasattr(cliente, "buscar_ex")
                and getattr(type(cliente), "buscar", None) is camada.SerpAPIClient.buscar):
            return cliente.buscar_ex(q)
        payload = cliente.buscar(q)
        motivo = "ok" if payload else (getattr(cliente, "ultimo_motivo", "") or "vazio")
        return camada.ResultadoBusca(payload, "ok" if payload else motivo)
    except Exception as e:  # cliente nunca derruba o agente
        telemetria.fallback("agente", f"busca falhou: {type(e).__name__}: {e}")
        return camada.ResultadoBusca(None, "erro")


async def _executar_onda(estado: EstadoBusca, cliente: Any,
                         plano: List[Tuple[Dict[str, Any], List[int], str]], onda: int,
                         limite: Optional[int] = None) -> int:
    """Executa um plano [(query, [afirmações], motivo)] na ordem dada (a ordem define quem
    recebe orçamento). Buscas em paralelo; timeout preserva o que já voltou. -> n buscas lançadas."""
    lancados: List[Tuple[Dict[str, Any], List[int], str, Optional[asyncio.Task]]] = []
    for q, ais, motivo in plano:
        if q["q"] in estado.resultados_q or any(q["q"] == l[0]["q"] for l in lancados):
            lancados.append((q, ais, motivo, None))  # mesma query nesta requisição: reusa, não gasta
            continue
        if not estado.reservar(limite):
            por = estado.parar_motivo or (f"teto {estado.teto}" if estado.buscas >= estado.teto
                                          else f"reserva p/ ondas extras (onda {onda} limitada a {limite})")
            estado.decisoes.append(f"onda {onda} af{ais}: {motivo} não buscada ({por})")
            telemetria.evento("etapa", nome="agente-onda", status="pulada", onda=onda, afirmacao=ais,
                              query=q["q"], motivo=motivo, n_resultados=0, n_novos=0, n_fontes=0,
                              detalhe=f"onda {onda} af{ais}: sem orçamento ({por})")
            continue
        lancados.append((q, ais, motivo, asyncio.create_task(asyncio.to_thread(_buscar, cliente, q))))
    tarefas = [t for *_, t in lancados if t is not None]
    if tarefas:
        _, pendentes = await asyncio.wait(tarefas, timeout=estado.timeout_s)
        if pendentes:
            estado.parar_motivo = "timeout"
            for t in pendentes:
                t.cancel()
            telemetria.fallback("agente", f"onda {onda}: teto {estado.timeout_s:.0f}s; {len(pendentes)} busca(s) "
                                          f"descartada(s), parcial preservado")
            estado.decisoes.append(f"onda {onda}: teto {estado.timeout_s:.0f}s ({len(pendentes)} busca(s) sem resposta)")
    for q, ais, motivo, t in lancados:  # absorção na ordem do plano (determinística)
        if t is None:
            res = estado.resultados_q.get(q["q"]) or camada.ResultadoBusca(None, "reuso")
            reuso = True
        else:
            reuso = False
            if t.done() and not t.cancelled() and t.exception() is None:
                res = t.result()
            else:
                res = camada.ResultadoBusca(None, "timeout")
            estado.resultados_q[q["q"]] = res
            if res.cache:
                estado.buscas_cache += 1
            if res.motivo == "cap" and estado.parar_motivo != "cap":
                estado.parar_motivo = "cap"
                estado.decisoes.append("teto diário SerpAPI: ondas restantes canceladas")
        for ai in ais:
            n, novos = estado.absorver(ai, onda, q, res.payload)
            qs = estado.queries.setdefault(ai, [])
            if q["q"] not in qs:
                qs.append(q["q"])
            status = "ok" if n else ("falha" if res.motivo in ("erro", "cap", "timeout") else "parcial")
            telemetria.evento("etapa", nome="agente-onda", status=status, onda=onda, afirmacao=ai,
                              query=q["q"], motivo=motivo, n_resultados=n, n_novos=novos, n_fontes=novos,
                              serpapi=res.motivo, cache=bool(res.cache), reuso=reuso,
                              detalhe=f"onda {onda} af{ai} ({motivo}): {novos} nova(s) de {n} resultado(s) "
                                      f"[{res.motivo}{', reuso' if reuso else ''}]")
    estado.ondas = max(estado.ondas, onda)
    return len(tarefas)


def plano_onda1(alvos: Sequence[Alvo], agencias: Optional[List[Dict[str, Any]]] = None
                ) -> List[Tuple[Dict[str, Any], List[int], str]]:
    """(a) consulta de todas as afirmações, depois (b) consulta + site: das agências."""
    agencias = agencias if agencias is not None else camada.agencias_checagem()
    a, b = [], []
    for al in alvos:
        a.append((camada._base(al.consulta, "google"), [al.indice], "consulta"))
        q_ag, fora = camada.query_agencias(al.consulta, agencias)
        if "site:" in q_ag["q"]:
            b.append((q_ag, [al.indice], "agencias" + (f" (-{len(fora)} fora do limite)" if fora else "")))
    return _agrupar_iguais(a + b)


def _agrupar_iguais(plano):
    """Mesma query para 2 afirmações = 1 busca atribuída às duas."""
    out: List[Tuple[Dict[str, Any], List[int], str]] = []
    idx: Dict[str, int] = {}
    for q, ais, motivo in plano:
        if q["q"] in idx:
            q0, ais0, m0 = out[idx[q["q"]]]
            out[idx[q["q"]]] = (q0, sorted(set(ais0) | set(ais)), m0)
        else:
            idx[q["q"]] = len(out)
            out.append((q, list(ais), motivo))
    return out


async def onda1(estado: EstadoBusca, cliente: Any, alvos: Sequence[Alvo],
                agencias: Optional[List[Dict[str, Any]]] = None) -> EstadoBusca:
    """Onda 1 com reserva: deixa `n_afirmações × max_extras` buscas para as ondas extras
    (nunca menos que 1 busca por afirmação na onda 1)."""
    if cliente is None or not getattr(cliente, "ativo", False):
        estado.decisoes.append("sem SerpAPI: nenhuma busca")
        return estado
    plano = plano_onda1(alvos, agencias)
    n = len(alvos)
    limite = max(min(estado.teto, n), estado.teto - n * max(0, estado.max_extras))
    if limite < len(plano):
        estado.decisoes.append(f"onda 1: {limite}/{len(plano)} busca(s) (reserva de {estado.teto - limite} "
                               f"p/ ondas extras)")
    await _executar_onda(estado, cliente, plano, 1, limite=estado.buscas + limite)
    return estado


async def onda_extra(estado: EstadoBusca, cliente: Any, pedidos: Sequence[Tuple[int, str, str]],
                     onda: int) -> EstadoBusca:
    """pedidos = [(afirmação, consulta reescrita, motor)] -> 1 busca por afirmação."""
    plano = []
    for ai, consulta, motor in pedidos:
        estado.extras[ai] = estado.extras.get(ai, 0) + 1
        plano.append((camada._base(consulta, "google"), [ai], f"reformulada ({motor})"))
    if plano and cliente is not None and getattr(cliente, "ativo", False):
        await _executar_onda(estado, cliente, _agrupar_iguais(plano), onda)
    return estado


# ----------------------------------------------------------------------------- crítico
@dataclass
class ResumoJulgamento:
    afirmacao: int
    n: Dict[str, int]
    clusters_postura: int
    veredito_aplicavel: bool
    fora_do_tema: List[Tuple[str, str]]  # (título, domínio) das páginas julgadas NAO_TRATA

    def contagens(self) -> Dict[str, Any]:
        return {**self.n, "clusters_postura": self.clusters_postura,
                "veredito_aplicavel": self.veredito_aplicavel}


def resumir_julgamento(ai: int, itens: Iterable[Dict[str, Any]]) -> ResumoJulgamento:
    """itens: {classe, cluster, url, veredito, titulo, dominio} das peças julgadas p/ a afirmação."""
    n = {"SUSTENTA": 0, "REFUTA": 0, "RELATA_SEM_ENDOSSO": 0, "NAO_TRATA": 0, "sem_juiz": 0}
    clusters, ver, fora = set(), False, []
    for it in itens:
        c = it.get("classe")
        if c in n:
            n[c] += 1
        else:
            n["sem_juiz"] += 1
        if c in COM_POSTURA:
            clusters.add(it.get("cluster") or it.get("url"))
        if c in TRATA and it.get("veredito") and selos.direcao(it.get("veredito")) != 0:
            ver = True
        if c == "NAO_TRATA":
            fora.append(((it.get("titulo") or "").strip()[:140], it.get("dominio") or ""))
    return ResumoJulgamento(afirmacao=ai, n=n, clusters_postura=len(clusters), veredito_aplicavel=ver,
                            fora_do_tema=fora)


def criticar(estado: EstadoBusca, resumo: ResumoJulgamento, juiz_disponivel: bool = True) -> Dict[str, Any]:
    """Decide, por afirmação, entre parar e gastar mais uma onda. Emite o evento `agente`."""
    ai = resumo.afirmacao
    if resumo.veredito_aplicavel:
        acao, motivo = "parar", "veredito de checagem aplicável"
    elif resumo.clusters_postura >= 2:
        acao, motivo = "parar", f"{resumo.clusters_postura} clusters independentes com postura"
    elif not juiz_disponivel:
        acao, motivo = "parar", "juiz indisponível: sem sinal para decidir outra onda"
    elif estado.extras.get(ai, 0) >= estado.max_extras:
        acao, motivo = "parar", f"máximo de ondas extras ({estado.max_extras}) atingido"
    elif estado.parar_motivo:
        acao, motivo = "parar", f"busca interrompida ({estado.parar_motivo})"
    elif estado.restante() <= 0:
        acao, motivo = "parar", f"teto de {estado.teto} buscas da requisição"
    else:
        acao = "nova_onda"
        n = resumo.n
        julgadas = sum(v for k, v in n.items() if k != "sem_juiz")
        if resumo.clusters_postura == 1:
            motivo = "só 1 cluster com postura (evidência suficiente pede 2)"
        elif not julgadas:
            motivo = "0 fontes julgadas (busca não trouxe candidatas)"
        elif n["RELATA_SEM_ENDOSSO"]:
            motivo = "0 posturas: só relatos sem endosso"
        else:
            motivo = "0 posturas: fontes fora do tema"
    d = {"afirmacao": ai, "decisao": acao, "motivo": motivo, "contagens": resumo.contagens(),
         "extras_feitas": estado.extras.get(ai, 0), "buscas_restantes": estado.restante()}
    estado.criticas.append(d)
    estado.decisoes.append(f"crítico af{ai}: {acao} ({motivo})")
    telemetria.evento("agente", **d)
    return d


# ----------------------------------------------------------------------------- reformulação
PROMPT_REFORMULAR = """Você ajuda a checar fatos buscando no Google. As buscas já feitas NÃO trouxeram fontes que confirmem ou desmintam a afirmação abaixo. Escreva UMA nova consulta de busca, em português, com 3 a 8 palavras-chave, que tenha mais chance de achar reportagens ou checagens sobre ESTA afirmação.

Regras:
- Mantenha as entidades centrais da afirmação (pessoas, órgãos, números, lugares, substâncias).
- Troque ou acrescente termos que um jornalista ou checador usaria: sinônimo, nome técnico, nome popular, órgão oficial, "é falso", "checagem".
- Não repita nenhuma das consultas já feitas.
- Não use operadores de busca (site:, aspas, OR, sinal de menos).
- Evite os assuntos das páginas fora do tema listadas: elas mostram para onde a busca derivou.

AFIRMAÇÃO: \"\"\"{afirmacao}\"\"\"

CONSULTAS JÁ FEITAS:
{feitas}

PÁGINAS JÁ VISTAS E FORA DO TEMA (título — domínio):
{fora}

Responda somente com JSON no formato {{"consulta": "..."}}"""


class _Reformulacao(BaseModel):
    consulta: str


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def limpar_consulta(q: str) -> str:
    """Tira operadores (site:, aspas, OR, -termo) e limita a 12 palavras."""
    q = re.sub(r"\bsite:\S+", " ", q or "", flags=re.I)
    q = re.sub(r"[\"“”()]", " ", q)
    q = re.sub(r"\bOR\b|\|", " ", q)
    q = re.sub(r"(^|\s)-\S+", " ", q)
    return " ".join(q.split()[:12])


def montar_prompt_reformular(afirmacao: str, feitas: Sequence[str], fora: Sequence[Tuple[str, str]]) -> str:
    return PROMPT_REFORMULAR.format(
        afirmacao=afirmacao.strip()[:600],
        feitas="\n".join(f"- {q}" for q in feitas) or "- (nenhuma)",
        fora="\n".join(f"- {t or '(sem título)'} — {d or '?'}" for t, d in list(fora)[:8]) or "- (nenhuma)")


def _fallback_reformular(alvo: Alvo, feitas: Sequence[str]) -> Optional[str]:
    termos = consulta_padrao(alvo.texto)
    feitas_n = {_norm(camada.limitar_palavras(q)) for q in feitas}
    for cand in (f"{termos} é verdade ou falso", f"{termos} checagem", f"{termos} falso"):
        if _norm(cand) not in feitas_n:
            return cand
    return None


def reformular(alvo: Alvo, feitas: Sequence[str], fora: Sequence[Tuple[str, str]],
               usar_llm: bool = True) -> Tuple[Optional[str], str]:
    """-> (consulta nova ou None, motor). LLM (finalidade "reformular") com JSON validado;
    falhou/desligado -> fallback determinístico marcado + telemetria.fallback. Nunca levanta."""
    feitas_n = {_norm(q) for q in feitas}

    def validar(dados: Any) -> Any:
        c = limpar_consulta(dados.consulta)
        if len(c.split()) < 2:
            raise ValueError("a consulta precisa ter pelo menos 2 palavras-chave")
        if _norm(c) in feitas_n:
            raise ValueError("a consulta repete uma das consultas já feitas; escreva uma diferente")
        dados.consulta = c
        return dados

    erro = "usar_llm=False"
    if usar_llm:
        res = llm.chat_json([{"role": "user", "content": montar_prompt_reformular(alvo.texto, feitas, fora)}],
                            _Reformulacao, finalidade="reformular", max_tokens=300, validar=validar)
        if res.ok and res.dados is not None:
            return res.dados.consulta, f"llm-reformular:{res.motor}"
        erro = res.erro or "LLM sem resposta válida"
    cand = _fallback_reformular(alvo, feitas)
    telemetria.fallback("agente.reformular", f"{erro[:200]} -> consulta determinística",
                        afirmacao=alvo.indice, consulta=cand)
    return cand, MOTOR_REFORMULAR_FALLBACK
