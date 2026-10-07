"""Pipeline ponta a ponta com busca, deep crawl e juiz FALSOS (sem rede).

O juiz falso é uma regra sobre o PRÓPRIO prompt do juiz (lê os itens JSON e classifica
pelo trecho), então o caminho real é exercitado: seleção → crawl → clusters → prompt →
citação verificada → decidir → relatório.
"""
import asyncio
import json
import re

import pytest

from factcheck_mvp import config, llm
from factcheck_mvp import pipeline as pl
from factcheck_mvp.aprofundar import CorpoLido
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.modelo_fake import MockDetector
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta
from factcheck_mvp import serpapi_layer as camada


class FakeSerp:
    ativo = True
    ultimo_motivo = "ok"
    uso_hoje = 0

    def __init__(self, resultados):
        self.resultados = resultados  # [{link, title, snippet}]

    def buscar(self, q):
        return {"organic_results": self.resultados}

    def engine_de(self, q):
        return "google"

    def result_key(self, q):
        return "organic_results"


def _juiz_falso(regras=None, citacao_inventada=False):
    """Classifica cada item do prompt: 'é falso'/'não há evidência' -> REFUTA; 'confirma' -> SUSTENTA;
    'circula' -> RELATA; senão NAO_TRATA. Citação = a frase do trecho que casou."""
    regras = regras or [(r"[^.]*(é falso|não há evidência|desmente)[^.]*", "REFUTA"),
                        (r"[^.]*(confirma|comprovou)[^.]*", "SUSTENTA"),
                        (r"[^.]*(circula|afirma em vídeo)[^.]*", "RELATA_SEM_ENDOSSO")]

    def fake(messages, max_tokens, timeout_s, finalidade):
        conteudo = messages[-1]["content"]
        if finalidade == "afirmacoes":
            texto = conteudo.split('"""')[1].strip()
            m = re.match(r"(?i)é falso que (.+)", texto)
            if m:
                nuc = m.group(1)[0].upper() + m.group(1)[1:]
                return json.dumps([{"afirmacao": texto, "nucleo": nuc, "polaridade": "nega",
                                    "consulta": nuc.lower()}]), "fake"
            return json.dumps([{"afirmacao": texto, "nucleo": texto, "polaridade": "afirma",
                                "consulta": texto.lower()}]), "fake"
        if finalidade == "padroes":
            return "NENHUM", "fake"
        itens = [json.loads(l) for l in conteudo.split("ITENS:\n", 1)[1].splitlines() if l.strip()]
        out = []
        for it in itens:
            texto = f"{it['titulo']}\n{it['trecho']}"
            classe, cit = "NAO_TRATA", ""
            for rx, c in regras:
                m = re.search(rx, texto, re.I)
                if m:
                    classe, cit = c, m.group(0).strip()
                    break
            if citacao_inventada and classe != "NAO_TRATA":
                cit = "um estudo de Harvard comprovou tudo isso definitivamente ontem"
            out.append({"i": it["i"], "classe": classe, "citacao": cit})
        return json.dumps({"itens": out}), "fake"
    return fake


@pytest.fixture
def amb(monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "0")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "SERP_ESTRATEGIA", "simples")
    monkeypatch.setattr(config, "SERP_ENGINE", "google")
    monkeypatch.setattr(config, "DISCOVERY_MAX_SITES", 0)
    monkeypatch.setattr(llm, "_local", _juiz_falso())
    corpos = {}

    async def fake_aprofundar(cands, catalogo, **kw):
        out = {}
        for d in cands:
            c = corpos.get(d["url"])
            if c is None:
                out[d["url"]] = CorpoLido(url=d["url"], erro="HTTP 403")
            else:
                texto, vp = c if isinstance(c, tuple) else (c, None)
                out[d["url"]] = CorpoLido(url=d["url"], final_url=d["url"], trecho_corpo=texto[:3000],
                                          corpo_lido=True, texto_completo=texto, metodo="fake",
                                          veredito_pagina=vp)
        return out
    monkeypatch.setattr(pl, "aprofundar", fake_aprofundar)
    return corpos


def _rodar(texto, resultados, indice=None, usar_llm=True):
    pipe = Pipeline(Catalogo.carregar(), indice if indice is not None else Indice.de_checagens([]), Indice(),
                    serpapi=FakeSerp(resultados), detector=MockDetector())
    return asyncio.run(pipe.executar(EntradaConsulta(tipo="titulo", conteudo=texto), usar_llm=usar_llm))


def _pag(url, titulo, frase):
    dom = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    contexto = " ".join(f"Apuração própria de {dom}, parágrafo {k}, com detalhes do caso." for k in range(12))
    corpo = f"{titulo}. {contexto} {frase} Mais contexto de {dom} sem relação direta."
    return {"link": url, "title": titulo, "snippet": frase[:120]}, corpo


TRES_REFUTAM = [
    _pag("https://g1.globo.com/saude/noticia/2024/01/cafe.ghtml", "Café cura câncer? Checagem",
         "É falso que café cura câncer, segundo o INCA."),
    _pag("https://www.estadao.com.br/estadao-verifica/cafe-cancer/", "Post sobre café e câncer",
         "Não há evidência de que café cura câncer, dizem oncologistas."),
    _pag("https://www.bbc.com/portuguese/articles/cafe123", "O que a ciência diz sobre café",
         "Pesquisadores desmente que café cura câncer em revisão ampla."),
]


def _prep(amb, pags):
    for r, corpo in pags:
        amb[r["link"]] = corpo
    return [r for r, _ in pags]


def test_tres_fontes_refutam_da_alta(amb):
    rel = _rodar("Café cura câncer", _prep(amb, TRES_REFUTAM))
    assert rel.propensao == "alta", rel.justificativa
    assert rel.decisao["contagem"]["refuta"] == 3 and len(rel.decisao["votos"]) == 3
    assert all(f.postura == "REFUTA" and f.citacao_verificada for f in rel.fontes)
    assert rel.header.startswith("🔴") and rel.justificativa.startswith("Alta propensão de ser fake news")


def test_tres_fontes_sustentam_da_baixa(amb):
    pags = [_pag(f"https://www.{d}/noticia/2024/aedes", "Dengue e o Aedes",
                 "O Ministério da Saúde confirma que a dengue é transmitida pelo Aedes aegypti.")
            for d in ("g1.globo.com", "bbc.com", "folha.uol.com.br")]
    rel = _rodar("A dengue é transmitida pelo Aedes aegypti", _prep(amb, pags))
    assert rel.propensao == "baixa", rel.justificativa


def test_so_relatos_da_indeterminada(amb):
    pags = [_pag(f"https://www.{d}/noticia/2024/video", "Vídeo viral sobre café",
                 "Circula nas redes um vídeo sobre café e câncer.") for d in ("g1.globo.com", "bbc.com")]
    rel = _rodar("Café cura câncer", _prep(amb, pags))
    assert rel.propensao == "indeterminada"
    assert rel.decisao["contagem"]["relata"] == 2


def test_sem_juiz_da_indeterminada_e_nao_fabrica_relevancia(amb, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Unsloth fora do ar")
    monkeypatch.setattr(llm, "_local", boom)
    rel = _rodar("Café cura câncer", _prep(amb, TRES_REFUTAM))
    assert rel.propensao == "indeterminada"
    assert all(f.relevante is None and f.postura is None for f in rel.fontes)
    assert any("Sem julgamento de conteúdo" in l for l in rel.limitacoes)
    assert not rel.decisao["votos"]


def test_usuario_nega_e_fontes_refutam_boato_da_baixa(amb):
    rel = _rodar("É falso que café cura câncer", _prep(amb, TRES_REFUTAM))
    assert rel.propensao == "baixa", rel.justificativa
    assert rel.decisao["por_afirmacao"][0]["polaridade"] == "nega"


def test_citacao_inexistente_vira_nao_trata(amb, monkeypatch):
    monkeypatch.setattr(llm, "_local", _juiz_falso(citacao_inventada=True))
    rel = _rodar("Café cura câncer", _prep(amb, TRES_REFUTAM))
    assert rel.propensao == "indeterminada"
    assert all(f.postura == "NAO_TRATA" and f.citacao_verificada is False for f in rel.fontes)
    assert rel.decisao["contagem"]["citacao_invalida"] == 3


def test_mesma_url_no_indice_e_na_web_conta_uma_vez(amb, monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://www.aosfatos.org/noticias/cafe-cura-cancer-falso/"
    idx = Indice.de_checagens([{"url": url, "titulo": "É falso que café cura câncer",
                                "afirmacao_checada": "Café cura câncer", "selo_original": "Falso",
                                "veredito": "FALSO", "agencia": "aos-fatos",
                                "trecho": "É falso que café cura câncer, segundo especialistas."}]
                               + [{"url": f"https://lupa.uol.com.br/x{i}", "titulo": t, "afirmacao_checada": t,
                                   "selo_original": "Falso", "veredito": "FALSO", "agencia": "lupa", "trecho": t}
                                  for i, t in enumerate(["Vacina altera o DNA humano", "Urnas foram fraudadas em 2022",
                                                         "Limão em jejum cura diabetes", "Governo vai confiscar poupança",
                                                         "Água gelada causa gripe"])])
    web = [{"link": url + "?utm_source=twitter", "title": "É falso que café cura câncer",
            "snippet": "É falso que café cura câncer, segundo especialistas."}]
    assert idx.buscar_checagens("Café cura câncer")  # o atalho acha a checagem
    rel = _rodar("Café cura câncer", web, indice=idx)
    assert len([f for f in rel.fontes if "aosfatos" in f.url]) == 1
    assert len(rel.decisao["votos"]) == 1, rel.decisao
    # 1 selo FALSO aplicável (juiz disse REFUTA) basta p/ alta, mas conta uma vez só
    assert rel.decisao["votos"][0]["peso"] <= 1.5 and rel.propensao == "alta", (rel.decisao, [e.detalhe for e in rel.etapas])


def test_selo_verdadeiro_do_fato_ou_fake_da_baixa(amb):
    url = "https://g1.globo.com/fato-ou-fake/noticia/2024/05/10/e-fato-que-ponte.ghtml"
    frase = "Circula nas redes a informação sobre a inauguração da ponte, verificada pelo Fato ou Fake."
    r, corpo = _pag(url, "Ponte Salvador-Itaparica foi inaugurada", frase)
    amb[url] = (corpo, {"selo_original": "#FATO", "veredito": "VERDADEIRO",
                        "afirmacao_checada": "Ponte Salvador-Itaparica foi inaugurada", "agencia": "g1-fato-ou-fake"})
    rel = _rodar("Ponte Salvador-Itaparica foi inaugurada", [r])
    assert rel.propensao == "baixa", rel.justificativa
    assert rel.decisao["vereditos_aplicados"][0]["veredito"] == "VERDADEIRO"


def test_quatro_dominios_com_corpo_de_agencia_um_cluster(amb):
    corpo = ("SÃO PAULO - Especialistas ouvidos afirmam que não há evidência de que café cura câncer. "
             + "O levantamento ouviu oncologistas de vários estados sobre o tema. " * 10 + "(Estadão Conteúdo)")
    res = []
    for d in ("istoe.com.br", "em.com.br", "gazetadopovo.com.br", "correiobraziliense.com.br"):
        u = f"https://www.{d}/noticia/2024/cafe-cancer"
        amb[u] = corpo
        res.append({"link": u, "title": f"Café e câncer ({d})", "snippet": "especialistas"})
    rel = _rodar("Café cura câncer", res)
    assert len(rel.decisao["votos"]) == 1
    assert rel.decisao["votos"][0]["cluster"] == "agencia:estadao-conteudo"
    assert rel.propensao == "media"  # uma voz só (republicada 4x) não chega a alta


def test_estilo_nao_entra_no_nivel(amb):
    """Mesma evidência, texto em caixa alta com apelo: nível idêntico; estilo só no relatório."""
    a = _rodar("Café cura câncer", _prep(amb, TRES_REFUTAM))
    b = _rodar("CAFÉ CURA CÂNCER!!! COMPARTILHE", _prep(amb, TRES_REFUTAM))
    assert a.propensao == b.propensao == "alta"
    assert a.decisao["log_odds"] == b.decisao["log_odds"]
    assert any(s.motor == "modelo-fake" and "não indica veracidade" in s.rotulo for s in b.sinais)


def test_claim_vago_contido_e_pede_metrica():
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""), detector=MockDetector())
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="texto", conteudo="A economia do Brasil só piorou no governo Lula"),
        usar_llm=False))
    assert rel.propensao == "indeterminada"
    assert any("vaga" in lim.lower() for lim in rel.limitacoes), rel.limitacoes


def test_claim_com_indicador_nao_e_vago():
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""), detector=MockDetector())
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="texto", conteudo="O desemprego piorou e chegou a 9,5% no trimestre passado"),
        usar_llm=False))
    assert not any("vaga" in lim.lower() for lim in rel.limitacoes), rel.limitacoes


def test_sem_busca_e_sem_llm_indeterminada():
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""), detector=MockDetector())
    rel = asyncio.run(pipe.executar(EntradaConsulta(tipo="titulo", conteudo="Receita de bolo de cenoura"),
                                    usar_llm=False))
    assert rel.propensao == "indeterminada"
    assert any(e.nome == "descoberta" and e.status == "pulada" for e in rel.etapas)


def test_base_aplicavel_pula_web(amb, monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://www.aosfatos.org/noticias/cafe-cura-cancer-falso/"
    idx = Indice.de_checagens([{"url": url, "titulo": "É falso que café cura câncer",
        "afirmacao_checada": "Café cura câncer", "selo_original": "Falso",
        "veredito": "FALSO", "agencia": "aos-fatos",
        "trecho": "É falso que café cura câncer, segundo especialistas."}]
        # Distratores p/ o BM25 ter IDF (índice de 1 doc só nunca casa: scores ~0;
        # mesmo padrão do test_mesma_url_no_indice_e_na_web_conta_uma_vez). Alvo verbatim.
        + [{"url": f"https://lupa.uol.com.br/x{i}", "titulo": t, "afirmacao_checada": t,
            "selo_original": "Falso", "veredito": "FALSO", "agencia": "lupa", "trecho": t}
           for i, t in enumerate(["Vacina altera o DNA humano", "Urnas foram fraudadas em 2022",
                                  "Limão em jejum cura diabetes", "Governo vai confiscar poupança",
                                  "Água gelada causa gripe"])])
    amb[url] = "É falso que café cura câncer, segundo especialistas. " * 50
    chamadas = {"n": 0}
    class Contadora(FakeSerp):
        def buscar(self, q):
            chamadas["n"] += 1
            return super().buscar(q)
    pipe = Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=Contadora([]), detector=MockDetector())
    import asyncio
    rel = asyncio.run(pipe.executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer")))
    assert chamadas["n"] == 0
    assert any(e.nome == "descoberta" and e.status == "pulada" and "checagem aplicável" in e.detalhe for e in rel.etapas)
    assert rel.propensao == "alta"

def test_base_inaplicavel_chama_web(amb, monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    idx = Indice.de_checagens([{"url": "https://lupa.uol.com.br/x", "titulo": "Reportagem sobre café",
        "afirmacao_checada": "Café cura câncer", "selo_original": None, "veredito": None,
        "agencia": "lupa", "trecho": "Reportagem sobre café."}]
        # Distratores p/ o BM25 ter IDF (índice de 1 doc só nunca casa; ver teste acima).
        + [{"url": f"https://lupa.uol.com.br/y{i}", "titulo": t, "afirmacao_checada": t,
            "selo_original": "Falso", "veredito": "FALSO", "agencia": "lupa", "trecho": t}
           for i, t in enumerate(["Vacina altera o DNA humano", "Urnas foram fraudadas em 2022",
                                  "Limão em jejum cura diabetes", "Governo vai confiscar poupança",
                                  "Água gelada causa gripe"])])
    r, corpo = _pag("https://g1.globo.com/x", "Café cura câncer? Checagem", "É falso que café cura câncer, segundo o INCA.")
    amb["https://g1.globo.com/x"] = corpo
    import asyncio
    rel = asyncio.run(Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=FakeSerp([r]), detector=MockDetector()).executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer")))
    # Guarda anti-vácuo: a base precisa ter devolvido a checagem (senão o gate nem é exercitado)
    assert any(e.nome == "base-checagem" and e.status == "ok" for e in rel.etapas)
    assert not any(e.nome == "descoberta" and e.status == "pulada" and "checagem aplicável" in e.detalhe for e in rel.etapas)

def test_crawl_antes_do_filtro_lexical(amb, monkeypatch):
    """Crawl-primeiro: overlap baixo no título/snippet não exclui do crawl nem do juiz.

    3 peças com teto JUIZ_MAX_NOTICIAS=2 (reproduz o corte de hoje com 3 peças):
    as 3 têm corpo lido e as 3 são julgadas; a de overlap ~0, cujo corpo traz os
    termos da afirmação, é julgada pelo corpo (REFUTA)."""
    monkeypatch.setattr(config, "JUIZ_MAX_NOTICIAS", 2)
    a1, c1 = _pag("https://g1.globo.com/saude/noticia/2024/01/cafe-cura-cancer-a.ghtml",
                  "Café cura câncer? Checagem",
                  "É falso que café cura câncer, segundo o INCA.")
    a2, c2 = _pag("https://www.estadao.com.br/estadao-verifica/2024/02/cafe-cancer-b/",
                  "Café e câncer: o que diz a ciência",
                  "Não há evidência de que café cura câncer, dizem oncologistas.")
    url_b = "https://www.exemplo.com.br/saude/2024/03/boletim-semanal-c/"
    b = {"link": url_b, "title": "Boletim de saúde da semana",
         "snippet": "Resumo semanal da redação com notas curtas."}
    dom_b = "www.exemplo.com.br"
    contexto_b = " ".join(f"Apuração própria de {dom_b}, parágrafo {k}, com detalhes do caso." for k in range(12))
    corpo_b = (f"Boletim de saúde da semana. {contexto_b} "
               "É falso que café cura câncer, segundo o INCA. "
               f"Mais contexto de {dom_b} sem relação direta.")
    amb[a1["link"]] = c1
    amb[a2["link"]] = c2
    amb[url_b] = corpo_b
    rel = _rodar("Café cura câncer", [a1, a2, b])
    juiz = next(e for e in rel.etapas if e.nome == "juiz")
    assert "3 julgamento" in juiz.detalhe, juiz.detalhe
    assert all(f.corpo_lido for f in rel.fontes), [(f.url, f.corpo_lido) for f in rel.fontes]
    baixa = next(f for f in rel.fontes if "boletim" in f.url)
    assert baixa.postura == "REFUTA", baixa


def test_checagem_antiga_para_fato_de_hoje_nao_pula_web(amb, monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://boatos.org/x-antiga"
    idx = Indice.de_checagens([{"url": url, "titulo": "Bolsonaro alta hospital",
        "afirmacao_checada": "Bolsonaro recebeu alta do hospital", "selo_original": "Falso",
        "veredito": "FALSO", "agencia": "boatos-org", "data_pub": "2020-01-01",
        "trecho": "É falso que Bolsonaro recebeu alta do hospital, segundo apuração."}]
        # Distratores p/ o BM25 ter IDF (índice de 1 doc só nunca casa; ver teste acima).
        + [{"url": f"https://lupa.uol.com.br/z{i}", "titulo": t, "afirmacao_checada": t,
            "selo_original": "Falso", "veredito": "FALSO", "agencia": "lupa", "trecho": t}
           for i, t in enumerate(["Vacina altera o DNA humano", "Urnas foram fraudadas em 2022",
                                  "Limão em jejum cura diabetes", "Governo vai confiscar poupança",
                                  "Água gelada causa gripe"])])
    amb[url] = "É falso que Bolsonaro recebeu alta do hospital, segundo apuração. " * 50
    import asyncio
    rel = asyncio.run(Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=FakeSerp([]), detector=MockDetector()).executar(EntradaConsulta(tipo="titulo", conteudo="Bolsonaro recebeu alta do hospital hoje")))
    # Guarda anti-vácuo: a base precisa ter devolvido a checagem antiga (o gate barra pela data)
    assert any(e.nome == "base-checagem" and e.status == "ok" for e in rel.etapas)
    assert not any(e.nome == "descoberta" and e.status == "pulada" and "checagem aplicável" in e.detalhe for e in rel.etapas)
