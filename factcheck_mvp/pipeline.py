"""Orquestrador. Nunca levanta: falha vira etapa + limitação (RF11).

Núcleo = CHECAGEM CRUZADA via busca aberta (fase 2):

  afirmações JSON {afirmacao, nucleo, polaridade, consulta}
   → [índice ClaimReview, só se INDICE_CHECAGENS=1]  +  busca aberta (SerpAPI, agente)
   → dedupe canônico (URL normalizada; índice+web = 1 peça)
   → seleção (teto JUIZ_MAX_NOTICIAS) → deep crawl (qualquer URL pública; ClaimReview da página)
   → clusters de independência (agência/grupo/corpo quase idêntico)
   → juiz de 4 classes com citação verificada (1 chamada por lote)
   → [SERP_ESTRATEGIA=agente] crítico pós-juiz por afirmação: parar (≥2 clusters com postura ou
     veredito aplicável) ou onda extra com consulta reescrita pelo LLM → extração → juiz (soma)
   → decisao.decidir (log-odds simétrico, 1 voto por cluster) → nível + why do mesmo objeto
  Estilo (BERTimbau/mock, padrões) roda à parte e NÃO entra no nível.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import asdict
import re
import unicodedata
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from . import (afirmacoes, confiabilidade, config, corroboracao, decisao, juiz_llm, padroes_llm, replay,
               selos, telemetria)
from . import agente as _agente
from .agregador import perguntas_guia
from .aprofundar import aprofundar
from .catalogo import Catalogo
from .indice import Indice
from .modelo_fake import DetectorFake, carregar_detector
from .schemas import (
    Afirmacao,
    EntradaConsulta,
    EtapaRecibo,
    FonteEvidencia,
    RelatorioChecagem,
    SinalAnalise,
)
from .serpapi_layer import SerpAPIClient, construir_queries, normalizar_item, rotear_fonte

log = logging.getLogger("factcheck.pipeline")
Progresso = Callable[[str], Awaitable[None]]

RUMOR_RE = re.compile(r"ouvi dizer|dizem que|me (contaram|mandaram|falaram|enviaram)|"
                      r"não tenho (certeza|fonte)|será que|boato|no zap|encaminhada", re.I)
OPINIAO_SATIRA_RE = re.compile(r"\b(opinião|opina|coluna|editorial|charge|humor|sátira|satire|"
                               r"acho que|na minha opinião|deveria|precisamos)\b", re.I)
# Claim vago (round C): comparativo sem métrica/período não é falsificável
# ("só piorou" — qual indicador? quando?). Pede especificação, não busca lixo.
VAGO_RE = re.compile(r"\b(piorou|piora|pior|melhorou|melhora|melhor|cada vez (pior|melhor))\b", re.I)
INDICADOR_RE = re.compile(
    r"\d|\b(pib|infla\w*|ipca|igp-?m|desemprego|emprego|renda|sal[aá]rio\w*|"
    r"juros|selic|d[oó]lar|c[âa]mbio|bolsa|ibovespa|pobreza|fome|crescimento|"
    r"recess\w*|d[ée]ficit|super[áa]vit|d[íi]vida|pontos?|por cento|%)\b", re.I)
GENERICA_RE = re.compile(r"^/(sobre/?|about/?|redes-sociais/?|contato/?)?$", re.I)
_SECOES = ("/fato-ou-fake", "/fato-ou-boato", "/estadao-verifica", "/confere", "/checagem",
           "/verificacao", "/lupa", "/comprova")


def _eh_generica(url: str, titulo: str = "", trecho: str = "") -> bool:
    """Homepage/seção institucional não é evidência (critic 1, loop 2). Usado também pelo bot."""
    try:
        from urllib.parse import urlparse as _up
        path = (_up(url or "").path or "/")
    except Exception:
        return True
    if GENERICA_RE.match(path or "/"):
        return True
    if path.endswith("/") and not re.search(r"\d{4}|\.htm|/noticia/.+/.+|/articles?/", path, re.I):
        seg = [s for s in path.strip("/").split("/") if s]
        if len(seg) <= 2 and len((trecho or "").strip()) < 200:
            return True
    raiz = "/" + path.strip("/")
    if raiz in ("/fato-ou-fake", "/fato-ou-boato", "/estadao-verifica", "/confere"):
        return True
    if len((trecho or "").strip()) < 200 and len((titulo or "").strip()) < 40:
        if re.search(r"^(sobre|home|início|redes sociais|boatos\.org)\b", (titulo or "").strip(), re.I):
            return True
    return False


def _eh_homepage(url: str) -> bool:
    """Filtro ANTES da leitura: só o inequívoco (raiz, /sobre, seção de checagem sem artigo)."""
    try:
        from urllib.parse import urlparse as _up
        path = (_up(url or "").path or "/")
    except Exception:
        return True
    if GENERICA_RE.match(path):
        return True
    seg = [x for x in path.strip("/").split("/") if x]
    if len(seg) == 1 and re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", seg[0], re.I):
        return True  # raiz com idioma (/en/, /pt-br/)
    return ("/" + path.strip("/").lower()) in _SECOES


async def _nada(_: str) -> None:
    return None


def _toks(s: str) -> set:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.findall(r"[a-z0-9]+", s) if len(t) >= 4}


def _overlap(afirmacao: str, titulo: str) -> float:
    """Triagem lexical barata p/ ORDENAR candidatas antes do juiz (0..1). Nunca decide postura."""
    ta, tt = _toks(afirmacao), _toks(titulo)
    return len(ta & tt) / max(1, len(ta))


def _veredito_tipado(valor: Optional[str], selo: Optional[str], agencia: Optional[str]) -> Optional[str]:
    """Enum de selos.VEREDITOS: o campo já tipado vale; senão normaliza o selo literal."""
    v = (valor or "").strip().upper()
    if v in selos.VEREDITOS:
        return v
    return selos.normalizar(selo or valor, agencia) if (selo or valor) else None


class Pipeline:
    def __init__(self, catalogo: Catalogo, indice_vereditos: Indice, indice_noticias: Indice,
                 serpapi: Optional[SerpAPIClient] = None, detector: Optional[DetectorFake] = None):
        self.catalogo = catalogo
        self.idx_ver = indice_vereditos   # atalho opcional: buscar_checagens (INDICE_CHECAGENS)
        self.idx_not = indice_noticias    # legado (amostras de homepages): não decide mais nada
        self.serpapi = serpapi or SerpAPIClient()
        self.detector = detector or carregar_detector()

    async def executar(self, entrada: EntradaConsulta, progresso: Progresso = _nada,
                       usar_llm: bool = True) -> RelatorioChecagem:
        """Envolve a checagem num run de telemetria (runs/<id>/trace.jsonl)."""
        from . import indice as _indice
        telemetria.iniciar_run({
            "entrada": entrada.conteudo[:500], "tipo": entrada.tipo, "usar_llm": usar_llm,
            "config": {"IO_MODO": replay.modo_atual(),
                       "SERP_ESTRATEGIA": getattr(config, "SERP_ESTRATEGIA", ""),
                       "SERP_ENGINE": getattr(config, "SERP_ENGINE", ""),
                       "serpapi_ativo": bool(getattr(self.serpapi, "ativo", False)),
                       "openrouter_ativo": bool(config.OPENROUTER_API_KEY),
                       "OPENROUTER_MODEL": config.OPENROUTER_MODEL,
                       "UNSLOTH_BASE_URL": config.UNSLOTH_BASE_URL,
                       "UNSLOTH_MODEL_NAME": config.UNSLOTH_MODEL_NAME or "(loaded=true de /models)",
                       "detector": getattr(self.detector, "nome", type(self.detector).__name__),
                       "INDICE_CHECAGENS": _indice.habilitado(),
                       "TRAFEGO_LISTA": confiabilidade.metadados().get("lista"),
                       "TRAFEGO_RANK_MAX": config.TRAFEGO_RANK_MAX,
                       "AGENTE_MAX_BUSCAS": getattr(config, "AGENTE_MAX_BUSCAS", None),
                       "AGENTE_MAX_ONDAS_EXTRAS": getattr(config, "AGENTE_MAX_ONDAS_EXTRAS", None),
                       "JUIZ_MAX_NOTICIAS": config.JUIZ_MAX_NOTICIAS, "JUIZ_LOTE": config.JUIZ_LOTE,
                       "JUIZ_TRECHO_MAX": config.JUIZ_TRECHO_MAX,
                       "LLM_DAILY_CAP": config.LLM_DAILY_CAP}})
        try:
            rel = await self._executar(entrada, progresso, usar_llm)
        except BaseException as e:
            telemetria.evento("erro", onde="pipeline", erro=f"{type(e).__name__}: {e}")
            telemetria.finalizar_run(None, erro=f"{type(e).__name__}: {e}"[:500])
            raise
        for f in rel.fontes:  # rastro das fontes que chegaram ao relatório
            telemetria.evento("fonte", url=f.url, estagio="relatorio", tipo_fonte=f.tipo_fonte,
                              decisao="descartada" if f.relevante is False else "mantida",
                              motivo=(f"juiz: {f.postura}" if f.postura else "não julgada"),
                              cluster=f.cluster, curada=f.curada, corpo_lido=f.corpo_lido,
                              confianca=f.confianca, confiabilidade=f.confiabilidade)
        telemetria.finalizar_run(rel.model_dump(mode="json"))
        return rel

    # ------------------------------------------------------------------ etapas auxiliares
    def _catalogar(self, p: Dict[str, Any]) -> None:
        """Catálogo pela URL (domínio + aliases); curada = portal curado (não candidato)."""
        portal = None
        try:
            portal = self.catalogo.por_url(p.get("url", ""))
        except Exception:
            portal = None
        p["curada"] = bool(portal) and bool(self.catalogo.eh_curado(portal))
        p["portal_id"] = (portal or {}).get("id")
        p["tipo_portal"] = (portal or {}).get("tipo") or ""
        if portal and portal.get("nome"):
            p["veiculo"] = portal["nome"]
        p["dominio"] = corroboracao.dominio(p.get("url", ""))
        # Curada > plataforma (rede social/UGC) > institucional > muito acessada (Tranco) > pouco acessada
        p["confiabilidade"] = confiabilidade.classificar(p.get("url", ""), p["curada"])

    async def _buscar_web(self, afs: List[Afirmacao], avisar, etapa, limitacoes
                          ) -> Tuple[List[Dict[str, Any]], Optional["_agente.EstadoBusca"]]:
        """SerpAPI (agente ou estático) com a CONSULTA de cada afirmação. -> (peças web, estado do agente)."""
        if (getattr(config, "SERP_ESTRATEGIA", "") or "").strip().lower() == "agente":
            await avisar("Agente de busca: onda 1…")
            estado = _agente.EstadoBusca.novo()  # POR REQUISIÇÃO: nada compartilhado entre consultas
            alvos = _agente.alvos_de(afs[: config.MAX_AFIRMACOES])
            await _agente.onda1(estado, self.serpapi, alvos)
            if estado.parar_motivo == "timeout":
                limitacoes.append(f"Agente de busca atingiu o teto de {estado.timeout_s:.0f}s na onda 1 "
                                  "(resultado parcial preservado).")
            docs = estado.pecas()
            etapa("descoberta-agente", "ok" if docs else "parcial",
                  f"Agente onda 1: {estado.resumo()}; URLs por afirmação {estado.traco()['por_afirmacao']}. "
                  f"Consultas (+ site: das agências do catálogo): {[a.consulta for a in alvos]}. "
                  + " | ".join(estado.decisoes[:4]),
                  [d.get("url", "") for d in docs[:5]])
            return [self._peca_web(d, d["_afirmacoes"]) for d in docs if d.get("url")], estado
        consultas: List[str] = []
        for af in afs[: config.MAX_AFIRMACOES]:
            q = (af.consulta or af.alvo()).strip()
            if q and q not in consultas:
                consultas.append(q)
        brutas: List[Dict[str, Any]] = []
        async def _q(consulta: str, q: Dict[str, Any]) -> List[Dict[str, Any]]:
            bruto = await asyncio.to_thread(self.serpapi.buscar, q)
            eng = self.serpapi.engine_de(q)
            saida = []
            for item in (bruto or {}).get(self.serpapi.result_key(q), [])[:6]:
                d = rotear_fonte(normalizar_item(item, engine=eng), self.catalogo)
                d["_afirmacao"] = consulta
                saida.append(d)
            return saida
        try:
            blocos = await asyncio.wait_for(asyncio.gather(
                *[_q(c, q) for c in consultas for q in construir_queries(c)]), timeout=60)
            for b in blocos:
                brutas += b
        except asyncio.TimeoutError:
            telemetria.fallback("serpapi", "teto 60s da busca estática")
            limitacoes.append("Descoberta web atingiu o teto de 60s (resultado parcial).")
        pecas = []
        for d in brutas:
            if not d.get("url"):
                continue
            ais = {i for i, af in enumerate(afs) if (af.consulta or af.alvo()).strip() == d.get("_afirmacao")}
            pecas.append(self._peca_web(d, ais or {0}))
        return pecas, None

    @staticmethod
    def _peca_web(d: Dict[str, Any], ais: set) -> Dict[str, Any]:
        return {"url": d["url"], "titulo": d.get("titulo") or "", "snippet": d.get("_snippet") or "",
                "veiculo": (d.get("fonte") or {}).get("nome", ""), "afs": set(ais),
                "origens": {"web"}, "data_pub": d.get("data_publicacao"),
                "scholar": bool(d.get("_scholar"))}

    # ------------------------------------------------------------------ principal
    async def _executar(self, entrada: EntradaConsulta, progresso: Progresso = _nada,
                        usar_llm: bool = True) -> RelatorioChecagem:
        from . import indice as _indice
        etapas: List[EtapaRecibo] = []
        limitacoes: List[str] = []

        def etapa(nome: str, status: str, detalhe: str = "", fontes_urls: Optional[List[str]] = None):
            etapas.append(EtapaRecibo(nome=nome, status=status, detalhe=detalhe, fontes=fontes_urls or []))
            telemetria.evento("etapa", nome=nome, status=status, detalhe=detalhe,
                              n_fontes=len(fontes_urls or []))

        async def avisar(msg: str) -> None:
            try:
                await progresso(msg)
            except Exception:
                pass  # callback de UI nunca quebra o pipeline (RF11)

        # 1. Recebimento + marcas de rumor/opinião/vago
        texto_base = entrada.conteudo.strip()
        eh_rumor = bool(RUMOR_RE.search(texto_base))
        eh_opiniao = bool(OPINIAO_SATIRA_RE.search(texto_base[:500]))
        eh_vago = bool(VAGO_RE.search(texto_base[:500])) and not INDICADOR_RE.search(texto_base[:800])
        if eh_rumor:
            limitacoes.append("Relato de segunda-mão sem fonte verificável: busca feita sobre o núcleo factual.")
        if eh_opiniao:
            limitacoes.append("Texto com marcas de opinião/sátira: não classificável como fato sem checagem aplicável.")
        if eh_vago:
            limitacoes.append("Afirmação vaga (comparativo sem indicador nem período: ex PIB, inflação, desemprego + datas): "
                              "diga qual métrica e qual período que eu checo de novo.")
        etapa("recebimento", "ok", f"Entrada do tipo {entrada.tipo} ({len(texto_base)} caracteres).")
        await avisar("Recebi. Extraindo as afirmações verificáveis…")

        # 2. Afirmações (JSON, polaridade preservada; fallback determinístico marcado)
        try:
            afs, motor_af = await asyncio.to_thread(afirmacoes.extrair_afirmacoes, texto_base, 0, usar_llm)
        except Exception as e:  # pragma: no cover
            telemetria.fallback("afirmacoes", f"{type(e).__name__}: {e}")
            afs, motor_af = afirmacoes.extrair_afirmacoes(texto_base, max_n=0, usar_llm=False)
        afs = afs[: config.MAX_AFIRMACOES]
        etapa("afirmacoes", "ok" if afs else "falha",
              f"{len(afs)} afirmação(ões) via {motor_af}: "
              + " | ".join(f"[{a.polaridade}] {a.texto!r} (núcleo {a.alvo()!r}; consulta {a.consulta!r})"
                           for a in afs))
        if not afs:
            ev0 = decisao.Evidencias(afirmacoes=[], vago=eh_vago, opiniao=eh_opiniao,
                                     rumor=eh_rumor)
            telemetria.evento("evidencias", **asdict(ev0))
            dec = decisao.decidir(ev0)
            dec.motivo = "nenhuma afirmação factual encontrada no texto"
            self._emitir_decisao(dec)
            return self._relatorio(entrada, dec, [], [], etapas,
                                   limitacoes + ["Nenhuma afirmação factual encontrada."])
        await avisar(f"Buscando fontes sobre {len(afs)} afirmação(ões)…")

        # 3. Atalho opcional: índice de checagens (ClaimReview/RSS)
        pecas: List[Dict[str, Any]] = []
        estado_agente: Optional[_agente.EstadoBusca] = None
        if _indice.habilitado() and hasattr(self.idx_ver, "buscar_checagens"):
            n_hits = 0
            for ai, af in enumerate(afs):
                try:
                    hits = self.idx_ver.buscar_checagens(af.alvo(), k=5)
                except Exception as e:
                    telemetria.fallback("indice", f"{type(e).__name__}: {e}")
                    hits = []
                for h in hits:
                    if not h.get("url"):
                        continue
                    n_hits += 1
                    pecas.append({"url": h["url"], "titulo": h.get("titulo") or h.get("afirmacao_checada") or "",
                                  "veiculo": h.get("agencia_nome") or h.get("agencia") or "",
                                  "afs": {ai}, "origens": {"indice"}, "trecho": h.get("trecho") or "",
                                  "data_pub": h.get("data_pub"), "agencia": h.get("agencia"),
                                  "selo_original": h.get("selo_original"),
                                  "afirmacao_checada": h.get("afirmacao_checada"),
                                  "veredito": _veredito_tipado(h.get("veredito"), h.get("selo_original"),
                                                               h.get("agencia")),
                                  "origem_veredito": "indice", "score_rel": h.get("score_rel")})
            etapa("base-checagem", "ok" if n_hits else "parcial",
                  f"{n_hits} checagem(ns) no índice (atalho INDICE_CHECAGENS=1).",
                  [p["url"] for p in pecas[:5]])
        else:
            etapa("base-checagem", "pulada", "Atalho do índice desligado (INDICE_CHECAGENS=0): núcleo = busca aberta.")

        # 4. Busca aberta (SerpAPI)
        if self.serpapi.ativo:
            await avisar("Consultando a web…")
            pecas_web, estado_agente = await self._buscar_web(afs, avisar, etapa, limitacoes)
            pecas += pecas_web
        else:
            etapa("descoberta", "pulada", "SERPAPI_KEY ausente: sem busca aberta.")
            limitacoes.append("Descoberta web desligada (sem SERPAPI_KEY).")

        # 5. Dedupe canônico global (antes do juiz): mesma URL do índice e da web = 1 peça
        pecas, fusoes = corroboracao.fundir_por_url(pecas)
        for mantida, fundida in fusoes:
            telemetria.evento("fonte", url=fundida, estagio="dedupe", decisao="fundida",
                              motivo=f"mesma URL canônica de {mantida}")
        uteis = []
        for p in pecas:
            if _eh_homepage(p["url"]):
                telemetria.evento("fonte", url=p["url"], estagio="dedupe", decisao="descartada",
                                  motivo="homepage/seção (não é artigo)")
                continue
            self._catalogar(p)
            uteis.append(p)
        pecas = uteis
        if self.serpapi.ativo:
            etapa("descoberta", "ok" if pecas else "parcial",
                  f"{len(pecas)} peça(s) única(s) após dedupe ({len(fusoes)} fundida(s)); "
                  f"{sum(1 for p in pecas if p.get('curada'))} de fonte curada."
                  + (f" [serpapi: {getattr(self.serpapi, 'ultimo_motivo', '')}]" if not pecas else ""),
                  [p["url"] for p in pecas[:5]])
            if not pecas:
                motivo = getattr(self.serpapi, "ultimo_motivo", "vazio")
                if motivo == "cap":
                    limitacoes.append(f"SerpAPI pausada: teto diário atingido "
                                      f"({getattr(config, 'SERPAPI_DAILY_CAP', 100)}/dia).")
                elif motivo == "erro":
                    limitacoes.append("SerpAPI falhou nesta consulta (detalhe no log do servidor).")
                else:
                    limitacoes.append("SerpAPI sem resultados para as afirmações.")

        # 6. Seleção p/ juiz (teto) + deep crawl das selecionadas
        selecionados = self._selecionar(afs, pecas)
        n_lidas, n_alvo, selecionados = await self._ler(afs, pecas, selecionados, avisar)
        if n_alvo:
            etapa("deep-crawl", "ok" if n_lidas else "parcial",
                  f"{n_lidas}/{n_alvo} página(s) com corpo lido; "
                  f"{sum(1 for p in pecas if p.get('origem_veredito') == 'pagina')} com ClaimReview.",
                  [p["url"] for p in pecas if p.get("corpo")][:5])

        # 7. Clusters de independência (agência / grupo / corpo quase idêntico)
        grupos = corroboracao.agrupar(pecas)
        for p in pecas:
            if p.get("motivo_cluster"):
                telemetria.evento("fonte", url=p["url"], estagio="dedupe", decisao="agrupada",
                                  motivo=f"cluster {p['cluster']}: {p['motivo_cluster']}")
        divs = corroboracao.divergencias([p for p in pecas if p.get("veredito") or p.get("data_pub")])
        for dv in divs:
            limitacoes.append(f"Divergência de {dv['campo']} entre fontes: {dv['leitura']} "
                              f"({'; '.join(dv['valores'][:4])}).")
        etapa("corroboracao", "ok", f"{grupos['n']} grupo(s) independente(s) entre {len(pecas)} peça(s).")

        # 8. Juiz de 4 classes (citação verificada), por afirmação
        julg: Dict[Tuple[int, int], Dict[str, Any]] = {}
        juiz_ok = False
        if selecionados:
            await avisar("Julgando o que cada fonte diz sobre a afirmação…")
            julg, juiz_ok = await self._julgar(afs, pecas, selecionados, usar_llm)
            n = {k: sum(1 for r in julg.values() if r.get("classe") == k) for k in juiz_llm.CLASSES}
            n_sem = sum(1 for r in julg.values() if r.get("classe") is None)
            n_reb = sum(1 for r in julg.values() if r.get("rebaixado"))
            motor_j = next((r.get("motor") for r in julg.values() if r.get("classe")), juiz_llm.MOTOR_FALLBACK)
            etapa("juiz", "ok" if juiz_ok and not n_sem else ("parcial" if juiz_ok else "falha"),
                  f"{len(julg)} julgamento(s) via {motor_j}: {n['SUSTENTA']} sustentam, {n['REFUTA']} refutam, "
                  f"{n['RELATA_SEM_ENDOSSO']} só relatam, {n['NAO_TRATA']} fora do tema "
                  f"({n_reb} rebaixada(s) por citação não verificada); {n_sem} sem julgamento.",
                  [pecas[pi]["url"] for (pi, _), r in julg.items() if r.get("classe") in juiz_llm.COM_POSTURA][:5])
            if not juiz_ok:
                limitacoes.append("Sem julgamento de conteúdo (LLM-juiz indisponível): "
                                  "o nível fica indeterminado; as fontes estão listadas para comparação.")
        else:
            etapa("juiz", "pulada", "Nenhuma fonte candidata para julgar.")

        # 8b. Agente: crítico PÓS-JUIZ decide, por afirmação, se gasta uma onda extra
        if estado_agente is not None:
            n_lidas += await self._ondas_extras(afs, pecas, julg, selecionados, juiz_ok, estado_agente,
                                                usar_llm, avisar, etapa, limitacoes)
            juiz_ok = juiz_ok or any(r.get("classe") is not None for r in julg.values())
            if pecas and "SerpAPI sem resultados para as afirmações." in limitacoes:
                limitacoes.remove("SerpAPI sem resultados para as afirmações.")  # a onda extra achou

        # 9. Descoberta de catálogo: só PROPÕE (nunca vira curada durante a consulta)
        await self._propor_catalogo(pecas, julg, etapa)

        # 10. Estilo do texto (fora do nível): modelo + padrões
        await avisar("Analisando o estilo do texto…")
        sinais_estilo = await self._estilo(texto_base, usar_llm, etapa, limitacoes)

        # 11. Decisão única
        itens = []
        for (pi, ai), r in julg.items():
            p = pecas[pi]
            itens.append(decisao.ItemEvidencia(
                url=p["url"], afirmacao=ai, cluster=p.get("cluster") or p["url"], classe=r.get("classe"),
                motor=r.get("motor") or "", citacao_verificada=r.get("citacao_verificada"),
                curada=bool(p.get("curada")), corpo_lido=bool(p.get("corpo")), veredito=p.get("veredito"),
                origem_veredito=p.get("origem_veredito"), veiculo=p.get("veiculo") or p.get("dominio") or "",
                confiabilidade=p.get("confiabilidade")))
        ev = decisao.Evidencias(
            afirmacoes=[decisao.AfirmacaoDecisao(texto=a.texto, nucleo=a.alvo(), polaridade=a.polaridade)
                        for a in afs],
            itens=itens, vago=eh_vago, opiniao=eh_opiniao, rumor=eh_rumor, juiz_disponivel=juiz_ok,
            n_lidas=n_lidas, n_consultadas=len(pecas))
        telemetria.evento("evidencias", **asdict(ev))
        dec = decisao.decidir(ev)
        self._emitir_decisao(dec)
        etapa("agregacao", "ok", f"propensão {dec.nivel} (L={dec.log_odds:+.2f}, p={dec.prob:.2f}): {dec.motivo}.")

        fontes = self._fontes(afs, pecas, julg, dec)
        sinais = self._sinais(dec) + sinais_estilo
        return self._relatorio(entrada, dec, sinais, fontes, etapas, limitacoes)

    # ------------------------------------------------------------------ seleção / juiz
    async def _ler(self, afs: List[Afirmacao], pecas: List[Dict[str, Any]],
                   selecionados: List[Tuple[int, int]], avisar) -> Tuple[int, int, List[Tuple[int, int]]]:
        """Deep crawl das peças selecionadas ainda sem corpo. -> (n lidas, n alvo, selecionados sem genéricas)."""
        alvo_crawl: List[Dict[str, Any]] = []
        vistos = set()
        for pi, ai in selecionados:
            p = pecas[pi]
            if p["url"] not in vistos and not p.get("corpo"):
                vistos.add(p["url"])
                alvo_crawl.append({"url": p["url"], "titulo": p.get("titulo", ""), "_afirmacao": afs[ai].alvo()})
        alvo_crawl = alvo_crawl[: max(1, config.DEEP_CRAWL_TOTAL or 10)]
        n_lidas = 0
        if not alvo_crawl:
            return 0, 0, selecionados
        await avisar("Lendo o corpo das fontes…")
        try:
            corpos = await aprofundar(alvo_crawl, self.catalogo,
                                      por_afirm=math.ceil(len(alvo_crawl) / 2),
                                      budget_total=max(config.DEEP_CRAWL_TIMEOUT_S or 15, 25))
        except Exception as e:
            telemetria.fallback("deep-crawl", f"{type(e).__name__}: {e}")
            corpos = {}
        por_url = {p["url"]: p for p in pecas}
        for url, c in corpos.items():
            p = por_url.get(url)
            if not p or c is None:
                continue
            if getattr(c, "corpo_lido", False):
                p["corpo"] = getattr(c, "texto_completo", "") or c.trecho_corpo
                p["trecho_juiz"] = c.trecho_corpo  # início + parágrafos com termos/conclusão
                p["metodo"] = getattr(c, "metodo", "")
                n_lidas += 1
            if getattr(c, "titulo", "") and not p.get("titulo"):
                p["titulo"] = c.titulo
            if getattr(c, "data_pub", None) and not p.get("data_pub"):
                p["data_pub"] = c.data_pub
            vp = getattr(c, "veredito_pagina", None)
            if vp and not p.get("veredito"):
                v = _veredito_tipado(vp.get("veredito"), vp.get("selo_original"), vp.get("agencia"))
                if v:
                    p.update(veredito=v, selo_original=vp.get("selo_original"),
                             afirmacao_checada=vp.get("afirmacao_checada"), origem_veredito="pagina",
                             agencia=vp.get("agencia"))
        # Homepage/seção que só se revela depois de lida (corpo de boilerplate): fora do juiz
        sel_pis = {pi for pi, _ in selecionados}
        genericas = {i for i, p in enumerate(pecas)
                     if p.get("corpo") and _eh_generica(p["url"], p.get("titulo", ""), p["corpo"])}
        for i in genericas & sel_pis:
            telemetria.evento("fonte", url=pecas[i]["url"], estagio="deep-crawl", decisao="descartada",
                              motivo="página genérica (homepage/seção) após leitura")
        return n_lidas, len(alvo_crawl), [(pi, ai) for pi, ai in selecionados if pi not in genericas]

    def _selecionar(self, afs: List[Afirmacao], pecas: List[Dict[str, Any]],
                    excluir: Optional[set] = None, afs_alvo: Optional[set] = None) -> List[Tuple[int, int]]:
        """Pares (peça, afirmação) p/ o juiz, até JUIZ_MAX_NOTICIAS, round-robin por afirmação.
        Ordem por afirmação: overlap (título+snippet × núcleo+consulta), selo, fonte curada e
        confiabilidade (muito acessada/institucional sobe; rede social e site pouco acessado descem).
        `excluir`: pares já julgados; `afs_alvo`: só estas afirmações (onda extra do agente)."""
        teto = max(0, config.JUIZ_MAX_NOTICIAS or 10)
        excluir = excluir or set()
        filas: List[List[int]] = []
        for ai, af in enumerate(afs):
            ref = f"{af.alvo()} {af.consulta}"
            if afs_alvo is not None and ai not in afs_alvo:
                filas.append([])
                continue
            cands = [i for i, p in enumerate(pecas) if ai in (p.get("afs") or {0}) and (i, ai) not in excluir]
            cands.sort(key=lambda i: (_overlap(ref, f"{pecas[i].get('titulo','')} {pecas[i].get('snippet','')}")
                                      + (0.3 if pecas[i].get("veredito") else 0)
                                      + (0.15 if pecas[i].get("curada") else 0)
                                      + (0.1 if pecas[i].get("tipo_portal") == "checagem" else 0)
                                      + confiabilidade.BONUS_SELECAO.get(pecas[i].get("confiabilidade") or "", 0.0)),
                       reverse=True)
            filas.append(cands)
        saida: List[Tuple[int, int]] = []
        k = 0
        while len(saida) < teto and any(k < len(f) for f in filas):
            for ai, f in enumerate(filas):
                if k < len(f) and len(saida) < teto:
                    saida.append((f[k], ai))
            k += 1
        return saida

    async def _julgar(self, afs, pecas, selecionados, usar_llm) -> Tuple[Dict[Tuple[int, int], Dict], bool]:
        por_af: Dict[int, List[int]] = {}
        for pi, ai in selecionados:
            por_af.setdefault(ai, []).append(pi)
        for ai in list(por_af):
            por_af[ai] = sorted(por_af[ai], key=lambda pi: (str(pecas[pi].get("url") or ""), pi))
        itens_por_af: Dict[int, List[Dict[str, Any]]] = {}
        for ai in sorted(por_af):
            pis = por_af[ai]
            alvo = afs[ai].alvo()
            itens = []
            for pi in pis:
                p = pecas[pi]
                corpo = p.get("trecho_juiz") or p.get("corpo") or p.get("trecho") or p.get("snippet") or ""
                vp = None
                if p.get("veredito") or p.get("selo_original"):
                    vp = {"selo": p.get("selo_original") or p.get("veredito"),
                          "alegacao_checada": p.get("afirmacao_checada") or ""}
                tipo = ("checagem" if (p.get("tipo_portal") == "checagem" or p.get("veredito")) else
                        "artigo acadêmico" if p.get("scholar") else
                        "notícia (veículo do catálogo)" if p.get("curada") else "site fora do catálogo")
                itens.append({"veiculo": p.get("veiculo") or "", "dominio": p.get("dominio") or "",
                              "tipo_fonte": tipo, "data": p.get("data_pub") or "",
                              "titulo": p.get("titulo") or "", "veredito_pagina": vp,
                              "trecho": juiz_llm.montar_trecho(p.get("titulo") or "", corpo, alvo)})
            itens_por_af[ai] = itens

        def _rodar() -> Dict[int, List[Dict[str, Any]]]:
            return {ai: juiz_llm.julgar(afs[ai].alvo(), its) for ai, its in itens_por_af.items()}

        resultados: Dict[int, List[Dict[str, Any]]] = {}
        if usar_llm:
            try:
                resultados = await asyncio.wait_for(asyncio.to_thread(_rodar),
                                                    timeout=config.JUIZ_TIMEOUT_TOTAL_S or 600)
            except asyncio.TimeoutError:
                telemetria.fallback("juiz", f"teto {config.JUIZ_TIMEOUT_TOTAL_S}s: julgamento descartado")
                resultados = {}
        else:
            telemetria.fallback("juiz", "usar_llm=False: sem julgamento de conteúdo")
        julg: Dict[Tuple[int, int], Dict[str, Any]] = {}
        juiz_ok = False
        for ai, pis in por_af.items():
            rs = resultados.get(ai) or [juiz_llm._vazio("sem julgamento (LLM desligado/indisponível/teto)")
                                        for _ in pis]
            if all(r.get("classe") is None for r in rs) and ai in resultados:
                telemetria.fallback("juiz", (rs[0].get("erro") or "juiz falhou")[:300], afirmacao=afs[ai].texto)
            for pi, r, it in zip(pis, rs, itens_por_af[ai]):
                julg[(pi, ai)] = r
                if r.get("classe") is not None:
                    juiz_ok = True
                p = pecas[pi]
                if r.get("classe") is None:
                    dec_f, mot = "nao-julgada", f"sem juiz: {r.get('erro')}"
                elif r.get("rebaixado"):
                    dec_f, mot = "rebaixada", (f"{r.get('classe_original')} -> NAO_TRATA: {r.get('erro')}; "
                                               f"citação={r.get('citacao', '')[:160]!r}")
                elif r["classe"] == "NAO_TRATA":
                    dec_f, mot = "descartada", "juiz: NAO_TRATA (fora do tema)"
                else:
                    dec_f, mot = "mantida", (f"juiz: {r['classe']} (citação {r.get('citacao_score')}): "
                                             f"{r.get('citacao', '')[:160]!r}")
                telemetria.evento("fonte", url=p["url"], estagio="juiz", decisao=dec_f, motivo=mot,
                                  afirmacao=ai, classe=r.get("classe"), cluster=p.get("cluster"),
                                  curada=p.get("curada"), corpo_lido=bool(p.get("corpo")),
                                  veredito=p.get("veredito"), n_chars_trecho=len(it.get("trecho") or ""))
        return julg, juiz_ok

    async def _ondas_extras(self, afs, pecas, julg, selecionados, juiz_ok, estado, usar_llm,
                            avisar, etapa, limitacoes) -> int:
        """Laço pós-juiz do agente. Muta `pecas` (só acrescenta: índices estáveis) e `julg`.
        -> páginas lidas a mais. Nunca levanta."""
        alvos = _agente.alvos_de(afs)
        n_lidas = 0
        # Juiz "disponível" p/ o crítico: rodou e classificou algo, ou não havia o que julgar.
        juiz_disp = bool(usar_llm) and (juiz_ok or not selecionados)
        try:
            # max_extras + 1 passes: o último só registra a avaliação final do crítico (parar)
            for rodada in range(max(0, estado.max_extras) + 1):
                onda = 2 + rodada
                pedidos = []
                for ai in range(len(afs)):
                    itens = [{"classe": r.get("classe"), "cluster": pecas[pi].get("cluster"), "url": pecas[pi]["url"],
                              "veredito": pecas[pi].get("veredito"), "titulo": pecas[pi].get("titulo"),
                              "dominio": pecas[pi].get("dominio")}
                             for (pi, a), r in julg.items() if a == ai]
                    res = _agente.resumir_julgamento(ai, itens)
                    d = _agente.criticar(estado, res, juiz_disp)
                    if d["decisao"] == "nova_onda":
                        pedidos.append((ai, res))
                if not pedidos:
                    break
                await avisar("Agente de busca: reformulando a busca para afirmações sem evidência…")
                reformuladas = await asyncio.to_thread(
                    lambda: [(ai, *_agente.reformular(alvos[ai], estado.queries.get(ai, []), res.fora_do_tema,
                                                      usar_llm)) for ai, res in pedidos])
                pedidos_q = [(ai, q, motor) for ai, q, motor in reformuladas if q]
                for ai, q, motor in reformuladas:
                    if not q:
                        estado.decisoes.append(f"onda {onda} af{ai}: sem consulta nova (todas já feitas)")
                if not pedidos_q:
                    break
                await _agente.onda_extra(estado, self.serpapi, pedidos_q, onda)
                if estado.parar_motivo == "timeout":
                    limitacoes.append(f"Agente de busca atingiu o teto de {estado.timeout_s:.0f}s na onda {onda} "
                                      "(resultado parcial preservado).")
                # Fontes novas: somam às anteriores (URL já vista ganha a afirmação nova)
                idx = {corroboracao.url_canonica(p["url"]): i for i, p in enumerate(pecas)}
                n_novas, n_somadas = 0, 0
                for dnovo in estado.pecas(onda=onda):
                    k = dnovo.get("_url_canonica") or corroboracao.url_canonica(dnovo.get("url", ""))
                    if k in idx:
                        pecas[idx[k]]["afs"] = set(pecas[idx[k]].get("afs") or set()) | set(dnovo["_afirmacoes"])
                        n_somadas += 1
                        continue
                    if _eh_homepage(dnovo.get("url", "")):
                        telemetria.evento("fonte", url=dnovo.get("url"), estagio="dedupe", decisao="descartada",
                                          motivo="homepage/seção (não é artigo)")
                        continue
                    p = self._peca_web(dnovo, dnovo["_afirmacoes"])
                    p["url_canonica"] = k
                    self._catalogar(p)
                    pecas.append(p)
                    idx[k] = len(pecas) - 1
                    n_novas += 1
                alvo_afs = {ai for ai, _, _ in pedidos_q}
                sel = self._selecionar(afs, pecas, excluir=set(julg), afs_alvo=alvo_afs)
                lidas, _, sel = await self._ler(afs, pecas, sel, avisar)
                n_lidas += lidas
                if sel:
                    corroboracao.agrupar(pecas)  # clusters com as peças novas (1 voto por cluster)
                    await avisar("Julgando as fontes da nova busca…")
                    j2, _ = await self._julgar(afs, pecas, sel, usar_llm)
                    julg.update(j2)
                novos_pares = set(sel)
                n = {k: sum(1 for par in novos_pares if (julg.get(par) or {}).get("classe") == k)
                     for k in juiz_llm.CLASSES}
                etapa(f"agente-onda-{onda}", "ok" if sel else "parcial",
                      f"Crítico pediu onda {onda} p/ af{sorted(alvo_afs)}: "
                      + "; ".join(f"af{ai} {q!r} ({motor})" for ai, q, motor in pedidos_q)
                      + f". {n_novas} peça(s) nova(s), {n_somadas} já vista(s) somada(s) à afirmação; "
                      f"{len(sel)} julgada(s): {n['SUSTENTA']} sustentam, {n['REFUTA']} refutam, "
                      f"{n['RELATA_SEM_ENDOSSO']} só relatam, {n['NAO_TRATA']} fora do tema. {estado.resumo()}.",
                      [pecas[pi]["url"] for pi, _ in sel][:5])
        except Exception as e:  # o agente nunca derruba o pipeline: fica o que já foi julgado
            log.warning("agente (ondas extras) falhou: %s", str(e)[:200])
            telemetria.fallback("agente", f"ondas extras: {type(e).__name__}: {e}")
        if estado.criticas:
            etapa("agente-critico", "ok",
                  " | ".join(f"af{c['afirmacao']}: {c['decisao']} ({c['motivo']})" for c in estado.criticas)
                  + f". Total: {estado.resumo()}.")
        return n_lidas

    async def _propor_catalogo(self, pecas, julg, etapa) -> None:
        teto = getattr(config, "DISCOVERY_MAX_SITES", 2) or 0
        if teto <= 0:
            return
        alvo, doms = [], set()
        for (pi, _), r in julg.items():
            p = pecas[pi]
            if r.get("classe") in juiz_llm.TRATA and not p.get("portal_id") and p.get("dominio") not in doms:
                doms.add(p.get("dominio"))
                alvo.append(p["url"])
        alvo = alvo[:teto]
        if not alvo:
            return
        try:
            from . import descoberta_site
            desc = await asyncio.wait_for(descoberta_site.descobrir(alvo), timeout=40)
            propostas = []
            for url in alvo:
                r = (desc or {}).get(url) or {}
                prop = r.get("proposta")
                if not prop:
                    continue
                ins = descoberta_site.inserir_no_catalogo(prop) or {}
                if not ins.get("ok"):
                    descoberta_site.salvar_proposta(prop)
                propostas.append(prop.get("dominio") or url)
            etapa("descoberta-catalogo", "ok",
                  f"{len(propostas)} site(s) proposto(s) para curadoria (não viram fonte curada nesta consulta): "
                  + ", ".join(propostas[:4]))
        except Exception as e:
            telemetria.fallback("descoberta-catalogo", f"{type(e).__name__}: {e}")

    async def _estilo(self, texto_base, usar_llm, etapa, limitacoes) -> List[SinalAnalise]:
        """BERTimbau/mock + padrões: 'sinais de estilo do texto (não indicam veracidade)'."""
        sinais: List[SinalAnalise] = []
        try:
            det = await asyncio.to_thread(self.detector.analisar, texto_base[:5000])
            sinais.append(SinalAnalise(
                motor="modelo-fake",
                rotulo=(f"sinal de estilo do texto (não indica veracidade): modelo {det['modelo']}"
                        f"{' (PLACEHOLDER)' if det.get('mock') else ''}"),
                valor=str(det["prob_fake"]), confianca=None))
            if det.get("mock"):
                limitacoes.append("Modelo de estilo ainda é um placeholder (RF08 parcial).")
            etapa("modelo", "ok", f"estilo prob_fake={det['prob_fake']} ({det['modelo']}); fora do nível.")
        except Exception as e:
            etapa("modelo", "falha", str(e)[:200])
        try:
            pads = await asyncio.to_thread(padroes_llm.analisar_padroes, texto_base[:5000], usar_llm)
            if pads["padroes"]:
                nomes = ", ".join(p["padrao"] for p in pads["padroes"][:5])
                sinais.append(SinalAnalise(motor="llm-padroes",
                                           rotulo="sinal de estilo do texto (não indica veracidade): padrões de linguagem",
                                           valor=f"encontrados: {nomes}", confianca=None))
            etapa("padroes", "ok", f"{len(pads['padroes'])} padrão(ões) via {pads['motor']}; fora do nível.")
        except Exception as e:
            etapa("padroes", "falha", str(e)[:200])
        for x in sinais:  # telemetria: estilo é exibido, mas peso 0 no nível
            telemetria.evento("sinal", motor=x.motor, rotulo=x.rotulo, valor=x.valor, confianca=None,
                              direcao=None, peso=0.0, fora_do_nivel=True, evidencias=[])
        return sinais

    # ------------------------------------------------------------------ saída
    @staticmethod
    def _emitir_decisao(dec: "decisao.Decisao") -> None:
        for v in dec.votos:
            telemetria.evento("sinal", motor="veredito-existente" if v.vereditos else "corroboracao",
                              rotulo=f"af{v.afirmacao} cluster {v.cluster}: {', '.join(v.classes + v.vereditos)}",
                              valor=v.motivo, confianca=None, direcao=v.direcao, peso=v.peso,
                              evidencias=v.urls[:3])
        telemetria.evento("decisao", nivel=dec.nivel, nivel_agregador=dec.nivel, score=dec.log_odds,
                          sinais=[f"af{v.afirmacao}:{v.cluster}:{v.valor:+.2f}" for v in dec.votos],
                          why=dec.why_1linha(), travas=dec.resumo_trace(), decisao=dec.to_dict())

    @staticmethod
    def _sinais(dec: "decisao.Decisao") -> List[SinalAnalise]:
        out = []
        for v in dec.votos:
            sentido = "contesta" if v.valor > 0 else "confirma"
            out.append(SinalAnalise(
                motor="veredito-existente" if v.vereditos else "corroboracao",
                rotulo=f"fonte independente {sentido} o que o texto afirma ({v.cluster})",
                valor=f"{v.valor:+.2f} em log-odds: {v.motivo}",
                confianca=round(min(1.0, v.peso / decisao.W_VEREDITO), 3), evidencias=v.urls[:5]))
        return out

    def _fontes(self, afs, pecas, julg, dec) -> List[FonteEvidencia]:
        melhor: Dict[int, Tuple[int, Dict[str, Any]]] = {}
        ordem = {"SUSTENTA": 0, "REFUTA": 0, "RELATA_SEM_ENDOSSO": 1, "NAO_TRATA": 3, None: 2}
        for (pi, ai), r in julg.items():
            if pi not in melhor or ordem[r.get("classe")] < ordem[melhor[pi][1].get("classe")]:
                melhor[pi] = (ai, r)
        pesos = {u: v.peso for v in dec.votos for u in v.urls}
        fontes = []
        for pi, p in enumerate(pecas):
            ai, r = melhor.get(pi, (None, {}))
            classe = r.get("classe")
            corpo = p.get("corpo") or p.get("trecho") or ""
            fontes.append((ordem[classe] if pi in melhor else 4, not bool(p.get("corpo")), not p.get("curada"),
                           FonteEvidencia(
                url=p["url"], titulo=p.get("titulo") or "", portal_id=p.get("portal_id"),
                portal_nome=p.get("veiculo") or p.get("dominio") or "",
                tipo_fonte="veredito" if p.get("veredito") else "corroboracao",
                veredito=p.get("veredito"), selo_original=p.get("selo_original"),
                veredito_normalizado=p.get("veredito"),
                confianca=round(min(1.0, pesos.get(p["url"], 0.0) / decisao.W_VEREDITO), 3) if p["url"] in pesos else None,
                trecho_corpo=corpo[:500] or None, corpo_lido=bool(p.get("corpo")),
                data_pub=p.get("data_pub"), quote=(r.get("citacao") or (p.get("snippet") or corpo)[:140] or None),
                tipo_conteudo="checagem" if (p.get("veredito") or p.get("tipo_portal") == "checagem") else "noticia",
                relevante=juiz_llm.postura_para_relevante(classe) if pi in melhor else None,
                postura=classe, citacao=r.get("citacao") or None, citacao_verificada=r.get("citacao_verificada"),
                motor_juiz=r.get("motor"), cluster=p.get("cluster"), curada=bool(p.get("curada")),
                confiabilidade=p.get("confiabilidade"),
                afirmacao=afs[ai].texto if ai is not None else None)))
        fontes.sort(key=lambda x: x[:3])
        return [f for *_, f in fontes][: max(10, config.MAX_EVIDENCIAS * 2)]

    @staticmethod
    def _relatorio(entrada, dec, sinais, fontes, etapas, limitacoes) -> RelatorioChecagem:
        return RelatorioChecagem(propensao=dec.nivel, justificativa=dec.justificativa(), sinais=sinais,
                                 fontes=fontes, etapas=etapas, limitacoes=limitacoes,
                                 perguntas_guia=perguntas_guia(), consulta=entrada,
                                 header=dec.header(), why_1linha=dec.why_1linha(), decisao=dec.to_dict())
