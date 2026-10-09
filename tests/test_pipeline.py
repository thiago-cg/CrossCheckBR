"""Pipeline ponta a ponta com busca, deep crawl e juiz FALSOS (sem rede).

O juiz falso é uma regra sobre o PRÓPRIO prompt do juiz (lê os itens JSON e classifica
pelo trecho), então o caminho real é exercitado: seleção → crawl → clusters → prompt →
citação verificada → decidir → relatório.
"""
import asyncio
import json
import re

import pytest

from factcheck_mvp import afirmacoes, config, llm
from factcheck_mvp import pipeline as pl
from factcheck_mvp.aprofundar import CorpoLido
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.modelo_fake import MockDetector
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import Afirmacao, EntradaConsulta
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
    amb[url] = "É falso que café cura câncer, segundo especialistas. " * 50
    assert idx.buscar_checagens("Café cura câncer")  # o atalho acha a checagem
    rel = _rodar("Café cura câncer", web, indice=idx)
    assert len([f for f in rel.fontes if "aosfatos" in f.url]) == 1
    assert len(rel.decisao["votos"]) == 1, rel.decisao
    # 1 selo FALSO aplicável (juiz disse REFUTA) basta p/ alta, mas conta uma vez só
    assert rel.decisao["votos"][0]["peso"] <= 1.5 and rel.propensao == "alta", (rel.decisao, [e.detalhe for e in rel.etapas])


def test_selo_verdadeiro_do_fato_ou_fake_da_baixa(amb, monkeypatch):
    """Direção pelo enum (VERDADEIRO → baixa), nunca pelo nome do portal ('Fato ou Fake').
    T6: o selo extraído da própria página não vota; o selo que vota vem do ÍNDICE (origem "indice")."""
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://g1.globo.com/fato-ou-fake/noticia/2024/05/10/e-fato-que-ponte.ghtml"
    frase = "Circula nas redes a informação sobre a inauguração da ponte, verificada pelo Fato ou Fake."
    r, corpo = _pag(url, "Ponte Salvador-Itaparica foi inaugurada", frase)
    amb[url] = corpo
    idx = Indice.de_checagens([{"url": url, "titulo": "Ponte Salvador-Itaparica foi inaugurada",
                                "afirmacao_checada": "Ponte Salvador-Itaparica foi inaugurada",
                                "selo_original": "#FATO", "veredito": "VERDADEIRO",
                                "agencia": "g1-fato-ou-fake", "trecho": frase}]
                               # Distratores p/ o BM25 ter IDF (índice de 1 doc só nunca casa).
                               + [{"url": f"https://lupa.uol.com.br/z{i}", "titulo": t, "afirmacao_checada": t,
                                   "selo_original": "Falso", "veredito": "FALSO", "agencia": "lupa", "trecho": t}
                                  for i, t in enumerate(["Vacina altera o DNA humano", "Urnas foram fraudadas em 2022",
                                                         "Limão em jejum cura diabetes", "Governo vai confiscar poupança",
                                                         "Água gelada causa gripe"])])
    rel = _rodar("Ponte Salvador-Itaparica foi inaugurada", [r], indice=idx)
    assert rel.propensao == "baixa", rel.justificativa
    assert rel.decisao["vereditos_aplicados"][0]["veredito"] == "VERDADEIRO"
    assert rel.decisao["vereditos_aplicados"][0]["url"] == url


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
    assert rel.onde_encontrado == "base"

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
    assert rel.onde_encontrado == "web"

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


def test_julga_cada_peca_com_corpo(amb, monkeypatch):
    """Pipeline chama o avaliador 1× por peça com corpo lido (manchete+corpo).

    4 peças com corpo lido: `avaliador.avaliar` deve ser chamado 4×, uma vez
    por par (peça, afirmação), e cada chamada recebe `titulo` e corpo
    (`corpo`/`trecho_juiz`/`texto_completo`) não-vazios."""
    from factcheck_mvp import avaliador as _aval
    pags = [
        _pag(f"https://www.{dom}/saude/2024/01/cafe-cancer-checagem/",
             "Café cura câncer? Checagem",
             "É falso que café cura câncer, segundo o INCA.")
        for dom in ("g1.globo.com", "estadao.com.br", "bbc.com", "folha.uol.com.br")
    ]
    resultados = _prep(amb, pags)
    chamadas = []

    def fake_avaliar(nucleo, peca):
        chamadas.append((nucleo, dict(peca)))
        return {"posicao": "REFUTA", "citacao": "É falso que café cura câncer",
                "citacao_score": 1.0, "citacao_verificada": True,
                "pagina_diz": "A página diz que é falso que café cura câncer.",
                "motor": "fake-avaliador", "erro": None, "corpo_lido": True}

    monkeypatch.setattr(_aval, "avaliar", fake_avaliar)
    rel = _rodar("Café cura câncer", resultados)
    assert len(chamadas) == 4, chamadas
    for nucleo, peca in chamadas:
        assert (nucleo or "").strip()
        assert (peca.get("titulo") or "").strip()
        corpo = peca.get("corpo") or peca.get("trecho_juiz") or peca.get("texto_completo") or ""
        assert corpo.strip()
    assert rel.propensao == "alta", rel.justificativa


def test_juiz_final_usa_flag_corpo_lido_do_pipeline(amb, monkeypatch):
    """Wiring avaliador→juiz (Task 4): `ItemEvidencia.corpo_lido` vem da flag da
    peça (`p.get("corpo_lido", p.get("corpo"))`), não de `bool(p.get("corpo"))`.

    Uma peça com corpo presente mas flag `corpo_lido=False` chega ao juiz como
    não-lida (E3: não vota); outra sem corpo chega como não-lida
    também. Espiona `decidir` para pinar o `ItemEvidencia` que o pipeline monta.
    """
    from factcheck_mvp import avaliador as _aval
    from factcheck_mvp import decisao as _dec

    r1, c1 = _pag("https://g1.globo.com/saude/2024/01/cafe-cancer-wiring-a/",
                  "Café cura câncer? Checagem",
                  "É falso que café cura câncer, segundo o INCA.")
    r2, c2 = _pag("https://www.estadao.com.br/estadao-verifica/2024/02/cafe-cancer-wiring-b/",
                  "Café e câncer: o que diz a ciência",
                  "Não há evidência de que café cura câncer, dizem oncologistas.")
    r3, _c3 = _pag("https://www.bbc.com/portuguese/articles/cafe-wiring-c",
                   "O que a ciência diz sobre café",
                   "Pesquisadores desmente que café cura câncer em revisão ampla.")
    resultados = _prep(amb, [(r1, c1), (r2, c2)]) + [r3]
    # r3 fora de `amb`: o crawl falha (sem corpo, flag False).
    url_lida, url_so_titulo, url_sem_corpo = r1["link"], r2["link"], r3["link"]

    tinha_corpo, vistos = {}, {}
    orig_decidir = _dec.decidir

    def _espiar(ev):
        vistos["itens"] = list(ev.itens)
        return orig_decidir(ev)

    monkeypatch.setattr(_dec, "decidir", _espiar)

    def fake_avaliar(nucleo, peca):
        url = peca.get("url") or ""
        tinha_corpo[url] = bool(peca.get("corpo"))
        if url == url_so_titulo:
            # Divergência coberta: corpo presente, mas a peça não foi lida.
            peca["corpo_lido"] = False
        return {"posicao": "REFUTA", "citacao": "É falso que café cura câncer",
                "citacao_score": 1.0, "citacao_verificada": True,
                "pagina_diz": "A página diz que é falso que café cura câncer.",
                "motor": "fake-avaliador", "erro": None, "corpo_lido": True}

    monkeypatch.setattr(_aval, "avaliar", fake_avaliar)
    rel = _rodar("Café cura câncer", resultados)

    # Guarda anti-vácuo: a divergência existe de fato no wiring.
    assert tinha_corpo[url_so_titulo] is True
    assert tinha_corpo[url_sem_corpo] is False

    por_url = {it.url: it for it in vistos["itens"]}
    assert set(por_url) == {url_lida, url_so_titulo, url_sem_corpo}
    assert por_url[url_lida].corpo_lido is True
    assert por_url[url_so_titulo].corpo_lido is False
    assert por_url[url_sem_corpo].corpo_lido is False
    assert all(it.classe == "REFUTA" and it.citacao_verificada is True for it in vistos["itens"])

    fontes = {f.url: f for f in rel.fontes}
    assert fontes[url_lida].corpo_lido is True
    assert fontes[url_so_titulo].corpo_lido is False
    assert fontes[url_sem_corpo].corpo_lido is False

    # E3: só a lida vota; as não lidas aparecem como "não analisadas integralmente".
    pesos = {u: v["peso"] for v in rel.decisao["votos"] for u in v["urls"]}
    assert set(pesos) == {url_lida}
    assert {n["url"] for n in rel.decisao["nao_analisadas"]} == {url_so_titulo, url_sem_corpo}
    assert rel.propensao == "media", rel.justificativa  # 1 fonte curada sozinha não satura


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


def test_pipeline_usa_data_referencia_da_entrada(amb, monkeypatch):
    # espiona decisao.decidir; EntradaConsulta(..., data_referencia="2026-01-15")
    # → ev.data_referencia == "2026-01-15" (não a data de hoje)
    from factcheck_mvp import decisao as _dec
    vistos = []
    _orig = _dec.decidir

    def _espiar(ev):
        vistos.append(ev)
        return _orig(ev)

    monkeypatch.setattr(_dec, "decidir", _espiar)
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=FakeSerp([]), detector=MockDetector())
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="titulo", conteudo="Bolsonaro recebeu alta do hospital hoje",
                        data_referencia="2026-01-15"), usar_llm=False))
    assert vistos and vistos[-1].data_referencia == "2026-01-15"
    rec = [e for e in rel.etapas if e.nome == "recebimento"][0]
    assert "2026-01-15" in rec.detalhe and "origem: entrada" in rec.detalhe


def test_data_da_pagina_substitui_data_textual_da_serpapi(amb, monkeypatch):
    """A string '11 de ago. de 2025' do Google não pode bloquear o datePublished ISO da página."""
    from factcheck_mvp.aprofundar import CorpoLido as _CorpoLido
    from factcheck_mvp.schemas import Afirmacao as _Afirmacao
    url = "https://www.exemplo.com.br/2025/08/checa-x"
    parcial = camada.normalizar_item(
        {"link": url, "title": "Checa X", "snippet": "checagem", "date": "11 de ago. de 2025"},
        engine="google")
    peca = pl.Pipeline._peca_web(parcial, {0})
    # Guarda anti-vácuo: a SerpAPI trouxe data textual válida (dia) — sem a
    # prioridade, ela bloquearia o ISO da página ("primeiro que chegar").
    assert peca["data_pub"] == "2025-08-11" and peca["data_pub_precisao"] == "dia"
    assert peca["data_pub_bruta"] == "11 de ago. de 2025"

    async def fake_aprofundar(cands, catalogo, **kw):
        return {url: _CorpoLido(
            url=url, final_url=url, trecho_corpo="É falso que X. " * 100,
            corpo_lido=True, texto_completo="É falso que X. " * 500, metodo="jsonld",
            titulo="Checa X", data_pub="2025-08-11T09:00:00-03:00", data_pub_fonte="jsonld")}

    monkeypatch.setattr(pl, "aprofundar", fake_aprofundar)
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=FakeSerp([]), detector=MockDetector())

    async def avisar(msg):
        return None

    afs = [_Afirmacao(texto="X aconteceu", nucleo="X aconteceu", polaridade="afirma", consulta="x")]
    n_lidas, n_alvo = asyncio.run(pipe._ler(afs, [peca], avisar))
    assert (n_lidas, n_alvo) == (1, 1)
    assert peca["data_pub"] == "2025-08-11"
    assert peca["data_pub_precisao"] == "dia"
    assert peca["data_pub_bruta"] == "2025-08-11T09:00:00-03:00"


# --- Task 5 (E4): gate E1 usa a mesma janela, referência e data da decisão ---
_DISTRATORES_BM25 = ["Vacina altera o DNA humano", "Urnas foram fraudadas em 2022",
                     "Limão em jejum cura diabetes", "Governo vai confiscar poupança",
                     "Água gelada causa gripe"]


def _extrator_fixo(afs):
    """Troca o extrator de afirmações por um que devolve `afs` (ex.: sem o "hoje")."""
    def _extrair(texto, max_n=0, usar_llm=True):
        return list(afs), "fake"
    return _extrair


def _eventos_aplicabilidade(monkeypatch):
    """Captura os eventos `fonte estagio=aplicabilidade` emitidos pelo pipeline."""
    from factcheck_mvp import telemetria as _tel
    capturados = []
    _evento_original = _tel.evento

    def _espiar(tipo, /, **dados):
        if tipo == "fonte" and dados.get("estagio") == "aplicabilidade":
            capturados.append(dados)
        return _evento_original(tipo, **dados)

    monkeypatch.setattr(_tel, "evento", _espiar)
    return capturados


def test_gate_barra_checagem_antiga_quando_o_extrator_perdeu_o_hoje(amb, monkeypatch):
    """A3/A4: o extrator devolve a afirmação SEM o "hoje"; o texto do usuário tem o "hoje" e a
    entrada traz a referência. A checagem de 2025 não é aplicável a um fato de 2026-10-09: a web não pula."""
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://boatos.org/x-bolsonaro-alta-antiga"
    idx = Indice.de_checagens([{"url": url, "titulo": "Bolsonaro alta hospital",
        "afirmacao_checada": "Bolsonaro recebeu alta do hospital", "selo_original": "Falso",
        "veredito": "FALSO", "agencia": "boatos-org", "data_pub": "2025-04-23",
        "trecho": "É falso que Bolsonaro recebeu alta do hospital, segundo apuração."}]
        + [{"url": f"https://lupa.uol.com.br/z{i}", "titulo": t, "afirmacao_checada": t,
            "selo_original": "Falso", "veredito": "FALSO", "agencia": "lupa", "trecho": t}
           for i, t in enumerate(_DISTRATORES_BM25)])
    amb[url] = "É falso que Bolsonaro recebeu alta do hospital, segundo apuração. " * 50
    monkeypatch.setattr(afirmacoes, "extrair_afirmacoes", _extrator_fixo(
        [Afirmacao(texto="Bolsonaro recebeu alta do hospital", nucleo="Bolsonaro recebeu alta do hospital",
                   polaridade="afirma", consulta="bolsonaro alta hospital")]))
    eventos = _eventos_aplicabilidade(monkeypatch)
    buscas = {"n": 0}

    class Contadora(FakeSerp):
        def buscar(self, q):
            buscas["n"] += 1
            return super().buscar(q)

    rel = asyncio.run(Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=Contadora([]),
                               detector=MockDetector()).executar(
        EntradaConsulta(tipo="titulo", conteudo="Bolsonaro recebeu alta do hospital hoje",
                        data_referencia="2026-10-09")))
    # Guarda anti-vácuo: a base devolveu a checagem e o gate a avaliou com janela, referência e data
    assert any(e.nome == "base-checagem" and e.status == "ok" for e in rel.etapas)
    da_checagem = [e for e in eventos if e["url"] == url]
    assert len(da_checagem) == 1
    assert da_checagem[0]["decisao"] == "inaplicavel" and da_checagem[0]["motivo"] == "data incompatível"
    assert (da_checagem[0]["janela"], da_checagem[0]["referencia"], da_checagem[0]["excedente"]) == (
        2, "2026-10-09", 532)  # 534 dias de 2025-04-23 a 2026-10-09, menos a janela de 2
    assert not any(e.nome == "descoberta" and e.status == "pulada" and "checagem aplicável" in e.detalhe
                   for e in rel.etapas)
    assert buscas["n"] > 0 and rel.onde_encontrado == "web"
    # a decisão desconta a mesma fonte, com a mesma janela e a mesma distância que o gate mediu
    assert [(x["janela"], x["dias_alem_da_janela"]) for x in rel.decisao["descontos_temporais"]] == [(2, 532)]


def test_gate_nao_barra_checagem_de_frase_sem_marcador_com_hoje_em_outra_frase(amb, monkeypatch):
    """Duas afirmações: o "hoje" está só na outra frase. A checagem antiga da frase sem marcador
    não herda a janela do texto: o gate a aceita e a decisão não desconta nada."""
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    url = "https://boatos.org/x-ponte-2019"
    idx = Indice.de_checagens([{"url": url, "titulo": "Ponte caiu em 2019",
        "afirmacao_checada": "A ponte caiu em 2019", "selo_original": "Falso",
        "veredito": "FALSO", "agencia": "boatos-org", "data_pub": "2019-06-01",
        "trecho": "É falso que a ponte caiu em 2019, segundo a prefeitura."}]
        + [{"url": f"https://lupa.uol.com.br/y{i}", "titulo": t, "afirmacao_checada": t,
            "selo_original": "Falso", "veredito": "FALSO", "agencia": "lupa", "trecho": t}
           for i, t in enumerate(_DISTRATORES_BM25)])
    amb[url] = "É falso que a ponte caiu em 2019, segundo a prefeitura. " * 50
    monkeypatch.setattr(afirmacoes, "extrair_afirmacoes", _extrator_fixo([
        Afirmacao(texto="A ponte caiu em 2019", nucleo="A ponte caiu em 2019", polaridade="afirma",
                  consulta="ponte caiu 2019"),
        Afirmacao(texto="Bolsonaro recebeu alta do hospital hoje", nucleo="Bolsonaro recebeu alta do hospital",
                  polaridade="afirma", consulta="bolsonaro alta hospital")]))
    eventos = _eventos_aplicabilidade(monkeypatch)
    rel = asyncio.run(Pipeline(Catalogo.carregar(), idx, Indice(), serpapi=FakeSerp([]),
                               detector=MockDetector()).executar(
        EntradaConsulta(tipo="titulo", conteudo="A ponte caiu em 2019. Bolsonaro recebeu alta do hospital hoje.",
                        data_referencia="2026-10-09")))
    da_frase_sem_marcador = [e for e in eventos if e["url"] == url and e["afirmacao"] == 0]
    # Guarda anti-vácuo: a checagem da frase sem marcador chegou ao gate
    assert len(da_frase_sem_marcador) == 1
    assert da_frase_sem_marcador[0]["decisao"] == "aplicavel"
    assert da_frase_sem_marcador[0]["janela"] is None and da_frase_sem_marcador[0]["excedente"] is None
    assert rel.onde_encontrado == "base"
    assert rel.decisao["descontos_temporais"] == []


class _BertStub(MockDetector):
    """Detector com cara de modelo real (mock=False): credibilidade fixa de 0,8."""
    nome = "bertimbau:stub"

    def analisar_pagina(self, titulo, corpo):
        return {"prob_fake": 0.8, "modelo": self.nome, "mock": False}


def _itens_decididos(monkeypatch, resultados, detector):
    """Roda o pipeline com avaliador falso e devolve os ItemEvidencia que chegam a `decidir`."""
    from factcheck_mvp import avaliador as _aval
    from factcheck_mvp import decisao as _dec

    vistos = {}
    orig_decidir = _dec.decidir

    def _espiar(ev):
        vistos["itens"] = list(ev.itens)
        return orig_decidir(ev)

    def fake_avaliar(nucleo, peca):
        return {"posicao": "REFUTA", "citacao": "É falso que café cura câncer",
                "citacao_score": 1.0, "citacao_verificada": True,
                "pagina_diz": "A página diz que é falso que café cura câncer.",
                "motor": "fake-avaliador", "erro": None, "corpo_lido": True}

    monkeypatch.setattr(_dec, "decidir", _espiar)
    monkeypatch.setattr(_aval, "avaliar", fake_avaliar)
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=FakeSerp(resultados), detector=detector)
    asyncio.run(pipe.executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer")))
    return {it.url: it for it in vistos["itens"]}


def test_t6_prob_fake_pagina_chega_ao_item_da_pagina_lida(amb, monkeypatch):
    """T6/B1c, wiring: `ItemEvidencia.prob_fake_pagina` vem de `p["bert"]["prob_fake"]` (T5) da
    página lida, quando o detector é modelo real. A só-título não foi lida: fica com None."""
    r1, c1 = _pag("https://g1.globo.com/saude/2024/01/cafe-cancer-bert-a/", "Café cura câncer? Checagem",
                  "É falso que café cura câncer, segundo o INCA.")
    r2, _c2 = _pag("https://www.bbc.com/portuguese/articles/cafe-bert-b", "O que a ciência diz sobre café",
                   "Pesquisadores desmentem que café cura câncer em revisão ampla.")
    resultados = _prep(amb, [(r1, c1)]) + [r2]  # r2 fora de `amb`: o crawl falha (sem corpo)
    por_url = _itens_decididos(monkeypatch, resultados, _BertStub())
    assert set(por_url) == {r1["link"], r2["link"]}
    assert por_url[r1["link"]].corpo_lido is True and por_url[r2["link"]].corpo_lido is False
    assert por_url[r1["link"]].prob_fake_pagina == pytest.approx(0.8)
    assert por_url[r2["link"]].prob_fake_pagina is None


def test_t6_mock_placeholder_nao_entra_na_decisao(amb, monkeypatch):
    """O mock (placeholder: 0,5 sem sinal, o que meia a postura) segue no trace, mas não entra no
    nível: a página lida chega ao juiz com prob_fake_pagina None (peso de antes do T6)."""
    r1, c1 = _pag("https://g1.globo.com/saude/2024/01/cafe-cancer-mock-a/", "Café cura câncer? Checagem",
                  "É falso que café cura câncer, segundo o INCA.")
    por_url = _itens_decididos(monkeypatch, _prep(amb, [(r1, c1)]), MockDetector())
    assert por_url[r1["link"]].corpo_lido is True
    assert por_url[r1["link"]].prob_fake_pagina is None


# --- E4 revisão (itens 4, 7, 8, 9): fonte real da data, rótulo do fallback, recebimento e link sem data ---
_URL_CHECA_Z = "https://www.exemplo.com.br/2025/08/checa-z"


def _peca_serpapi_11_ago(url):
    """Peça web com a data textual da SerpAPI ("11 de ago. de 2025" -> dia, tier absoluto)."""
    parcial = camada.normalizar_item(
        {"link": url, "title": "Checa Z", "snippet": "checagem", "date": "11 de ago. de 2025"},
        engine="google")
    return pl.Pipeline._peca_web(parcial, {0})


def _ler_com_pagina(monkeypatch, peca, url, **campos_pagina):
    """Roda `_ler` com uma página lida (CorpoLido com `campos_pagina`) e devolve a peça."""
    async def fake_aprofundar(cands, catalogo, **kw):
        return {url: CorpoLido(url=url, final_url=url, trecho_corpo="É falso que X. " * 100,
                               corpo_lido=campos_pagina.get("metodo") != "falha",
                                texto_completo="É falso que X. " * 500
                                if campos_pagina.get("metodo") != "falha" else "",
                                titulo="Checa Z", **campos_pagina)}
    monkeypatch.setattr(pl, "aprofundar", fake_aprofundar)
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=FakeSerp([]), detector=MockDetector())

    async def avisar(msg):
        return None

    afs = [Afirmacao(texto="X aconteceu", nucleo="X aconteceu", polaridade="afirma", consulta="x")]
    asyncio.run(pipe._ler(afs, [peca], avisar))
    return peca


@pytest.mark.parametrize("metodo", ["jsonld", "seletor", "regex", "falha", "readerlm", "trafilatura"])
def test_data_jsonld_prevalece_sobre_a_textual_qualquer_que_seja_o_metodo_do_texto(amb, monkeypatch, metodo):
    """A fonte da data é o JSON-LD (campo próprio), não o método que extraiu o texto (I-3)."""
    peca = _peca_serpapi_11_ago(_URL_CHECA_Z)
    assert peca["data_pub"] == "2025-08-11" and peca["data_pub_precisao"] == "dia"  # guarda anti-vácuo
    _ler_com_pagina(monkeypatch, peca, _URL_CHECA_Z, metodo=metodo, data_pub="2025-08-12T09:00:00-03:00",
                    data_pub_fonte="jsonld")
    assert peca["data_pub"] == "2025-08-12"
    assert peca["data_pub_precisao"] == "dia"
    assert peca["data_pub_bruta"] == "2025-08-12T09:00:00-03:00"


@pytest.mark.parametrize("fonte", ["trafilatura", "readerlm"])
def test_data_de_trafilatura_ou_readerlm_nao_troca_a_data_absoluta_da_serpapi(amb, monkeypatch, fonte):
    peca = _peca_serpapi_11_ago(_URL_CHECA_Z)
    _ler_com_pagina(monkeypatch, peca, _URL_CHECA_Z, metodo="jsonld", data_pub="2025-08-12",
                    data_pub_fonte=fonte)
    assert peca["data_pub"] == "2025-08-11"  # SerpAPI absoluta (dia) vence trafilatura (dia) e ReaderLM


def test_fallback_de_data_relativa_sem_ancora_diz_sem_ancora(monkeypatch):
    capturados = []
    monkeypatch.setattr(pl.telemetria, "fallback",
                        lambda onde, motivo="", /, **kw: capturados.append((onde, motivo)))
    relativa = {"url": "https://g1.globo.com/a", "titulo": "t", "fonte": {}, "data_pub": None,
                "data_pub_bruta": "há 3 dias"}
    ilegivel = {"url": "https://g1.globo.com/b", "titulo": "t", "fonte": {}, "data_pub": None,
                "data_pub_bruta": "ontem à tarde"}
    pl.Pipeline._peca_web(relativa, {0})
    pl.Pipeline._peca_web(ilegivel, {0})
    assert capturados == [("data_pub", "sem âncora"), ("data_pub", "formato não reconhecido")]


def _recebimento(rel):
    return [e for e in rel.etapas if e.nome == "recebimento"][0].detalhe


def _executar(entrada, usar_llm=False):
    return asyncio.run(Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                                serpapi=FakeSerp([]), detector=MockDetector()).executar(entrada, usar_llm=usar_llm))


def test_recebimento_mostra_marcador_janela_referencia_e_origem(amb):
    rel = _executar(EntradaConsulta(tipo="titulo", conteudo="Bolsonaro recebeu alta do hospital hoje",
                                    data_referencia="2026-10-09"))
    assert _recebimento(rel).endswith(
        "marcador temporal: hoje (janela 2d); referência 2026-10-09 (origem: entrada).")


def test_recebimento_origem_relogio_e_ausente(amb, monkeypatch):
    from factcheck_mvp import replay as _replay
    monkeypatch.setattr(_replay, "hoje", lambda contexto: "2026-10-09")
    rel = _executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer"))
    assert _recebimento(rel).endswith("marcador temporal: ausente; referência 2026-10-09 (origem: relogio).")
    monkeypatch.setattr(_replay, "hoje", lambda contexto: None)
    rel2 = _executar(EntradaConsulta(tipo="titulo", conteudo="Café cura câncer"))
    assert _recebimento(rel2).endswith("marcador temporal: ausente; referência ausente (origem: ausente).")


def test_link_sem_data_desliga_o_e4_e_nao_consulta_o_relogio(amb, monkeypatch):
    """A5: entrada por link cuja página não tem data: referência explicitamente ausente, sem relógio."""
    from factcheck_mvp import replay as _replay
    from factcheck_mvp import decisao as _dec
    chamadas = []

    def _relogio_proibido(contexto):
        chamadas.append(contexto)
        return "2026-10-09"

    monkeypatch.setattr(_replay, "hoje", _relogio_proibido)
    vistos = []
    _orig = _dec.decidir

    def _espiar(ev):
        vistos.append(ev)
        return _orig(ev)

    monkeypatch.setattr(_dec, "decidir", _espiar)
    rel = _executar(EntradaConsulta(tipo="texto", conteudo="Bolsonaro recebeu alta do hospital hoje",
                                    sem_referencia_temporal=True))
    assert chamadas == []
    assert vistos and vistos[-1].data_referencia is None
    assert _recebimento(rel).endswith(
        "marcador temporal: hoje (janela 2d); referência ausente (origem: ausente (link sem data)).")
    assert rel.decisao["descontos_temporais"] == []
