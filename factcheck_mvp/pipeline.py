"""Orquestrador das 7 etapas. Nunca levanta: falha vira etapa+limitação (RF11)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import afirmacoes, config, corroboracao, implicacao, padroes_llm
from .agregador import agregar, perguntas_guia
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


async def _nada(_: str) -> None:
    return None


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

        def etapa(nome: str, status: str, detalhe: str = "", fontes_urls: Optional[List[str]] = None):
            etapas.append(EtapaRecibo(nome=nome, status=status, detalhe=detalhe, fontes=fontes_urls or []))

        async def avisar(msg: str) -> None:
            try:
                await progresso(msg)
            except Exception:
                pass  # callback de UI nunca quebra o pipeline (RF11)

        # 1. Normalização
        texto_base = entrada.conteudo.strip()
        if entrada.tipo == "link" and not texto_base.startswith(("http://", "https://", "www.")):
            pass  # classificar_entrada já garante o formato; segue como texto citável
        etapa("recebimento", "ok", f"Entrada do tipo {entrada.tipo} ({len(texto_base)} caracteres).")
        await avisar("Recebi. Extraindo as afirmações verificáveis…")

        # 2. Afirmações (LLM local; laya não gera texto)
        try:
            afs: List[Afirmacao] = await asyncio.to_thread(
                afirmacoes.extrair_afirmacoes, texto_base, 0, usar_llm)
        except Exception as e:  # pragma: no cover
            afs = afirmacoes.extrair_afirmacoes(texto_base, max_n=0, usar_llm=False)
            limitacoes.append(f"Extração de afirmações via fallback: {e}")
        etapa("afirmacoes", "ok" if afs else "falha", f"{len(afs)} afirmação(ões).")
        if not afs:
            return self._relatorio(entrada, "indeterminada",
                                   "Não identifiquei afirmação factual para checar.", sinais, fontes, etapas,
                                   limitacoes + ["Nenhuma afirmação factual encontrada."])
        await avisar(f"Buscando nas bases de checagem ({len(afs)} afirmação(ões))…")

        # 3. Índices locais: vereditos (13 checagem) + notícias (7 gerais, RF05).
        # Piso de score: 1 token em comum não vira evidência; confiança escala com o score.
        achados_ver: List[Dict[str, Any]] = []
        achados_not: List[Dict[str, Any]] = []
        for af in afs:
            achados_ver += [{**h, "afirmacao": af.texto} for h in self.idx_ver.buscar(af.texto, top_k=5)
                            if h["score"] >= config.INDICE_SCORE_MIN]
            achados_not += [{**h, "afirmacao": af.texto} for h in self.idx_not.buscar(af.texto, top_k=5)
                            if h["score"] >= config.INDICE_SCORE_MIN]
        for h in achados_ver[: config.MAX_EVIDENCIAS]:
            d = h["doc"]
            conf = min(0.9, round(0.4 + 0.15 * h["score"], 3))
            fontes.append(FonteEvidencia(url=d.get("url", ""), titulo=d.get("titulo", ""),
                                         portal_id=(d.get("fonte") or {}).get("id"),
                                         portal_nome=(d.get("fonte") or {}).get("nome", ""),
                                         tipo_fonte="veredito", veredito=d.get("veredito"),
                                         selo_original=d.get("selo_original"), confianca=conf))
            if not d.get("veredito"):
                continue
            # Veredito só vira sinal se trata DA afirmação (laya): selo sobre
            # outro assunto não contamina a consulta (ex: VERDADEIRO sobre golpes
            # não abona "bets proibidas").
            imp = await asyncio.to_thread(
                implicacao.implicacao,
                f"{d.get('titulo','')} {d.get('corpo_texto','') or ''}", h["afirmacao"])
            if not imp.get("relevante"):
                continue
            portal = (d.get("fonte") or {}).get("nome", "checador")
            sus, ref = imp.get("sustenta", 0.0), imp.get("refuta", 0.0)
            if ref >= 0.6:
                # Peça refuta a afirmação: selo VERDADEIRO aqui fortalece a refutação.
                sinais.append(SinalAnalise(motor="veredito-existente",
                                           rotulo=f"selo do portal ({portal}): {d['veredito']} + fonte refuta",
                                           valor=f"afirmação refutada (selo {d['veredito']})",
                                           confianca=min(conf, round(ref, 3)),
                                           evidencias=[d.get("url", "")]))
            elif sus >= 0.6:
                sinais.append(SinalAnalise(motor="veredito-existente",
                                           rotulo=f"selo do portal ({portal}): {d['veredito']} + fonte sustenta",
                                           valor=f"afirmação sustentada (selo {d['veredito']})",
                                           confianca=min(conf, round(sus, 3)),
                                           evidencias=[d.get("url", "")]))
            else:
                sinais.append(SinalAnalise(motor="veredito-existente",
                                           rotulo=f"selo do portal ({portal}): {d['veredito']}",
                                           valor=str(d["veredito"]), confianca=conf,
                                           evidencias=[d.get("url", "")]))
            if d.get("confianca_veredito") is not None:  # veredito veio do laya no crawl
                sinais.append(SinalAnalise(motor="laya", rotulo="veredito confirmado pelo classificador",
                                           valor=str(d["veredito"]), confianca=d["confianca_veredito"],
                                           evidencias=[d.get("url", "")]))
        for h in achados_not[: config.MAX_EVIDENCIAS]:
            d = h["doc"]
            if not d.get("url"):
                continue
            fontes.append(FonteEvidencia(url=d["url"], titulo=d.get("titulo", ""),
                                         portal_id=(d.get("fonte") or {}).get("id"),
                                         portal_nome=(d.get("fonte") or {}).get("nome", ""),
                                         tipo_fonte="corroboracao",
                                         confianca=min(0.85, round(0.4 + 0.15 * h["score"], 3))))
        if achados_ver or achados_not:
            etapa("base-checagem", "ok",
                  f"{len(achados_ver)} checagem(ns) e {len(achados_not)} notícia(s) no índice local.",
                  [h["doc"].get("url", "") for h in (achados_ver + achados_not)[:5]])
        else:
            etapa("base-checagem", "parcial", "Nenhuma checagem prévia encontrada no índice.")
            limitacoes.append("Sem checagem prévia no índice local.")

        # 4. Descoberta fresca (SerpAPI) + implicação laya (concorrente, com teto)
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
            vistos, unicas = set(), []
            for d in descobertas:
                if d["url"] and d["url"] not in vistos:
                    vistos.add(d["url"])
                    unicas.append(d)

            async def _implicar(d: Dict[str, Any]) -> Dict[str, Any]:
                # Só há título+metadados (sem corpo: deep crawl é extensão futura).
                texto = d.get("titulo", "")
                d["_imp"] = await asyncio.to_thread(implicacao.implicacao, texto, d["_afirmacao"])
                return d

            try:
                candidatas = await asyncio.wait_for(
                    asyncio.gather(*[_implicar(d) for d in unicas[:12]]), timeout=90)
            except asyncio.TimeoutError:
                candidatas = []
                limitacoes.append("Implicação laya atingiu o teto (resultado parcial).")
            relevantes = [d for d in candidatas if d.get("_imp", {}).get("relevante")]
            n_sus = sum(1 for d in relevantes if d["_imp"].get("sustenta", 0.0) >= 0.6)
            n_ref = sum(1 for d in relevantes if d["_imp"].get("refuta", 0.0) >= 0.6)
            if n_ref:
                sinais.append(SinalAnalise(motor="corroboracao", rotulo="fontes refutam a afirmação",
                                           valor=f"{n_ref} fonte(s) contestam a afirmação",
                                           confianca=0.8,
                                           evidencias=[d["url"] for d in relevantes if d["_imp"].get("refuta", 0.0) >= 0.6][:5]))
            if n_sus:
                # Evidência só-título sustenta "barato" (manchete afirma fácil);
                # refutação por manchete é mais compromissada. Tier documentado.
                # Desconto extra se a manchete é modal/condicional ("serão",
                # "projeto", "defende") — cobre o tema sem afirmar o fato.
                modais = re.compile(r"\b(serão|será|podem|pode|poderá|projeto|defende|estuda|quer|pretende|discute|avalia|se depender)\b", re.I)
                titulos_sus = [d["titulo"] for d in relevantes if d["_imp"].get("sustenta", 0.0) >= 0.6]
                n_modais = sum(1 for t in titulos_sus if modais.search(t or ""))
                conf_sus = 0.5 if not n_modais else 0.25
                sinais.append(SinalAnalise(motor="corroboracao", rotulo="fontes sustentam a afirmação (só título)",
                                           valor=f"{n_sus} manchete(s) sustentam ({n_modais} condicionais); corpo não lido",
                                           confianca=conf_sus,
                                           evidencias=[d["url"] for d in relevantes if d["_imp"].get("sustenta", 0.0) >= 0.6][:5]))
                limitacoes.append("Implicação feita só em manchetes (sem corpo: deep crawl futuro).")
            for d in relevantes[: config.MAX_EVIDENCIAS]:
                fontes.append(FonteEvidencia(url=d["url"], titulo=d["titulo"],
                                             portal_id=d["fonte"].get("id") or None,
                                             portal_nome=d["fonte"].get("nome", ""),
                                             tipo_fonte="corroboracao",
                                             confianca=max(d["_imp"]["sustenta"], d["_imp"]["refuta"])))
            etapa("descoberta", "ok" if unicas else "parcial",
                  f"{len(unicas)} resultado(s), {len(relevantes)} relevante(s) após implicação laya "
                  f"(só títulos: corpo exige deep crawl).",
                  [d["url"] for d in relevantes[:5]])
            if not unicas:
                limitacoes.append("SerpAPI sem resultados para as afirmações.")
        else:
            etapa("descoberta", "pulada", "SERPAPI_KEY ausente: só índice local.")
            limitacoes.append("Descoberta web desligada (sem SERPAPI_KEY).")

        # 5. Corroboração (7 gerais, no que foi reunido)
        corroboraveis = [f.model_dump() for f in fontes if f.tipo_fonte in ("corroboracao", "veredito")]
        contagem = corroboracao.contar_independentes(corroboraveis)
        for div in corroboracao.divergencias(corroboraveis):
            sinais.append(SinalAnalise(motor="corroboracao",
                                       rotulo=f"{div['tipo']}: {div['campo']}",
                                       valor=f"{div['leitura']} ({'; '.join(div['valores'][:4])})",
                                       confianca=0.8, evidencias=[f.url for f in fontes[:5]]))
        if contagem["n"] >= 3:
            sinais.append(SinalAnalise(motor="corroboracao", rotulo="cobertura ampla",
                                       valor="informação presente em 3+ veículos independentes",
                                       confianca=0.75, evidencias=[f.url for f in fontes[:5]]))
        elif contagem["n"] <= 1 and fontes and not any(s.motor == "veredito-existente" for s in sinais):
            # "Isolada" só quando NÃO há veredito: uma checagem encontrada já é achado, não ausência.
            sinais.append(SinalAnalise(motor="corroboracao", rotulo="cobertura isolada",
                                       valor="informação ausente ou isolada nos veículos consultados",
                                       confianca=0.7, evidencias=[f.url for f in fontes[:3]]))
        etapa("corroboracao", "ok", f"{contagem['n']} fonte(s) independente(s).")

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
        etapa("agregacao", "ok", f"propensão {agg['propensao']} a partir de {len(sinais)} sinais.")
        return self._relatorio(entrada, agg["propensao"], agg["justificativa"], sinais,
                               fontes[: config.MAX_EVIDENCIAS * 2], etapas, limitacoes)

    @staticmethod
    def _relatorio(entrada, propensao, justificativa, sinais, fontes, etapas, limitacoes):
        return RelatorioChecagem(propensao=propensao, justificativa=justificativa, sinais=sinais,
                                 fontes=fontes, etapas=etapas, limitacoes=limitacoes,
                                 perguntas_guia=perguntas_guia(), consulta=entrada)
