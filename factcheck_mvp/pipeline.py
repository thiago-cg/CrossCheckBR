"""Orquestrador. Nunca levanta: falha vira etapa + limitação (RF11).

Núcleo = CHECAGEM CRUZADA via busca aberta (fase 2):

  afirmações JSON {afirmacao, nucleo, polaridade, consulta}
   → [índice ClaimReview, só se INDICE_CHECAGENS=1]  +  busca aberta (SerpAPI, agente)
   → dedupe canônico (URL normalizada; índice+web = 1 peça)
   → deep crawl de todas as relevantes (qualquer URL pública; ClaimReview da página)
   → seleção (só ordena: overlap nunca exclui do crawl nem do juiz)
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
from dataclasses import asdict
from datetime import datetime, timezone
import re
import unicodedata
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

from . import (afirmacoes, aplicabilidade, avaliador, confiabilidade, config, corroboracao, decisao, juiz_llm,
               padroes_llm, replay, selos, telemetria)
from . import agente as _agente
from .agregador import perguntas_guia, texto_de_linha
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
# B5: o comparativo vago só trava quando o SUJEITO é genérico (entidade coletiva
# sem referente contável: economia, país, governo, vida, situação). Sujeito
# específico (fármaco, doença, pessoa) é checável mesmo com "piora/melhora"
# ("Ibuprofeno piora o quadro de dengue" tem referente verificável).
SUJEITO_GENERICO_RE = re.compile(
    r"\b(economia|pa[íi]s(es)?|brasil|na[çc][ãa]o(es)?|governos?|gest[ãa]o|administra[çc][ãa]o|"
    r"vida|situa[çc][ãa]o|cen[áa]rio|momento|tudo|coisas?)\b", re.I)
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


def _tier_data(fonte: Optional[str], precisao: Optional[str], relativa: bool = False) -> int:
    """Prioridade de uma data de publicação (maior vence; E4 Task 2 + revisão I-3).

    JSON-LD (dia) 6 > SerpAPI absoluta/ISO (dia) 5 > trafilatura (dia) 4 > SerpAPI relativa
    ancorada 3 > só ano 2 > ReaderLM 1. `fonte` é a fonte REAL da data; o método que extraiu o
    texto não entra. Fonte não declarada (None) fica no nível da trafilatura: não sobrepõe a
    data absoluta da SerpAPI.
    """
    if relativa:
        return 3
    if fonte == "readerlm":
        return 1
    if precisao == "ano":
        return 2
    return {"jsonld": 6, "serpapi": 5, "trafilatura": 4}.get(fonte, 4)


# Campos que `avaliador.avaliar` lê da peça: se nenhum mudou, a entrada do LLM é a mesma.
_CAMPOS_JULGAMENTO = ("titulo", "corpo", "texto_completo", "trecho_juiz", "corpo_lido", "veiculo", "dominio",
                      "tipo_fonte", "data_pub", "data", "veredito", "selo_original", "afirmacao_checada",
                      "veredito_pagina")
# Julgamentos válidos da fase base, reaproveitados no passo 8: (afirmação, URL canônica) -> (assinatura, resultado)
CacheJulgamento = Dict[Tuple[int, str], Tuple[tuple, Dict[str, Any]]]


def _assinatura_julgamento(peca: Dict[str, Any]) -> tuple:
    return tuple(peca.get(c) for c in _CAMPOS_JULGAMENTO)


def _contar_lidas(pecas: List[Dict[str, Any]]) -> int:
    """Páginas lidas na lista FINAL de peças (fase base + passadas da web). A lista tem uma peça por
    URL canônica (fundir_por_url), então a página lida na base não se soma de novo na web."""
    return sum(1 for p in pecas if p.get("corpo_lido"))


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
                        "JUIZ_TRECHO_MAX": config.JUIZ_TRECHO_MAX}})
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
            ancora = ((bruto or {}).get("search_metadata") or {}).get("created_at")
            saida = []
            for item in (bruto or {}).get(self.serpapi.result_key(q), [])[:6]:
                d = rotear_fonte(normalizar_item(item, engine=eng, ancora=ancora), self.catalogo)
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
        peca = {"url": d["url"], "titulo": d.get("titulo") or "", "snippet": d.get("_snippet") or "",
                "veiculo": (d.get("fonte") or {}).get("nome", ""), "afs": set(ais),
                "origens": {"web"}, "data_pub": d.get("data_pub"),
                "data_pub_precisao": d.get("data_pub_precisao"),
                "data_pub_bruta": d.get("data_pub_bruta"),
                "scholar": bool(d.get("_scholar"))}
        bruta = peca.get("data_pub_bruta")
        if bruta and not peca.get("data_pub"):
            telemetria.fallback("data_pub", aplicabilidade.motivo_data_ilegivel(bruta), valor=str(bruta)[:40])
        return peca

    # ------------------------------------------------------------------ principal
    async def _executar(self, entrada: EntradaConsulta, progresso: Progresso = _nada,
                        usar_llm: bool = True) -> RelatorioChecagem:
        from . import indice as _indice
        etapas: List[EtapaRecibo] = []
        limitacoes: List[str] = []
        for aviso in config.AVISOS_CONFIG:  # E4: valor inválido no env cai no default, com rastro
            telemetria.fallback("config", aviso)

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
        # E4: referência explícita (entrada) > relógio gravado (replay.*) > nenhuma. Link sem data
        # (A5) desliga o E4 de forma explícita: nunca cai no relógio de hoje.
        if entrada.sem_referencia_temporal:
            ref, origem_ref = None, "ausente (link sem data)"
        elif entrada.data_referencia:
            ref, origem_ref = entrada.data_referencia, "entrada"
        else:
            ref = replay.hoje(contexto=texto_base)
            origem_ref = "relogio" if ref else "ausente"
        marcador = aplicabilidade.marcador_temporal(texto_base)
        janela_ref = aplicabilidade.janela_temporal(texto_base)
        marcador_ref = f"{marcador} (janela {janela_ref}d)" if marcador is not None else "ausente"
        eh_rumor = bool(RUMOR_RE.search(texto_base))
        eh_opiniao = bool(OPINIAO_SATIRA_RE.search(texto_base[:500]))
        eh_vago = (bool(VAGO_RE.search(texto_base[:500]))
                   and bool(SUJEITO_GENERICO_RE.search(texto_base[:500]))
                   and not INDICADOR_RE.search(texto_base[:800]))
        if eh_rumor:
            limitacoes.append("Relato de segunda-mão sem fonte verificável: busca feita sobre o núcleo factual.")
        if eh_opiniao:
            limitacoes.append("Texto com marcas de opinião/sátira: não classificável como fato sem checagem aplicável.")
        if eh_vago:
            limitacoes.append("Afirmação vaga (comparativo sem indicador nem período: ex PIB, inflação, desemprego + datas): "
                              "diga qual métrica e qual período que eu checo de novo.")
        etapa("recebimento", "ok",
              f"Entrada do tipo {entrada.tipo} ({len(texto_base)} caracteres). "
              f"marcador temporal: {marcador_ref}; referência {ref or 'ausente'} "
              f"(origem: {origem_ref}).")
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
                                    limitacoes + ["Nenhuma afirmação factual encontrada."], "web")
        await avisar(f"Buscando fontes sobre {len(afs)} afirmação(ões)…")

        # 3. Atalho opcional: índice de checagens (ClaimReview/RSS) — só a base (E1)
        pecas_base: List[Dict[str, Any]] = []
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
                    bruta_base = h.get("data_pub")
                    norm_base = (aplicabilidade.normalizar_data(bruta_base)
                                 if isinstance(bruta_base, str) and bruta_base.strip() else None)
                    if bruta_base and not norm_base:
                        telemetria.fallback("data_pub", aplicabilidade.motivo_data_ilegivel(bruta_base),
                                            valor=str(bruta_base)[:40])
                    pecas_base.append({"url": h["url"], "titulo": h.get("titulo") or h.get("afirmacao_checada") or "",
                                   "veiculo": h.get("agencia_nome") or h.get("agencia") or "",
                                   "afs": {ai}, "origens": {"indice"}, "trecho": h.get("trecho") or "",
                                   "data_pub": norm_base[0] if norm_base else None,
                                   "data_pub_precisao": norm_base[1] if norm_base else None,
                                   "data_pub_bruta": bruta_base, "agencia": h.get("agencia"),
                                  "selo_original": h.get("selo_original"),
                                  "afirmacao_checada": h.get("afirmacao_checada"),
                                  "veredito": _veredito_tipado(h.get("veredito"), h.get("selo_original"),
                                                               h.get("agencia")),
                                  "origem_veredito": "indice", "score_rel": h.get("score_rel")})
            etapa("base-checagem", "ok" if n_hits else "parcial",
                  f"{n_hits} checagem(ns) no índice (atalho INDICE_CHECAGENS=1).",
                  [p["url"] for p in pecas_base[:5]])
        else:
            etapa("base-checagem", "pulada", "Atalho do índice desligado (INDICE_CHECAGENS=0): núcleo = busca aberta.")

        # 3b. Base primeiro (E1): dedupe + catálogo da base ANTES da fase base (índices estáveis p/ o gate)
        pecas_base, _fusoes_base = corroboracao.fundir_por_url(pecas_base)
        for mantida, fundida in _fusoes_base:
            telemetria.evento("fonte", url=fundida, estagio="dedupe", decisao="fundida",
                              motivo=f"mesma URL canônica de {mantida}")
        _uteis_base = []
        for p in pecas_base:
            if _eh_homepage(p["url"]):
                telemetria.evento("fonte", url=p["url"], estagio="dedupe", decisao="descartada",
                                  motivo="homepage/seção (não é artigo)")
                continue
            self._catalogar(p)
            _uteis_base.append(p)
        pecas_base = _uteis_base

        # 3c. Fase base: julga só a base e testa aplicabilidade (gate E1). O que ela julga vai para
        # `cache_julg`; o passo 8 reaproveita, em vez de chamar o avaliador de novo no mesmo par.
        cache_julg: CacheJulgamento = {}
        julg_base, houve_aplicavel = await self._fase_base(afs, pecas_base, usar_llm, avisar,
                                                           texto_usuario=texto_base, referencia=ref,
                                                           cache=cache_julg)

        # Gate (E1): checagem aplicável na base → pula web e ondas extras.
        # Telemetria do gate é evento("etapa"), nunca fallback. Com SERPAPI_KEY ausente
        # mantém a mensagem existente (web já desligada; fluxo normal só com a base).
        pulou_web = bool(houve_aplicavel and self.serpapi.ativo)
        if pulou_web:
            pecas = pecas_base
            julg: Dict[Tuple[int, int], Dict[str, Any]] = julg_base
            selecionados = list(julg.keys())
            n_lidas = _contar_lidas(pecas)
            juiz_ok = any(r.get("classe") is not None for r in julg.values())
            etapa("descoberta", "pulada", "web pulada: checagem aplicável na base",
                  [p["url"] for p in pecas[:5]])
            limitacoes.append("Web pulada: checagem aplicável na base.")
            if selecionados:
                _n_alvo_base = len({pi for pi, _ in selecionados})
                etapa("deep-crawl", "ok" if n_lidas else "parcial",
                      f"{n_lidas}/{_n_alvo_base} página(s) com corpo lido; "
                      f"{sum(1 for p in pecas if p.get('origem_veredito') == 'pagina')} com ClaimReview.",
                      [p["url"] for p in pecas if p.get("corpo")][:5])
            if selecionados:
                _n = {k: sum(1 for r in julg.values() if r.get("classe") == k) for k in juiz_llm.CLASSES}
                _n_sem = sum(1 for r in julg.values() if r.get("classe") is None)
                _n_reb = sum(1 for r in julg.values() if r.get("rebaixado"))
                _motor_j = next((r.get("motor") for r in julg.values() if r.get("classe")),
                                juiz_llm.MOTOR_FALLBACK)
                etapa("avaliador", "ok" if juiz_ok and not _n_sem else ("parcial" if juiz_ok else "falha"),
                      f"{len(julg) - _n_sem}/{len(julg)} avaliação(ões) 1:1 (manchete+corpo, base).",
                      [pecas[pi]["url"] for (pi, _), r in julg.items()
                       if r.get("classe") in juiz_llm.COM_POSTURA][:5])
                etapa("juiz", "ok" if juiz_ok and not _n_sem else ("parcial" if juiz_ok else "falha"),
                      f"{len(julg)} julgamento(s) via {_motor_j}: {_n['SUSTENTA']} sustentam, "
                      f"{_n['REFUTA']} refutam, {_n['RELATA_SEM_ENDOSSO']} só relatam, "
                      f"{_n['NAO_TRATA']} fora do tema ({_n_reb} rebaixada(s) por citação não verificada); "
                      f"{_n_sem} sem julgamento.",
                      [pecas[pi]["url"] for (pi, _), r in julg.items()
                       if r.get("classe") in juiz_llm.COM_POSTURA][:5])
                if not juiz_ok:
                    limitacoes.append("Sem julgamento de conteúdo (LLM-juiz indisponível): "
                                      "o nível fica indeterminado; as fontes estão listadas para comparação.")
            else:
                etapa("juiz", "pulada", "Nenhuma fonte candidata para julgar.")

        if not pulou_web:
            # 4. Busca aberta (SerpAPI) — fluxo inalterado quando a base não resolve
            pecas = list(pecas_base)
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

            # 6. Crawl-primeiro: deep crawl de TODAS as relevantes, depois a seleção só ordena
            n_lidas, n_alvo = await self._ler(afs, pecas, avisar)
            selecionados = self._selecionar(afs, pecas)
            if n_alvo:
                etapa("deep-crawl", "ok" if n_lidas == n_alvo else "parcial",
                      f"{n_lidas}/{n_alvo} página(s) com corpo lido; "
                      f"{sum(1 for p in pecas if p.get('origem_veredito') == 'pagina')} com ClaimReview.",
                      [p["url"] for p in pecas if p.get("corpo")][:5])
                if n_lidas < n_alvo:
                    limitacoes.append(f"Deep crawl parcial: {n_lidas}/{n_alvo} página(s) com corpo lido "
                                      "(teto de tempo/cap; parcial preservado).")

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
        # (gate E1: com checagem aplicável na base, julg/juiz_ok já vêm da fase base)
        if not pulou_web:
            julg = {}
            juiz_ok = False
            if selecionados:
                await avisar("Julgando o que cada fonte diz sobre a afirmação…")
                julg, juiz_ok = await self._julgar(afs, pecas, selecionados, usar_llm, cache=cache_julg)
                n = {k: sum(1 for r in julg.values() if r.get("classe") == k) for k in juiz_llm.CLASSES}
                n_sem = sum(1 for r in julg.values() if r.get("classe") is None)
                n_reb = sum(1 for r in julg.values() if r.get("rebaixado"))
                motor_j = next((r.get("motor") for r in julg.values() if r.get("classe")), juiz_llm.MOTOR_FALLBACK)
                etapa("avaliador", "ok" if juiz_ok and not n_sem else ("parcial" if juiz_ok else "falha"),
                      f"{len(julg) - n_sem}/{len(julg)} avaliação(ões) 1:1 (manchete+corpo): "
                      f"{n['SUSTENTA']} sustentam, {n['REFUTA']} refutam, "
                      f"{n['RELATA_SEM_ENDOSSO']} só relatam, {n['NAO_TRATA']} fora do tema; "
                      f"{n_sem} sem avaliar.",
                      [pecas[pi]["url"] for (pi, _), r in julg.items()
                       if r.get("classe") in juiz_llm.COM_POSTURA][:5])
                if n_sem and juiz_ok:
                    limitacoes.append(f"Avaliação parcial: {len(julg) - n_sem}/{len(julg)} peça(s) avaliada(s) "
                                      "(teto de tempo/cap; parcial preservado).")
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
            await self._ondas_extras(afs, pecas, julg, selecionados, juiz_ok, estado_agente,
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
            # T6/B1c: P(fake) da página (credibilidade do BERTimbau). O mock é placeholder: devolve 0,5
            # sem sinal, o que cortaria a postura pela metade; por isso não entra no nível (o valor
            # segue no trace, evento bert_pagina).
            bert = p.get("bert") or {}
            prob_fake = None if bert.get("mock", True) else bert.get("prob_fake")
            itens.append(decisao.ItemEvidencia(
                url=p["url"], afirmacao=ai, cluster=p.get("cluster") or p["url"], classe=r.get("classe"),
                motor=r.get("motor") or "", citacao_verificada=r.get("citacao_verificada"),
                curada=bool(p.get("curada")), corpo_lido=bool(p.get("corpo_lido", p.get("corpo"))),
                veredito=p.get("veredito"),
                origem_veredito=p.get("origem_veredito"), veiculo=p.get("veiculo") or p.get("dominio") or "",
                confiabilidade=p.get("confiabilidade"), data_pub=p.get("data_pub"),
                prob_fake_pagina=prob_fake,
                data_pub_bruta=p.get("data_pub_bruta"), data_pub_precisao=p.get("data_pub_precisao")))
        ev = decisao.Evidencias(
            afirmacoes=[decisao.AfirmacaoDecisao(
                texto=a.texto, nucleo=a.alvo(), polaridade=a.polaridade,
                janela=aplicabilidade.janela_da_afirmacao(a.texto, texto_base, len(afs), referencia=ref),
                marco=aplicabilidade.marco_da_afirmacao(a.texto, texto_base, len(afs), referencia=ref))
                        for a in afs],
            itens=itens, vago=eh_vago, opiniao=eh_opiniao, rumor=eh_rumor, juiz_disponivel=juiz_ok,
            n_lidas=_contar_lidas(pecas), n_consultadas=len(pecas), texto_usuario=texto_base,
            data_referencia=ref)
        telemetria.evento("evidencias", **asdict(ev))
        dec = decisao.decidir(ev)
        self._emitir_decisao(dec)
        etapa("agregacao", "ok", f"propensão {dec.nivel} (L={dec.log_odds:+.2f}, p={dec.prob:.2f}): {dec.motivo}.")

        fontes = self._fontes(afs, pecas, julg, dec)
        sinais = self._sinais(dec) + sinais_estilo
        return self._relatorio(entrada, dec, sinais, fontes, etapas, limitacoes,
                               "base" if pulou_web else "web")

    # ------------------------------------------------------------------ seleção / juiz
    async def _fase_base(self, afs: List[Afirmacao], pecas_base: List[Dict[str, Any]],
                         usar_llm: bool = True, avisar=None,
                         texto_usuario: str = "", referencia: Optional[str] = None,
                         cache: Optional[CacheJulgamento] = None
                         ) -> Tuple[Dict[Tuple[int, int], Dict[str, Any]], bool]:
        """Fase base (E1): seleciona/lê/julga SÓ a base e testa aplicabilidade.

        Para cada par (peça, afirmação) chama `aplicabilidade.e_aplicavel` com as mesmas
        entradas da decisão (E4): janela e marco da afirmação (`janela_da_afirmacao` e
        `marco_da_afirmacao` sobre `texto_usuario` e nº de afirmações), `referencia` já resolvida em `_executar`
        (a mesma de `Evidencias.data_referencia`) e `data_pub` normalizada. Emite
        `telemetria.evento("fonte", estagio="aplicabilidade", decisao=..., motivo=...,
        janela=..., excedente=..., referencia=...)` por decisão. O gate (pular a web) é
        evento("etapa"), nunca fallback — fallback só em erro real.

        -> (julg_base, houve_aplicavel). Mutaciona `pecas_base` (corpo lido). `cache` recebe os
        julgamentos feitos aqui (ver `_julgar`), para o passo 8 não julgar o mesmo par de novo.
        Base vazia (INDICE_CHECAGENS=0) → ({}, False): fluxo web normal.
        """
        if not pecas_base:
            return {}, False
        if avisar is None:
            avisar = _nada
        await self._ler(afs, pecas_base, avisar)
        selecionados = self._selecionar(afs, pecas_base)
        if not selecionados:
            return {}, False
        julg, _ = await self._julgar(afs, pecas_base, selecionados, usar_llm, cache=cache)
        janelas = [aplicabilidade.janela_da_afirmacao(a.texto, texto_usuario, len(afs), referencia=referencia)
                   for a in afs]
        marcos = [aplicabilidade.marco_da_afirmacao(a.texto, texto_usuario, len(afs), referencia=referencia)
                  for a in afs]
        houve = False
        for (pi, ai), r in julg.items():
            p = pecas_base[pi]
            janela, marco = janelas[ai], marcos[ai]
            aplicavel, motivo = aplicabilidade.e_aplicavel(
                r.get("classe"), r.get("citacao_verificada"), bool(p.get("corpo")),
                p.get("veredito"), texto_usuario, p.get("data_pub"),
                referencia=referencia, janela=janela, marco=marco)
            medida = aplicabilidade.medida_da_afirmacao(janela, marco, p.get("data_pub"), referencia)
            telemetria.evento("fonte", url=p.get("url"), estagio="aplicabilidade",
                              decisao="aplicavel" if aplicavel else "inaplicavel",
                              motivo=motivo, afirmacao=ai, classe=r.get("classe"),
                              corpo_lido=bool(p.get("corpo")), veredito=p.get("veredito"),
                              janela=janela, marco=marco, excedente=medida[1] if medida else None,
                              referencia=referencia)
            if aplicavel:
                houve = True
        return julg, houve

    async def _ler(self, afs: List[Afirmacao], pecas: List[Dict[str, Any]],
                     avisar) -> Tuple[int, int]:
        """Crawl-primeiro N×1: deep crawl de `min(relevantes, teto)` peças, ANTES de
        qualquer seleção lexical. `por_afirm = AGENTE_MAX_POR_AFIRMACAO` e
        `teto = max(DEEP_CRAWL_TOTAL, 3×por_afirm)` resolvidos em runtime (o total
        sempre cobre cada relevante, mesmo com por_afirm elevado). O crawl é
        fatiado em lotes de `por_afirm*2` — cap de tarefas por chamada de
        `aprofundar` — p/ todas as candidatas serem tentadas no happy path
        (1 chamada de 36 com por_afirm=12 deixaria 12 sem resposta → fallback
        espúrio); `por_afirm` segue intacto em cada chamada.
        Preenche corpo/trecho_juiz/metodo/titulo/data_pub/veredito_pagina; peças não
        lidas seguem adiante marcadas com corpo_lido=False (nunca excluídas aqui).
        Timeout/cap (URLs sem resposta, ausentes do dict): parcial preservado +
        `fallback deep-crawl`. Marca `_generica` nas homepages/seções reveladas
        genéricas após a leitura.
        -> (n lidas, n alvo)."""
        alvo_crawl: List[Dict[str, Any]] = []
        vistos = set()
        for p in pecas:
            if p["url"] not in vistos and not p.get("corpo"):
                vistos.add(p["url"])
                ai0 = next((ai for ai in sorted(p.get("afs") or {0}) if ai < len(afs)), None)
                alvo_crawl.append({"url": p["url"], "titulo": p.get("titulo", ""),
                                   "_afirmacao": afs[ai0].alvo() if ai0 is not None and afs else ""})
        por_afirm = max(1, int(getattr(config, "AGENTE_MAX_POR_AFIRMACAO", 12) or 12))
        teto_total = max(1, int(getattr(config, "DEEP_CRAWL_TOTAL", 0) or 0), 3 * por_afirm)
        alvo_crawl = alvo_crawl[: teto_total]
        n_lidas = 0
        if alvo_crawl:
            await avisar("Lendo o corpo das fontes…")
            budget = max(getattr(config, "DEEP_CRAWL_TIMEOUT_S", 15) or 15, 25)
            lote = max(1, por_afirm * 2)  # espelha o cap de tarefas por chamada de aprofundar()
            corpos: Dict[str, Any] = {}
            try:
                n_lotes = max(1, -(-len(alvo_crawl) // lote))
                for i in range(0, len(alvo_crawl), lote):
                    parcial = await aprofundar(alvo_crawl[i:i + lote], self.catalogo,
                                               por_afirm=por_afirm,
                                               budget_total=budget / n_lotes)
                    corpos.update(parcial or {})
            except Exception as e:
                telemetria.fallback("deep-crawl", f"{type(e).__name__}: {e}")
                # corpos preserva o que os lotes anteriores já devolveram
            faltantes = [d["url"] for d in alvo_crawl if d["url"] not in (corpos or {})]
            if faltantes:
                telemetria.fallback("deep-crawl",
                                    f"teto {budget:.0f}s/cap: {len(faltantes)}/{len(alvo_crawl)} "
                                    "sem resposta, parcial preservado")
            por_url = {p["url"]: p for p in pecas}
            for url, c in corpos.items():
                p = por_url.get(url)
                if not p or c is None:
                    continue
                if getattr(c, "corpo_lido", False):
                    p["corpo"] = getattr(c, "texto_completo", "") or c.trecho_corpo
                    p["trecho_juiz"] = c.trecho_corpo  # início + parágrafos com termos/conclusão
                    p["metodo"] = getattr(c, "metodo", "")
                    p["corpo_lido"] = True
                    n_lidas += 1
                else:
                    p["corpo_lido"] = False
                if getattr(c, "titulo", "") and not p.get("titulo"):
                    p["titulo"] = c.titulo
                # E4 Task 2: prioridade da data (fim do intervalo normalizado) —
                # JSON-LD da página (dia) > SerpAPI absoluta/ISO (dia) > trafilatura (dia)
                # > SerpAPI relativa ancorada > ano > ReaderLM. A troca usa o tier
                # (fonte+precisão), nunca "primeiro que chegar".
                bruta_pagina = getattr(c, "data_pub", None)
                norm_pagina = (aplicabilidade.normalizar_data(bruta_pagina)
                               if isinstance(bruta_pagina, str) and bruta_pagina.strip() else None)
                if bruta_pagina and not norm_pagina:
                    telemetria.fallback("data_pub", aplicabilidade.motivo_data_ilegivel(bruta_pagina),
                                        valor=str(bruta_pagina)[:40])
                elif norm_pagina is not None:
                    nova, prec_nova = norm_pagina
                    # Tier pela FONTE da data (jsonld|trafilatura|readerlm), não pelo método do texto:
                    # falha/seletor/regex não rebaixam uma data que veio do JSON-LD (revisão I-3).
                    tier_nova = _tier_data(getattr(c, "data_pub_fonte", None), prec_nova)
                    bruta_atual = p.get("data_pub_bruta")
                    if not p.get("data_pub"):
                        tier_atual = -1
                    else:
                        tier_atual = _tier_data("serpapi", p.get("data_pub_precisao"),
                                                relativa=aplicabilidade.e_relativa(bruta_atual))
                    if tier_nova > tier_atual:
                        p["data_pub"], p["data_pub_precisao"] = nova, prec_nova
                        p["data_pub_bruta"] = bruta_pagina
                vp = getattr(c, "veredito_pagina", None)
                if vp and not p.get("veredito"):
                    v = _veredito_tipado(vp.get("veredito"), vp.get("selo_original"), vp.get("agencia"))
                    if v:
                        p.update(veredito=v, selo_original=vp.get("selo_original"),
                                 afirmacao_checada=vp.get("afirmacao_checada"), origem_veredito="pagina",
                                 agencia=vp.get("agencia"))
        for p in pecas:
            if "corpo_lido" not in p:
                p["corpo_lido"] = bool(p.get("corpo"))
        # B1b (T5/D1): BERTimbau/mock mede a CREDIBILIDADE da página (prob_fake);
        # a direção vem do avaliador (T6 consome p["bert"]). Só páginas lidas.
        for p in pecas:
            if not p.get("corpo_lido"):
                continue
            try:
                r = self.detector.analisar_pagina(p.get("titulo") or "", p.get("corpo") or "")
            except Exception as e:
                telemetria.fallback("bert_pagina", f"{type(e).__name__}: {e}")
                continue
            p["bert"] = {"prob_fake": r.get("prob_fake"),
                         "modelo": r.get("modelo") or getattr(self.detector, "nome",
                                                              type(self.detector).__name__),
                         "mock": bool(r.get("mock", True))}
            telemetria.evento("bert_pagina", url=p.get("url"), prob_fake=p["bert"]["prob_fake"])
        # Homepage/seção que só se revela depois de lida (corpo de boilerplate): fora do juiz
        for p in pecas:
            if p.get("corpo") and not p.get("_generica") \
                    and _eh_generica(p["url"], p.get("titulo", ""), p["corpo"]):
                p["_generica"] = True
                telemetria.evento("fonte", url=p["url"], estagio="deep-crawl", decisao="descartada",
                                  motivo="página genérica (homepage/seção) após leitura")
        return n_lidas, len(alvo_crawl)

    def _selecionar(self, afs: List[Afirmacao], pecas: List[Dict[str, Any]],
                    excluir: Optional[set] = None, afs_alvo: Optional[set] = None) -> List[Tuple[int, int]]:
        """Pares (peça, afirmação) p/ o juiz, em round-robin por afirmação, SEM teto:
        todas as peças relevantes vão ao julgamento (lidas ou marcadas sem corpo).
        Ordem por afirmação: overlap (título+snippet × núcleo+consulta), selo, fonte curada e
        confiabilidade (muito acessada/institucional sobe; rede social e site pouco acessado descem).
        `_overlap` é só ordenação: nunca exclui do crawl nem do julgamento. Só ficam de
        fora homepages/seções reveladas genéricas após a leitura (marcadas por `_ler`).
        `excluir`: pares já julgados; `afs_alvo`: só estas afirmações (onda extra do agente)."""
        excluir = excluir or set()
        filas: List[List[int]] = []
        for ai, af in enumerate(afs):
            ref = f"{af.alvo()} {af.consulta}"
            if afs_alvo is not None and ai not in afs_alvo:
                filas.append([])
                continue
            cands = [i for i, p in enumerate(pecas)
                     if ai in (p.get("afs") or {0}) and (i, ai) not in excluir and not p.get("_generica")]
            cands.sort(key=lambda i: (_overlap(ref, f"{pecas[i].get('titulo','')} {pecas[i].get('snippet','')}")
                                      + (0.3 if pecas[i].get("veredito") else 0)
                                      + (0.15 if pecas[i].get("curada") else 0)
                                      + (0.1 if pecas[i].get("tipo_portal") == "checagem" else 0)
                                      + confiabilidade.BONUS_SELECAO.get(pecas[i].get("confiabilidade") or "", 0.0)),
                       reverse=True)
            filas.append(cands)
        saida: List[Tuple[int, int]] = []
        k = 0
        while any(k < len(f) for f in filas):
            for ai, f in enumerate(filas):
                if k < len(f):
                    saida.append((f[k], ai))
            k += 1
        return saida

    async def _julgar(self, afs, pecas, selecionados, usar_llm,
                      cache: Optional[CacheJulgamento] = None) -> Tuple[Dict[Tuple[int, int], Dict], bool]:
        """Julga 1× por par (peça, afirmação) via `avaliador.avaliar` (manchete+corpo).

        `julg[(pi, ai)] = {"classe": posicao, "citacao", "citacao_score",
        "citacao_verificada", "pagina_diz", "raciocinio", "motor", "erro", "rebaixado", ...}`.
        `rebaixado` deriva da citação (`citacao_verificada is False`, como no juiz
        em lote). `_overlap` segue só como ordenação em `_selecionar`, nunca como
        gate. Timeout/cap (teto `JUIZ_TIMEOUT_TOTAL_S`): o parcial já avaliado é
        preservado (nunca descartado) + `fallback juiz/avaliador`. Por peça emite
        `fonte` com `estagio="avaliador"` (posicao, n_chars_trecho, corpo_lido,
        metodo) e depois `estagio="juiz"` (agregação). Ondas extras e fase base
        reutilizam este caminho.

        `cache` (um por execução: a fase base grava, o passo 8 lê): par já julgado com a MESMA
        entrada do avaliador (`_assinatura_julgamento`) reaproveita o resultado, sem nova chamada
        ao LLM e sem novo evento `avaliador` (o da base já está no trace). Só entra no cache o
        julgamento válido (`classe` não None): falha de LLM e par sem corpo são avaliados de novo."""
        por_af: Dict[int, List[int]] = {}
        for pi, ai in selecionados:
            por_af.setdefault(ai, []).append(pi)
        for ai in list(por_af):
            por_af[ai] = sorted(por_af[ai], key=lambda pi: (str(pecas[pi].get("url") or ""), pi))
        # Trecho só p/ telemetria (n_chars_trecho): o julgamento usa manchete+corpo via avaliador.
        trechos: Dict[Tuple[int, int], str] = {}
        for ai, pis in por_af.items():
            alvo = afs[ai].alvo()
            for pi in pis:
                p = pecas[pi]
                corpo = p.get("trecho_juiz") or p.get("corpo") or p.get("trecho") or p.get("snippet") or ""
                trechos[(pi, ai)] = juiz_llm.montar_trecho(p.get("titulo") or "", corpo, alvo)

        def _normalizar(a: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "classe": a.get("posicao"), "citacao": a.get("citacao") or "",
                "citacao_score": a.get("citacao_score"),
                "citacao_verificada": a.get("citacao_verificada"),
                "pagina_diz": a.get("pagina_diz") or "",
                "raciocinio": a.get("raciocinio") or "",
                "motor": a.get("motor") or "", "erro": a.get("erro"),
                "rebaixado": (a.get("rebaixado") if "rebaixado" in a
                              else a.get("citacao_verificada") is False),
                "classe_original": a.get("classe_original"),
            }

        def _evento_avaliador(pi: int, ai: int, a: Dict[str, Any]) -> None:
            p = pecas[pi]
            pos = a.get("posicao")
            if pos is None:
                dec_a, mot_a = "nao-julgada", f"sem avaliador: {a.get('erro')}"
            elif pos == "NAO_TRATA":
                dec_a, mot_a = "descartada", "avaliador: NAO_TRATA (fora do tema)"
            else:
                dec_a, mot_a = "mantida", f"avaliador: {pos}"
            telemetria.evento("fonte", url=p["url"], estagio="avaliador", decisao=dec_a, motivo=mot_a,
                              afirmacao=ai, posicao=pos, classe=pos,
                              n_chars_trecho=len(trechos.get((pi, ai)) or ""),
                              corpo_lido=bool(a.get("corpo_lido")), metodo=a.get("motor") or "")

        resultados: Dict[Tuple[int, int], Dict[str, Any]] = {}
        if usar_llm:
            budget = config.JUIZ_TIMEOUT_TOTAL_S or 600

            async def _todos() -> None:
                for ai in sorted(por_af):
                    alvo = afs[ai].alvo()
                    for pi in por_af[ai]:
                        chave = (ai, corroboracao.url_canonica(pecas[pi].get("url") or ""))
                        assinatura = _assinatura_julgamento(pecas[pi])
                        if cache is not None and chave in cache and cache[chave][0] == assinatura:
                            resultados[(pi, ai)] = dict(cache[chave][1])  # já julgado na fase base
                            continue
                        try:
                            a = await asyncio.to_thread(avaliador.avaliar, alvo, pecas[pi])
                        except Exception as e:  # 1 par falhou: registra e segue (parcial preservado)
                            telemetria.fallback("avaliador", f"{type(e).__name__}: {e}", afirmacao=ai)
                            a = {"posicao": None, "citacao": "", "citacao_score": None,
                                 "citacao_verificada": None, "pagina_diz": "",
                                 "motor": "fallback-avaliador-erro",
                                 "erro": f"{type(e).__name__}: {e}"[:300], "corpo_lido": False}
                        resultados[(pi, ai)] = _normalizar(a)
                        _evento_avaliador(pi, ai, a)
                        if cache is not None and resultados[(pi, ai)]["classe"] is not None:
                            cache[chave] = (assinatura, dict(resultados[(pi, ai)]))

            try:
                await asyncio.wait_for(_todos(), timeout=budget)
            except asyncio.TimeoutError:
                telemetria.fallback("juiz", f"teto {budget}s: parcial preservado "
                                            f"({len(resultados)}/{len(selecionados)})")
                telemetria.fallback("avaliador", f"teto {budget}s: "
                                                 f"{len(selecionados) - len(resultados)} sem avaliar, "
                                                 "parcial preservado")
        else:
            telemetria.fallback("juiz", "usar_llm=False: sem julgamento de conteúdo")
        julg: Dict[Tuple[int, int], Dict[str, Any]] = {}
        juiz_ok = False
        for ai, pis in por_af.items():
            rs = [resultados.get((pi, ai))
                  or juiz_llm._vazio("sem julgamento (LLM desligado/indisponível/teto)") for pi in pis]
            if resultados and all(r.get("classe") is None for r in rs):
                telemetria.fallback("juiz", (rs[0].get("erro") or "juiz falhou")[:300], afirmacao=afs[ai].texto)
            for pi, r in zip(pis, rs):
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
                                  veredito=p.get("veredito"),
                                  n_chars_trecho=len(trechos.get((pi, ai)) or ""))
        return julg, juiz_ok

    async def _ondas_extras(self, afs, pecas, julg, selecionados, juiz_ok, estado, usar_llm,
                            avisar, etapa, limitacoes) -> None:
        """Laço pós-juiz do agente. Muta `pecas` (só acrescenta: índices estáveis) e `julg`.
        As páginas lidas são contadas depois, pela lista final de `pecas` (`_contar_lidas`).
        Nunca levanta."""
        alvos = _agente.alvos_de(afs)
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
                await self._ler(afs, pecas, avisar)
                sel = self._selecionar(afs, pecas, excluir=set(julg), afs_alvo=alvo_afs)
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
        # E4: uma fonte descontada por item de dec.descontos_temporais (decidir segue puro,
        # sem telemetria: o pipeline lê o objeto pronto). data_pub_bruta/precisao vêm do próprio
        # desconto; ficam None só em traces anteriores a elas.
        for d in dec.descontos_temporais:
            telemetria.evento("fonte", url=d.get("url"), estagio="data", decisao="descontada",
                              motivo=f"{d.get('dias_alem_da_janela')} dias além da janela "
                                     f"de {d.get('janela')}",
                              afirmacao=d.get("afirmacao"), data_pub=d.get("data_pub"),
                              data_pub_bruta=d.get("data_pub_bruta"), precisao=d.get("data_pub_precisao"),
                              r=d.get("r"), bits=d.get("bits_descartados"))
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
        # E4 Task 6: relevância temporal por URL (menor r = mais descontada).
        _r_por_url: Dict[str, float] = {}
        for x in (getattr(dec, "descontos_temporais", None) or []):
            u = (x or {}).get("url")
            try:
                r = float((x or {}).get("r"))
            except (TypeError, ValueError):
                continue
            if u and (u not in _r_por_url or r < _r_por_url[u]):
                _r_por_url[u] = r
        fontes = []
        for pi, p in enumerate(pecas):
            ai, r = melhor.get(pi, (None, {}))
            classe = r.get("classe")
            corpo = p.get("corpo") or p.get("trecho") or ""
            fontes.append((ordem[classe] if pi in melhor else 4, not bool(p.get("corpo")), not p.get("curada"),
                           FonteEvidencia(
                url=p["url"], titulo=texto_de_linha(p.get("titulo")), portal_id=p.get("portal_id"),
                portal_nome=p.get("veiculo") or p.get("dominio") or "",
                tipo_fonte="veredito" if p.get("veredito") else "corroboracao",
                veredito=p.get("veredito"), selo_original=p.get("selo_original"),
                veredito_normalizado=p.get("veredito"),
                confianca=round(min(1.0, pesos.get(p["url"], 0.0) / decisao.W_VEREDITO), 3) if p["url"] in pesos else None,
                trecho_corpo=corpo[:500] or None, corpo_lido=bool(p.get("corpo_lido", p.get("corpo"))),
                data_pub=p.get("data_pub"),
                quote=texto_de_linha(r.get("citacao") or (p.get("snippet") or corpo)[:140]) or None,
                data_pub_bruta=p.get("data_pub_bruta"), data_pub_precisao=p.get("data_pub_precisao"),
                relevancia_temporal=_r_por_url.get(p["url"]),
                tipo_conteudo="checagem" if (p.get("veredito") or p.get("tipo_portal") == "checagem") else "noticia",
                relevante=juiz_llm.postura_para_relevante(classe) if pi in melhor else None,
                postura=classe, citacao=r.get("citacao") or None, citacao_verificada=r.get("citacao_verificada"),
                raciocinio=r.get("raciocinio") or None,
                motor_juiz=r.get("motor"), cluster=p.get("cluster"), curada=bool(p.get("curada")),
                confiabilidade=p.get("confiabilidade"),
                afirmacao=afs[ai].texto if ai is not None else None)))
        fontes.sort(key=lambda x: x[:3])
        # N×1 avalia cada relevante (sem corte por JUIZ_MAX_NOTICIAS); o teto aqui é
        # só de EXIBIÇÃO no relatório — com PISO 10 (compat: testes legados fixam
        # JUIZ_MAX_NOTICIAS=2 e exigem a 3ª fonte no relatório). Na prática
        # JUIZ_MAX_NOTICIAS só eleva o teto acima de 10, nunca corta abaixo disso.
        teto_exib = max(int(getattr(config, "JUIZ_MAX_NOTICIAS", 10) or 10),
                        int(getattr(config, "MAX_EVIDENCIAS", 5) or 5) * 2, 10)
        return [f for *_, f in fontes][: teto_exib]

    @staticmethod
    def _relatorio(entrada, dec, sinais, fontes, etapas, limitacoes,
                   onde_encontrado: Literal["base", "web"] = "web") -> RelatorioChecagem:
        # E4 Task 6: limitação neutra de data + pergunta de data no topo (bits ficam no JSON).
        lims = list(limitacoes or [])
        priorizar_data = bool(getattr(dec, "travas", {}).get("data_incompativel")
                              and getattr(dec, "descontos_temporais", None))
        if priorizar_data:
            # No início: o bot mostra só as 3 primeiras limitações (telegram_bot.formatar).
            lims.insert(0, dec.limitacao_datas())
        return RelatorioChecagem(propensao=dec.nivel, justificativa=dec.justificativa(), sinais=sinais,
                                 fontes=fontes, etapas=etapas, limitacoes=lims,
                                 perguntas_guia=perguntas_guia(), consulta=entrada,
                                 header=dec.header(), why_1linha=dec.why_1linha(), decisao=dec.to_dict(),
                                 onde_encontrado=onde_encontrado)
