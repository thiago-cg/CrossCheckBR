"""Orquestrador das 7 etapas. Nunca levanta: falha vira etapa+limitação (RF11)."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import afirmacoes, config, corroboracao, factcheck_api, implicacao, juiz_llm, padroes_llm
from .agregador import agregar, gerar_header, montar_justificativa, perguntas_guia
from .aprofundar import aprofundar
from .catalogo import Catalogo
from .indice import Indice
from .logs import id_consulta, novo_id, resumo_texto
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


def _eh_generica(url: str, titulo: str = "", trecho: str = "") -> bool:
    """Homepage/seção institucional não é evidência (critic 1, loop 2)."""
    try:
        from urllib.parse import urlparse as _up
        path = (_up(url or "").path or "/")
    except Exception:
        return True
    if GENERICA_RE.match(path or "/"):
        return True
    # Seção sem marcador de artigo (sem data/.htm/noticia-id): ex /fato-ou-fake/, /estadao-verifica
    if path.endswith("/") and not re.search(r"\d{4}|\.htm|/noticia/.+/.+|/articles?/", path, re.I):
        seg = [s for s in path.strip("/").split("/") if s]
        # URL sem marcador NÃO basta p/ genérica: com corpo real lido (>=200 chars)
        # é evidência válida (artigos sem data/.htm são comuns); sem corpo, só
        # homepage/seção => genérica (review loop 4).
        if len(seg) <= 2 and len((trecho or "").strip()) < 200:
            return True
    # Checklist-section conhecida é SEMPRE genérica (com ou sem barra final):
    # mesmo com corpo lido, é seção/hub, não artigo individual.
    raiz = "/" + path.strip("/")
    if raiz in ("/fato-ou-fake", "/fato-ou-boato", "/estadao-verifica", "/confere"):
        return True
    if len((trecho or "").strip()) < 200 and len((titulo or "").strip()) < 40:
        # título curto genérico sem corpo útil
        if re.search(r"^(sobre|home|início|redes sociais|boatos\.org)\b", (titulo or "").strip(), re.I):
            return True
    return False


async def _nada(_: str) -> None:
    return None


def _overlap(afirmacao: str, titulo: str) -> float:
    """Triagem lexical barata p/ ordenar manchetes antes do juiz (0..1)."""
    import unicodedata as _ud
    ta = set(_ud.normalize("NFKD", (afirmacao or "")).encode("ascii", "ignore").decode().lower().split())
    tt = set(_ud.normalize("NFKD", (titulo or "")).encode("ascii", "ignore").decode().lower().split())
    ta = {t for t in ta if len(t) >= 4}
    return len(ta & tt) / max(1, len(ta))


class Pipeline:
    def __init__(self, catalogo: Catalogo, indice_vereditos: Indice, indice_noticias: Indice,
                 serpapi: Optional[SerpAPIClient] = None, detector: Optional[DetectorFake] = None):
        self.catalogo = catalogo
        self.idx_ver = indice_vereditos
        self.idx_not = indice_noticias
        self.serpapi = serpapi or SerpAPIClient()
        self.detector = detector or carregar_detector()

    async def executar(self, entrada: EntradaConsulta, progresso: Progresso = _nada,
                       usar_llm: bool = True) -> RelatorioChecagem:
        etapas: List[EtapaRecibo] = []
        sinais: List[SinalAnalise] = []
        fontes: List[FonteEvidencia] = []
        limitacoes: List[str] = []
        if id_consulta.get() == "-":
            novo_id()  # bot/testes: a API já define o id na requisição
        t_inicio = time.time()
        log.info("checagem iniciada: tipo=%s, %d caracteres, llm=%s: %r", entrada.tipo,
                 len(entrada.conteudo), usar_llm, resumo_texto(entrada.conteudo))

        def etapa(nome: str, status: str, detalhe: str = "", fontes_urls: Optional[List[str]] = None):
            etapas.append(EtapaRecibo(nome=nome, status=status, detalhe=detalhe, fontes=fontes_urls or []))
            # Toda etapa vira linha de log (o recibo do usuário é a mesma fonte).
            (log.info if status in ("ok", "pulada") else log.warning)(
                "etapa %s [%s] %.1fs: %s", nome, status, time.time() - t_inicio, detalhe)

        async def avisar(msg: str) -> None:
            try:
                await progresso(msg)
            except Exception:
                pass  # callback de UI nunca quebra o pipeline (RF11)

        # LLM-juiz: teto de peças julgadas por consulta (1 resumo cada + juiz
        # em lote por afirmação). O teto é consumido na ordem: selos do índice,
        # depois cobertura web.
        restam_juiz = max(0, config.JUIZ_MAX_NOTICIAS or 10)

        async def _julgar_lote(pares: List[tuple], teto: int) -> List[Dict[str, Any]]:
            """pares=[(texto, afirmacao)] → por item
            {sustenta, refuta, relevante, resumo, score, erro, motor}.
            Resumos em concorrência; juiz em 1 call por afirmação. Sem LLM, sem
            teto ou com falha: fallback lexical honesto por item. Nunca levanta.
            """
            pares = list(pares[:max(0, teto)])
            # Rajada total de resumos estourou 429 no fallback (log café/câncer):
            # semáforo limita a concorrência sem mudar o teto.
            sem = asyncio.Semaphore(max(1, config.JUIZ_CONCORRENCIA or 4))
            if usar_llm and pares:
                async def _r(t: str, af: str) -> Dict[str, Any]:
                    async with sem:
                        try:
                            return await asyncio.to_thread(juiz_llm.resumir, t, af)
                        except Exception as e:
                            return {"resumo": "", "erro": str(e)[:200], "motor": "llm-juiz"}
                resumos = await asyncio.gather(*[_r(t, af) for t, af in pares])
            else:
                motivo = "llm desligado" if not usar_llm else "teto do juiz esgotado"
                resumos = [{"resumo": "", "erro": motivo, "motor": "llm-juiz"} for _ in pares]
            saida = [{"texto": t, "afirmacao": af, "resumo": (r.get("resumo") or ""),
                      "erro_resumo": r.get("erro")} for (t, af), r in zip(pares, resumos)]
            grupos: Dict[str, List[int]] = {}
            for i, s in enumerate(saida):
                if s["resumo"]:
                    grupos.setdefault(s["afirmacao"], []).append(i)

            async def _j(af: str, idxs: List[int]) -> List[Dict[str, Any]]:
                try:
                    return await asyncio.to_thread(
                        juiz_llm.termometro, [saida[i]["resumo"] for i in idxs], af)
                except Exception as e:  # termometro nunca levanta; rede de segurança
                    return [{"score": 0, "sustenta": 0.0, "refuta": 0.0, "relevante": False,
                             "erro": str(e)[:200], "motor": "llm-juiz"} for _ in idxs]

            if saida:
                log.info("  juiz: %d agente(s) de resumo, %d fora do tema, %d com resumo",
                         len(saida), sum(1 for s_ in saida if s_["resumo"].strip() == juiz_llm.FORA_DO_TEMA),
                         sum(1 for s_ in saida if s_["resumo"] and s_["resumo"].strip() != juiz_llm.FORA_DO_TEMA))
            if grupos:
                blocos = await asyncio.gather(*[_j(af, idxs) for af, idxs in grupos.items()])
                for (_, idxs), scores in zip(grupos.items(), blocos):
                    for i, sc in zip(idxs, scores):
                        saida[i].update(sc)
            for s in saida:
                if not s.get("resumo") or s.get("erro"):
                    fb = implicacao._fallback_lexico(s["texto"], s["afirmacao"])
                    base = " | ".join(e for e in (s.pop("erro_resumo", None),
                                                  s.pop("erro", None)) if e)
                    s.update(sustenta=fb["sustenta"], refuta=fb["refuta"],
                             relevante=fb["relevante"], score=None, motor=fb.get("motor"))
                    s["erro"] = (base + " | fallback-lexico").strip(" |") if base else "fallback-lexico"
                else:
                    s.pop("erro_resumo", None)
            return saida

        # 1. Normalização + rumor/opinião (checks #6: fato vs opinião/sátira; rumor 2ª mão)
        texto_base = entrada.conteudo.strip()
        if entrada.tipo == "link" and not texto_base.startswith(("http://", "https://", "www.")):
            pass  # classificar_entrada já garante o formato; segue como texto citável
        eh_rumor = bool(RUMOR_RE.search(texto_base))
        eh_opiniao = bool(OPINIAO_SATIRA_RE.search(texto_base[:500]))
        eh_vago = bool(VAGO_RE.search(texto_base[:500])) and not INDICADOR_RE.search(texto_base[:800])
        detalhe_rec = f"Entrada do tipo {entrada.tipo} ({len(texto_base)} caracteres)."
        if eh_rumor:
            limitacoes.append("Relato de segunda-mão sem fonte verificável: busca feita sobre o núcleo factual.")
        if eh_opiniao:
            limitacoes.append("Texto com marcas de opinião/sátira: não classificável como fato; veredito contido.")
        if eh_vago:
            limitacoes.append("Afirmação vaga (comparativo sem indicador nem período: ex PIB, inflação, desemprego + datas): "
                              "diga qual métrica e qual período que eu checo de novo.")
        etapa("recebimento", "ok", detalhe_rec)
        await avisar("Recebi. Extraindo as afirmações verificáveis…")

        # 2. Afirmações (OpenRouter deepseek->gemma, depois LLM local; fallback determinístico)
        llm_motor = "regex-deterministico"
        try:
            afs, llm_motor = await asyncio.to_thread(
                afirmacoes.extrair_afirmacoes, texto_base, 0, usar_llm)
        except Exception as e:  # pragma: no cover
            afs, llm_motor = afirmacoes.extrair_afirmacoes(texto_base, max_n=0, usar_llm=False)
            limitacoes.append(f"Extração de afirmações via fallback: {e}")
        etapa("afirmacoes", "ok" if afs else "falha", f"{len(afs)} afirmação(ões) via {llm_motor}.")
        for af in afs:
            log.info("  afirmação %d: %r", af.indice, resumo_texto(af.texto, 160))
        if eh_rumor or eh_opiniao or eh_vago:
            log.info("  marcas no texto: rumor=%s opinião/sátira=%s vago=%s", eh_rumor, eh_opiniao, eh_vago)
        if not afs:
            return self._relatorio(entrada, "indeterminada",
                                   "Não identifiquei afirmação factual para checar.", sinais, fontes, etapas,
                                   limitacoes + ["Nenhuma afirmação factual encontrada."])
        await avisar(f"Buscando nas bases de checagem ({len(afs)} afirmação(ões))…")

        # 3. Índices locais: vereditos (13 checagem) + notícias (7 gerais, RF05).
        # Piso de score: 1 token em comum não vira evidência; confiança escala com o score.
        achados_ver: List[Dict[str, Any]] = []
        achados_not: List[Dict[str, Any]] = []
        # Checagens das agências (Google Fact Check) vêm antes do índice local:
        # entram no corte MAX_EVIDENCIAS e passam pelo mesmo LLM-juiz dos selos.
        n_factcheck = 0
        if factcheck_api.ativo():
            try:
                blocos_fc = await asyncio.wait_for(asyncio.gather(*[
                    asyncio.to_thread(factcheck_api.buscar, af.texto)
                    for af in afs[: config.MAX_AFIRMACOES]]), timeout=20)
            except asyncio.TimeoutError:
                blocos_fc = []
                limitacoes.append("Busca nas agências de checagem excedeu o tempo (parcial).")
            vistos_fc = set()
            for af, docs in zip(afs, blocos_fc):
                for d in docs:
                    if d["url"] not in vistos_fc:
                        vistos_fc.add(d["url"])
                        # score 3.0 -> confiança 0.85: checagem publicada, ainda por julgar
                        achados_ver.append({"doc": d, "score": 3.0, "afirmacao": af.texto})
            n_factcheck = len(vistos_fc)
            log.info("  google fact check: %d checagem(ns) das agências", n_factcheck)
        for af in afs:
            achados_ver += [{**h, "afirmacao": af.texto} for h in self.idx_ver.buscar(af.texto, top_k=5)
                            if h["score"] >= config.INDICE_SCORE_MIN]
            achados_not += [{**h, "afirmacao": af.texto} for h in self.idx_not.buscar(af.texto, top_k=5)
                            if h["score"] >= config.INDICE_SCORE_MIN]
        pos_ver: List[tuple] = []  # (índice em fontes, hit) p/ julgar selos
        for h in achados_ver[: config.MAX_EVIDENCIAS]:
            d = h["doc"]
            conf = min(0.9, round(0.4 + 0.15 * h["score"], 3))
            corpo = (d.get("corpo_texto") or "")[:500]
            gen = _eh_generica(d.get("url", ""), d.get("titulo", ""), corpo)
            if gen:
                # Homepage/seção não vira evidência ranqueada (loop 3); mantém rastro com confiança baixa
                conf = min(conf, 0.3)
            fontes.append(FonteEvidencia(url=d.get("url", ""), titulo=d.get("titulo", ""),
                                         portal_id=(d.get("fonte") or {}).get("id"),
                                         portal_nome=(d.get("fonte") or {}).get("nome", ""),
                                         tipo_fonte="veredito", veredito=d.get("veredito"),
                                         selo_original=d.get("selo_original"), confianca=conf,
                                         trecho_corpo=(corpo if corpo and not gen else None),
                                         corpo_lido=bool(corpo) and not gen,
                                         data_pub=(d.get("data_publicacao") or None),
                                         quote=(corpo[:140] if corpo and not gen else None),
                                         tipo_conteudo=d.get("tipo_conteudo")))
            if d.get("veredito") and not gen:
                pos_ver.append((len(fontes) - 1, h))
        # Julgamento LLM-juiz dos selos (1 resumo cada + juiz em lote, teto):
        # selo sobre outro assunto não contamina a consulta (ex: VERDADEIRO
        # sobre golpes não abona "bets proibidas").
        julg_ver = await _julgar_lote(
            [(f"{h['doc'].get('titulo','')} {h['doc'].get('corpo_texto','') or ''}",
              h["afirmacao"]) for _, h in pos_ver], restam_juiz)
        restam_juiz -= len(julg_ver)
        for (pos, h), j in zip(pos_ver, julg_ver):
            d = h["doc"]
            log.info("  selo %-24s %s nota=%s relevante=%s | %s",
                     (d.get("fonte") or {}).get("nome", "")[:24], d.get("veredito"),
                     j.get("score"), j.get("relevante"), resumo_texto(d.get("titulo", ""), 70))
            if j.get("resumo"):
                fontes[pos].resumo_juiz = (j["resumo"] or "")[:500]
            if j.get("score") is not None:
                fontes[pos].score_juiz = j["score"]
            fontes[pos].relevante = bool(j.get("relevante"))
            if not j.get("relevante"):
                continue
            portal = (d.get("fonte") or {}).get("nome", "checador")
            sus, ref = j.get("sustenta", 0.0), j.get("refuta", 0.0)
            # Gate lexical (sem LLM) nunca confere confiança >=0.6 ao selo:
            # evita veredito fantasma puro overlap (review loop 4).
            conf_hit = min(0.9, round(0.4 + 0.15 * h["score"], 3))
            teto = 0.55 if j.get("motor") == "lexico-fallback" else 0.9
            conf_sinal = min(conf_hit, teto)
            if ref >= 0.6:
                # Peça refuta a afirmação: selo VERDADEIRO aqui fortalece a refutação.
                sinais.append(SinalAnalise(motor="veredito-existente",
                                           rotulo=f"selo do portal ({portal}): {d['veredito']} + fonte refuta",
                                           valor=f"afirmação refutada (selo {d['veredito']})",
                                           confianca=min(conf_sinal, round(ref, 3)),
                                           evidencias=[d.get("url", "")]))
            elif sus >= 0.6:
                sinais.append(SinalAnalise(motor="veredito-existente",
                                           rotulo=f"selo do portal ({portal}): {d['veredito']} + fonte sustenta",
                                           valor=f"afirmação sustentada (selo {d['veredito']})",
                                           confianca=min(conf_sinal, round(sus, 3)),
                                           evidencias=[d.get("url", "")]))
            else:
                sinais.append(SinalAnalise(motor="veredito-existente",
                                           rotulo=f"selo do portal ({portal}): {d['veredito']}",
                                           valor=str(d["veredito"]), confianca=conf_sinal,
                                           evidencias=[d.get("url", "")]))
            if d.get("confianca_veredito") is not None:  # veredito veio do laya no crawl
                sinais.append(SinalAnalise(motor="laya", rotulo="veredito confirmado pelo classificador",
                                           valor=str(d["veredito"]), confianca=d["confianca_veredito"],
                                           evidencias=[d.get("url", "")]))
        for h in achados_not[: config.MAX_EVIDENCIAS]:
            d = h["doc"]
            if not d.get("url"):
                continue
            corpo_n = (d.get("corpo_texto") or "")[:500]
            gen_n = _eh_generica(d.get("url", ""), d.get("titulo", ""), corpo_n)
            fontes.append(FonteEvidencia(url=d["url"], titulo=d.get("titulo", ""),
                                         portal_id=(d.get("fonte") or {}).get("id"),
                                         portal_nome=(d.get("fonte") or {}).get("nome", ""),
                                         tipo_fonte="corroboracao",
                                         confianca=min(0.85, round(0.4 + 0.15 * h["score"], 3)),
                                         trecho_corpo=(corpo_n if corpo_n and not gen_n else None),
                                         corpo_lido=bool(corpo_n) and not gen_n,
                                         data_pub=(d.get("data_publicacao") or None),
                                         quote=(corpo_n[:140] if corpo_n and not gen_n else None),
                                         tipo_conteudo=d.get("tipo_conteudo")))
        # "ok" só com checagem julgada relevante: homepage/seção casando por
        # palavra solta não é achado (a base local ainda é quase só homepages).
        n_ver_relev = sum(1 for pos, _ in pos_ver if fontes[pos].relevante)
        if n_ver_relev:
            etapa("base-checagem", "ok",
                  f"{n_ver_relev} checagem(ns) relevante(s) "
                  f"({n_factcheck} das agências via Google Fact Check; resto do índice local).",
                  [fontes[pos].url for pos, _ in pos_ver if fontes[pos].relevante][:5])
        elif achados_ver or achados_not:
            fc = (f"{n_factcheck} checagem(ns) das agências fora do tema; " if factcheck_api.ativo()
                  else "Google Fact Check desligado (sem FACTCHECK_API_KEY); ")
            etapa("base-checagem", "parcial",
                  f"Nenhuma checagem sobre o tema. {fc}índice local com {len(self.idx_ver)} "
                  f"documentos ({len(achados_ver) + len(achados_not) - n_factcheck} só por palavras soltas).")
            limitacoes.append("Não encontramos checagem publicada por agências sobre o tema.")
        else:
            etapa("base-checagem", "parcial", "Nenhuma checagem prévia encontrada no índice.")
            limitacoes.append("Sem checagem prévia no índice local.")

        # 4. Descoberta fresca (SerpAPI) + julgamento LLM-juiz (teto 10)
        if self.serpapi.ativo:
            await avisar("Consultando cobertura recente nos veículos…")
            descobertas: List[Dict[str, Any]] = []

            async def _buscar_query(af_texto: str, q: Dict[str, Any]) -> List[Dict[str, Any]]:
                bruto = await asyncio.to_thread(self.serpapi.buscar, q)
                saida = []
                for item in (bruto or {}).get("news_results", [])[:6]:
                    parcial = rotear_fonte(normalizar_item(item), self.catalogo)
                    parcial["_afirmacao"] = af_texto
                    saida.append(parcial)
                return saida

            consultas = [(af.texto, q) for af in afs[: config.MAX_AFIRMACOES] for q in construir_queries(af.texto)]
            try:
                blocos = await asyncio.wait_for(
                    asyncio.gather(*[_buscar_query(t, q) for t, q in consultas]), timeout=60)
                for b in blocos:
                    descobertas += b
            except asyncio.TimeoutError:
                limitacoes.append("Descoberta web atingiu o teto de 60s (resultado parcial).")
            log.info("  serpapi: %d consulta(s), %d resultado(s) brutos (uso hoje %d)",
                     len(consultas), len(descobertas), self.serpapi.uso_hoje)
            vistos, unicas = set(), []
            for d in descobertas:
                if d["url"] and d["url"] not in vistos:
                    vistos.add(d["url"])
                    unicas.append(d)

            # Triagem lexical barata das manchetes (0..1); o juiz decide a
            # relevância — o Jev não participa mais do julgamento.
            # Ordena TODAS e fatia depois: match forte fora do top-12 SerpAPI
            # também chega ao juiz.
            ordenadas = sorted(unicas,
                               key=lambda d: _overlap(d.get("_afirmacao", ""),
                                                      d.get("titulo", "")),
                               reverse=True)[:12]
            # Leitura (check #2): CADA notícia que vai a um agente do juiz é lida
            # na íntegra — qualquer site público vindo do Google News, não só o
            # catálogo (anti-SSRF segue a cada hop). Falha -> agente lê a manchete.
            corpos = {}
            a_ler = ordenadas[: max(config.DEEP_CRAWL_MAX_PAGES, 1)]
            if a_ler:
                try:
                    await avisar(f"Lendo as {len(a_ler)} notícias encontradas…")
                    corpos = await aprofundar(a_ler, self.catalogo, por_afirm=len(a_ler),
                                              exigir_catalogo=False)
                except Exception:
                    corpos = {}
                for d in a_ler:
                    c = corpos.get(d.get("url", ""))
                    if c and c.corpo_lido:
                        d["_corpo"] = c
                        if not d.get("catalogada"):
                            # Site fora do catálogo: corpo lido vale, mas no tier
                            # provisório (confiança reduzida até curadoria).
                            d["_descoberta"] = True
            n_lidas = sum(1 for d in a_ler if d.get("_corpo"))
            log.info("  leitura: %d de %d notícias lidas na íntegra", n_lidas, len(a_ler))
            # Descoberta de catálogo: relevante NÃO catalogada entra no catálogo
            # IMEDIATAMENTE (primeira vez que aparece) + corpus com peso cheio.
            # Se a inserção falhar: fallback provisório (proposta pendente + conf 0.5).
            n_descoberta_corpo = 0
            novos_dominios: List[str] = []
            novos_provisorios: List[str] = []
            nao_cat = [d for d in ordenadas if not d.get("catalogada")]
            if nao_cat:
                try:
                    from . import descoberta_site
                    await avisar("Mapeando sites novos para o catálogo…")
                    teto_n = getattr(config, "DISCOVERY_MAX_SITES", 2) or 2
                    desc = await descoberta_site.descobrir(
                        [d["url"] for d in nao_cat[:teto_n] if d.get("url")])
                    for d in nao_cat[:teto_n]:
                        r = desc.get(d.get("url", ""))
                        if r and r.get("corpo") and r["corpo"].corpo_lido:
                            d["_corpo"] = r["corpo"]  # o juiz lê o corpo adiante
                            n_descoberta_corpo += 1
                        if r and r.get("proposta"):
                            dom = r["proposta"].get("dominio", "")
                            ins = descoberta_site.inserir_no_catalogo(r["proposta"])
                            if ins.get("ok") and self.catalogo.adicionar(ins["portal"]):
                                d["catalogada"] = True  # catálogo: peso cheio
                                d.pop("_descoberta", None)
                                novos_dominios.append(dom)
                            elif descoberta_site.salvar_proposta(r["proposta"]):
                                d["_descoberta"] = True  # provisório: conf reduzida
                                novos_provisorios.append(dom)
                    if novos_dominios:
                        limitacoes.append(
                            "Site(s) novo(s) adicionado(s) ao catálogo: "
                            + ", ".join(sorted(set(novos_dominios))[:4]) + ".")
                    if novos_provisorios:
                        limitacoes.append(
                            "Site(s) novo(s) mapeado(s) p/ o catálogo (pendente de curadoria): "
                            + ", ".join(sorted(set(novos_provisorios))[:4]) + ".")
                    if any(d.get("_descoberta") for d in nao_cat[:teto_n]):
                        limitacoes.append(
                            "Fonte(s) fora do catálogo com corpo lido "
                            "provisório (confiança reduzida até curadoria).")
                except Exception:
                    log.exception("descoberta de catálogo falhou (parcial)")
            # Julgamento LLM-juiz (teto): 1 resumo por peça (corpo lido
            # prevalece sobre a manchete) + 1 juiz-termômetro em lote.
            pares_ser: List[tuple] = []
            julg_ds: List[Dict[str, Any]] = []
            for d in ordenadas:
                if len(pares_ser) >= restam_juiz:
                    break
                corp = d.get("_corpo")
                trecho = (corp.trecho_corpo if corp and corp.corpo_lido else "") or ""
                if trecho and _eh_generica(d.get("url", ""), d.get("titulo", ""), trecho):
                    continue  # corpo de homepage/seção será descartado; poupa o teto
                texto_j = (trecho[:2000] if trecho else (d.get("titulo", "") or "")[:2000])
                if len(texto_j.strip()) < 30 and not trecho:
                    continue
                pares_ser.append((texto_j, d.get("_afirmacao", "")))
                julg_ds.append(d)
            try:
                julgados = await asyncio.wait_for(
                    _julgar_lote(pares_ser, restam_juiz), timeout=120)
            except asyncio.TimeoutError:
                julgados = []
                limitacoes.append("Julgamento LLM-juiz atingiu o teto (resultado parcial).")
            restam_juiz -= len(julgados)
            for d, j in zip(julg_ds, julgados):
                d["_imp"] = j
                log.info("  agente %-22s %s nota=%s relevante=%s motor=%s | %s",
                         (d.get("fonte") or {}).get("nome", "")[:22],
                         "texto " if d.get("_corpo") else "título",
                         j.get("score"), j.get("relevante"), j.get("motor") or "-",
                         resumo_texto(d.get("titulo", ""), 70))
            juiz_ok = any(d.get("_imp", {}).get("motor") == "llm-juiz"
                          and not d["_imp"].get("erro") for d in julg_ds)
            if any("fallback-lexico" in (d.get("_imp", {}).get("erro") or "") for d in julg_ds):
                limitacoes.append("A análise por IA das notícias falhou ou atingiu o limite diário "
                                  "(LLM_DAILY_CAP): parte das fontes foi comparada só por palavras, "
                                  "sem saber se confirmam ou contestam o conteúdo.")
            relevantes = [d for d in julg_ds if d.get("_imp", {}).get("relevante")]
            # Sem juiz (LLM fora/cap) e zero relevantes: relevância lexical +
            # corpo lido provisório nas top-3 catalogadas (nunca o tier cheio).
            if not relevantes and unicas and not juiz_ok:
                candidatas_top = [d for d in ordenadas if d.get("catalogada")][:3]
                if candidatas_top:
                    for d in candidatas_top:
                        if not d.get("_imp", {}).get("relevante"):
                            d["_imp"] = {"sustenta": 0.35, "refuta": 0.0,
                                         "relevante": True, "erro": "lexico-top3"}
                    relevantes = candidatas_top
                    limitacoes.append("Sem LLM-juiz: relevância lexical + corpo lido (provisório, confiança reduzida).")
            n_corpo_lido = sum(1 for d in relevantes
                               if d.get("_corpo") and d["_corpo"].corpo_lido)
            n_sus = sum(1 for d in relevantes if d["_imp"].get("sustenta", 0.0) >= 0.6)
            n_ref = sum(1 for d in relevantes if d["_imp"].get("refuta", 0.0) >= 0.6)
            n_sus_corpo = sum(1 for d in relevantes
                              if d.get("_corpo") and d["_corpo"].corpo_lido
                              and not d.get("_descoberta")
                              and d["_imp"].get("sustenta", 0.0) >= 0.6)
            # Site novo (descoberta): corpo lido vale como evidência provisória,
            # tier próprio com confiança reduzida — nunca o tier 0.75 catalogado.
            n_sus_desc = sum(1 for d in relevantes
                             if d.get("_descoberta") and d.get("_corpo")
                             and d["_corpo"].corpo_lido
                             and d["_imp"].get("sustenta", 0.0) >= 0.6)
            # Peso proporcional ao placar: 3 contestam x 1 sustenta não pode
            # empatar 1 sinal com 1 sinal (caso Selic: saía "baixa").
            frac_ref = n_ref / max(1, n_ref + n_sus)
            frac_sus = 1.0 - frac_ref if n_ref else 1.0
            if n_ref:
                sinais.append(SinalAnalise(motor="corroboracao", rotulo="fontes refutam a afirmação",
                                           valor=f"{n_ref} fonte(s) contestam a afirmação",
                                           confianca=round(0.8 * frac_ref, 3),
                                           evidencias=[d["url"] for d in relevantes if d["_imp"].get("refuta", 0.0) >= 0.6][:5]))
            if n_sus:
                # Tier de evidência (check #2): corpo lido vale mais que só-título
                modais = re.compile(r"\b(serão|será|podem|pode|poderá|projeto|defende|estuda|quer|pretende|discute|avalia|se depender)\b", re.I)
                titulos_sus = [d["titulo"] for d in relevantes if d["_imp"].get("sustenta", 0.0) >= 0.6]
                n_modais = sum(1 for t in titulos_sus if modais.search(t or ""))
                if n_sus_corpo:
                    sinais.append(SinalAnalise(motor="corroboracao", rotulo="fontes sustentam a afirmação (corpo lido)",
                                               valor=f"{n_sus_corpo} fonte(s) com corpo lido sustentam ({n_sus} manchetes)",
                                               confianca=round(0.75 * frac_sus, 3),
                                               evidencias=[d["url"] for d in relevantes if d.get("_corpo") and d["_corpo"].corpo_lido and not d.get("_descoberta")][:5]))
                elif n_sus_desc:
                    sinais.append(SinalAnalise(motor="corroboracao", rotulo="fontes sustentam a afirmação (site novo, corpo lido)",
                                               valor=f"{n_sus_desc} fonte(s) fora do catálogo com corpo lido (provisório, até curadoria)",
                                               confianca=round(0.5 * frac_sus, 3),
                                               evidencias=[d["url"] for d in relevantes if d.get("_descoberta")][:5]))
                else:
                    conf_sus = round((0.5 if not n_modais else 0.25) * frac_sus, 3)
                    sinais.append(SinalAnalise(motor="corroboracao", rotulo="fontes sustentam a afirmação (só título)",
                                               valor=f"{n_sus} manchete(s) sustentam ({n_modais} condicionais); corpo não lido",
                                               confianca=conf_sus,
                                               evidencias=[d["url"] for d in relevantes if d["_imp"].get("sustenta", 0.0) >= 0.6][:5]))
                    limitacoes.append("Implicação feita só em manchetes (sem corpo: deep crawl futuro).")
            for d in relevantes[: config.MAX_EVIDENCIAS]:
                corp = d.get("_corpo")
                trecho = (corp.trecho_corpo[:500] if corp and corp.corpo_lido else None)
                # Critic 1: homepage institucional nunca conta como corpo lido
                generica = _eh_generica(d.get("url", ""), d.get("titulo", ""), trecho or "")
                corpo_ok = bool(corp and corp.corpo_lido) and not generica
                if generica and corp and corp.corpo_lido:
                    trecho = None  # descarta boilerplate
                fontes.append(FonteEvidencia(url=d["url"], titulo=d["titulo"],
                                             portal_id=d["fonte"].get("id") or None,
                                             portal_nome=d["fonte"].get("nome", ""),
                                             tipo_fonte="corroboracao",
                                             confianca=(min(max(d["_imp"]["sustenta"], d["_imp"]["refuta"]), 0.5)
                                                        if d.get("_descoberta")
                                                        else max(d["_imp"]["sustenta"], d["_imp"]["refuta"])),
                                             trecho_corpo=trecho,
                                             corpo_lido=corpo_ok,
                                             data_pub=d.get("data_publicacao"),
                                             quote=(trecho[:140] if trecho else d["titulo"][:140]),
                                             tipo_conteudo="noticia",
                                             resumo_juiz=(d["_imp"].get("resumo") or "")[:500] or None,
                                             score_juiz=d["_imp"].get("score"),
                                             relevante=bool(d["_imp"].get("relevante"))))
            n_lidas = sum(1 for d in a_ler if d.get("_corpo"))  # inclui as lidas na descoberta
            motivo = getattr(self.serpapi, "ultimo_motivo", "vazio")
            etapa("descoberta", "ok" if unicas else "parcial",
                  f"{len(unicas)} resultado(s); {n_lidas} de {len(a_ler)} notícias lidas na íntegra "
                  f"(as demais, só pela manchete); {len(relevantes)} sobre o assunto segundo o juiz "
                  f"({n_corpo_lido} delas com o texto lido)"
                  f"{f' [serpapi: {motivo}]' if not unicas else ''}.",
                  [d["url"] for d in relevantes[:5]])
            if not unicas:
                motivo = getattr(self.serpapi, "ultimo_motivo", "vazio")
                cap = getattr(config, "SERPAPI_DAILY_CAP", 100)
                if motivo == "cap":
                    limitacoes.append(
                        f"SerpAPI pausada: teto diário atingido ({cap}/dia). "
                        "Volta amanhã (UTC) ou suba SERPAPI_DAILY_CAP no .env.")
                elif motivo == "erro":
                    limitacoes.append("SerpAPI falhou nesta consulta (detalhe no log do servidor).")
                else:
                    limitacoes.append("SerpAPI sem resultados para as afirmações.")
        else:
            etapa("descoberta", "pulada", "SERPAPI_KEY ausente: só índice local.")
            limitacoes.append("Descoberta web desligada (sem SERPAPI_KEY).")

        # 5. Corroboração (7 gerais, no que foi reunido)
        # Veredito só com relevância julgada: selo sobre outro assunto e página
        # institucional nunca viram "cobertura" (caso água da torneira → BAIXA).
        # Não-julgadas (índice sem LLM) seguem visíveis, mas não decidem.
        corroboraveis = [f.model_dump() for f in fontes
                         if f.tipo_fonte in ("corroboracao", "veredito")
                         and f.relevante is True]
        # Quem contesta a afirmação não é "cobertura" que a reforça: 4 veículos
        # desmentindo não podem virar "presente em 3+ veículos" (reduz propensão).
        # Sem nota do juiz (cap/falha -> lexical) não se sabe o sentido: não conta.
        contagem = corroboracao.contar_independentes(
            [f for f in corroboraveis if f.get("score_juiz") is not None and f["score_juiz"] >= 0])
        for div in corroboracao.divergencias(corroboraveis):
            sinais.append(SinalAnalise(motor="corroboracao",
                                       rotulo=f"{div['tipo']}: {div['campo']}",
                                       valor=f"{div['leitura']} ({'; '.join(div['valores'][:4])})",
                                       confianca=0.8, evidencias=[f.url for f in fontes[:5]]))
        if contagem["n"] >= 3:
            sinais.append(SinalAnalise(motor="corroboracao", rotulo="cobertura ampla",
                                       valor="informação presente em 3+ veículos independentes",
                                       confianca=0.75, evidencias=[f.url for f in fontes[:5]]))
        elif (corroboracao.contar_independentes(corroboraveis)["n"] <= 1 and fontes
              and not any(s.motor == "veredito-existente" for s in sinais)):
            # "Isolada" mede presença do ASSUNTO (inclui quem contesta): desmentido
            # em 5 veículos não é "ausente nos veículos".
            # "Isolada" só quando NÃO há veredito: uma checagem encontrada já é achado, não ausência.
            sinais.append(SinalAnalise(motor="corroboracao", rotulo="cobertura isolada",
                                       valor="informação ausente ou isolada nos veículos consultados",
                                       confianca=0.7, evidencias=[f.url for f in fontes[:3]]))
        etapa("corroboracao", "ok",
              f"{corroboracao.contar_independentes(corroboraveis)['n']} veículo(s) independente(s) "
              f"tratam do assunto; {contagem['n']} sem contestar o conteúdo.")

        # 6. Modelo próprio (RF08/09) — mock explícito até o real
        await avisar("Rodando o modelo de detecção e a análise de padrões…")
        try:
            det = await asyncio.to_thread(self.detector.analisar, texto_base[:5000])
            conf_modelo = 0.4 if det.get("mock") else 0.85  # mock pesa pouco e declara
            sinais.append(SinalAnalise(motor="modelo-fake",
                                       rotulo=f"modelo {det['modelo']}{' (PLACEHOLDER)' if det.get('mock') else ''}",
                                       valor=str(det["prob_fake"]), confianca=conf_modelo))
            if det.get("mock"):
                limitacoes.append("Modelo de detecção ainda é um placeholder (RF08 parcial).")
            etapa("modelo", "ok", f"prob_fake={det['prob_fake']} ({det['modelo']}).")
        except Exception as e:
            etapa("modelo", "falha", str(e)[:200])
            limitacoes.append("Modelo de detecção indisponível nesta consulta.")

        # 7. Padrões via LLM local (fallback regex determinístico)
        try:
            pads = await asyncio.to_thread(padroes_llm.analisar_padroes, texto_base[:5000], usar_llm)
            if pads["padroes"]:
                nomes = ", ".join(p["padrao"] for p in pads["padroes"][:5])
                sinais.append(SinalAnalise(motor="llm-padroes", rotulo="padrões de desinformação",
                                           valor=f"encontrados: {nomes}", confianca=0.65))
            etapa("padroes", "ok", f"{len(pads['padroes'])} padrão(ões) via {pads['motor']}.")
        except Exception as e:
            etapa("padroes", "falha", str(e)[:200])

        # 8. Agregação (templates neutros, RF12/RNF02)
        await avisar("Montando o relatório…")
        agg = agregar(sinais)
        prop = agg["propensao"]
        for s_ in sinais:
            log.info("  sinal %-18s conf=%s %s | %s", s_.motor, s_.confianca, s_.rotulo,
                     resumo_texto(s_.valor, 60))
        log.info("  agregador: score=%s -> %s", agg.get("score"), prop)
        extras: List[str] = []
        # Loop 3: veredito só conta se confianca>=0.6 e fonte não-genérica.
        tem_veredito = any(s.motor == "veredito-existente" and (s.confianca or 0) >= 0.6
                           for s in sinais)
        # Corpo só ancora veredito se JULGADO relevante: corpo lido fora do tema
        # (selo sobre outro assunto) não sustenta nada (caso água da torneira).
        tem_corpo = any(getattr(f, "corpo_lido", False) and not _eh_generica(
            f.url, f.titulo, f.trecho_corpo or "") and f.relevante is True for f in fontes)
        n_corpo = sum(1 for f in fontes if getattr(f, "corpo_lido", False) and not _eh_generica(
            f.url, f.titulo, f.trecho_corpo or "") and f.relevante is True)
        # Opinião/sátira: contém o veredito só quando NÃO há evidência forte.
        # Notícia citando "precisamos/deveria" com veredito+corpo não é rebaixada.
        if eh_opiniao and prop != "indeterminada" and not (tem_veredito and n_corpo >= 1):
            prop = "indeterminada"
            extras.append("O texto tem marcas de opinião ou sátira, por isso não foi tratado como fato.")
        # Claim vago (round C): comparativo sem indicador/período não é
        # falsificável — contém em indeterminada e pede especificação.
        if eh_vago and prop != "indeterminada":
            prop = "indeterminada"
            extras.append("A afirmação é vaga demais para checar: diga qual indicador e qual período.")
        if not tem_veredito and not tem_corpo and prop in ("baixa", "media", "alta"):
            prop = "indeterminada"
            if any(f.relevante is True for f in fontes):
                # Há fonte julgada sobre o tema, mas só pela manchete: diz isso,
                # não "nada sobre o tema" (contradizia o "1 contesta" do header).
                extras.append("As fontes sobre o assunto foram avaliadas só pela manchete, "
                              "sem o texto completo, então faltam elementos para estimar a propensão.")
                limitacoes.append("Fontes sobre o tema lidas só pela manchete: estimativa contida em indeterminada.")
            else:
                extras.append("Não encontramos checagens nem notícias que tratem do assunto, "
                              "então faltam elementos para estimar a propensão.")
                limitacoes.append("Sem evidência relevante (nada sobre o tema nas fontes consultadas): veredito contido em indeterminada.")
        elif eh_rumor and not tem_veredito:
            if not tem_corpo and prop in ("baixa", "media"):
                prop = "indeterminada"
                extras.append("É um relato sem fonte: vale procurar a origem da informação.")
        etapa("agregacao", "ok", f"propensão {prop} a partir de {len(sinais)} sinais.")
        # Ordena úteis+corpo_lido primeiro ANTES de fatiar (não descarta deep-crawl).
        # Julgado-irrelevante vai p/ o fim (resto auditável, nunca manchete).
        def _chave_f(f):
            try:
                gen = _eh_generica(f.url, f.titulo, f.trecho_corpo or "")
            except Exception:
                gen = False
            return (gen or f.relevante is False, not getattr(f, "corpo_lido", False),
                    -(f.confianca or 0))
        fontes_ord = sorted(fontes, key=_chave_f)
        if prop != agg["propensao"]:
            log.info("  propensão ajustada %s -> %s: %s", agg["propensao"], prop, " ".join(extras))
        justificativa = montar_justificativa(prop, sinais, " ".join(extras))
        log.info("checagem concluída em %.1fs: %s | %d fonte(s), %d limitação(ões)",
                 time.time() - t_inicio, prop, len(fontes), len(limitacoes))
        return self._relatorio(entrada, prop, justificativa, sinais,
                               fontes_ord[: config.MAX_EVIDENCIAS * 2], etapas, limitacoes)

    @staticmethod
    def _relatorio(entrada, propensao, justificativa, sinais, fontes, etapas, limitacoes):
        # Loop 3: header conta fontes úteis (não-genéricas E não julgadas-irrelevantes);
        # lista ordenada úteis primeiro
        def _g(f):
            try:
                return _eh_generica(f.url, f.titulo, f.trecho_corpo or "")
            except Exception:
                return False
        uteis = [f for f in fontes if not _g(f) and f.relevante is not False]
        resto = [f for f in fontes if _g(f)]
        fontes_ord = uteis + resto
        # Header conta só o que o juiz julgou relevante (índice não julgado não
        # é "fonte sobre o assunto").
        julgadas = [f for f in uteis if f.relevante is True]
        hdr = gerar_header(propensao, sinais, n_fontes=len(julgadas),
                           n_corpo_lido=sum(1 for f in julgadas if getattr(f, "corpo_lido", False)),
                           n_contestam=sum(1 for f in julgadas if (f.score_juiz or 0) < 0),
                           n_confirmam=sum(1 for f in julgadas if (f.score_juiz or 0) > 0))
        if not uteis and fontes:
            limitacoes = list(limitacoes) + ["Sem evidência relevante (só homepages/seções): prefira buscar a fonte original."]
        return RelatorioChecagem(propensao=propensao, justificativa=justificativa, sinais=sinais,
                                 fontes=fontes_ord, etapas=etapas, limitacoes=limitacoes,
                                 perguntas_guia=perguntas_guia(), consulta=entrada,
                                 header=hdr["header"], why_1linha=hdr["why_1linha"])
