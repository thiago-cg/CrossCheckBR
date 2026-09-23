"""LLM-juiz: resumo por notícia (teto 10) + juiz-termômetro em lote."""
import asyncio

from factcheck_mvp import juiz_llm
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import Afirmacao, EntradaConsulta
from factcheck_mvp import serpapi_layer as camada


def _chat_fabrica(resumos="resumo factual da peça.", juiz='{"0": {"score": 75, "relevante": true}}'):
    chamadas = []

    def fake_chat(messages, max_tokens=0, timeout_s=0, temperature=0.0):
        prompt = (messages[0].get("content", "") if messages else "")
        if "termômetro" in prompt:
            chamadas.append("juiz")
            return juiz, "deepseek/deepseek-v4-flash"
        chamadas.append("resumo")
        return resumos, "deepseek/deepseek-v4-flash"

    return fake_chat, chamadas


def test_termometro_parse_e_escala(monkeypatch):
    fake, _ = _chat_fabrica(juiz='{"0": {"score": -80, "relevante": true}, "1": {"score": 10}}')
    monkeypatch.setattr(juiz_llm.llm_openrouter, "chat", fake)
    out = juiz_llm.termometro(["resumo um com tamanho suficiente", "resumo dois também suficiente"], "afirmação suficiente aqui")
    assert out[0]["score"] == -80 and out[0]["refuta"] == 0.8 and out[0]["sustenta"] == 0.0
    assert out[0]["relevante"] is True
    # item 1: score baixo sem flag explícita -> |10| < 30 -> irrelevante
    assert out[1]["relevante"] is False and out[1]["score"] == 10


def test_termometro_falha_honesta(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("cap estourado")

    monkeypatch.setattr(juiz_llm.llm_openrouter, "chat", _boom)
    out = juiz_llm.termometro(["resumo com tamanho suficiente aqui"], "afirmação suficiente aqui")
    assert out[0]["relevante"] is False and "cap" in (out[0]["erro"] or "")
    assert out[0]["motor"] == "llm-juiz"


def test_fora_do_tema_nao_chama_juiz(monkeypatch):
    fake, chamadas = _chat_fabrica(resumos="FORA_DO_TEMA")
    monkeypatch.setattr(juiz_llm.llm_openrouter, "chat", fake)
    r = juiz_llm.resumir("texto fonte com mais de trinta caracteres aqui", "afirmação suficiente aqui")
    assert r["resumo"] == "FORA_DO_TEMA"
    out = juiz_llm.termometro([r["resumo"]], "afirmação suficiente aqui")
    assert out[0]["relevante"] is False and out[0]["score"] == 0
    assert chamadas == ["resumo"]  # juiz nem foi invocado


def test_termometro_lote_esparso_remapeia(monkeypatch):
    """Item 0 FORA_DO_TEMA + item 1 real: o juiz recebe bloco sequencial
    ("[0] ...") e o score volta p/ o índice original (sem trocar/perder)."""
    vistos = []

    def fake_chat(messages, max_tokens=0, timeout_s=0, temperature=0.0):
        vistos.append(messages[0].get("content", ""))
        return '{"0": {"score": 60, "relevante": true}}', "m"

    monkeypatch.setattr(juiz_llm.llm_openrouter, "chat", fake_chat)
    out = juiz_llm.termometro(["FORA_DO_TEMA", "resumo real com tamanho suficiente aqui"],
                              "afirmacao suficiente aqui")
    assert out[0]["relevante"] is False and out[0]["score"] == 0
    assert out[1]["score"] == 60 and out[1]["relevante"] is True
    assert "[0] resumo real" in vistos[0]


def test_concorrencia_resumos_limitada(monkeypatch):
    """Rajada de resumos respeita JUIZ_CONCORRENCIA (evita 429 auto-infligido)."""
    import threading
    import time

    import factcheck_mvp.config as cfg
    assert (cfg.JUIZ_CONCORRENCIA or 4) == 4
    docs = [{
        "url": f"https://portal.test/noticia-{i}", "titulo": f"Senado aprova medida econômica {i}",
        "corpo_texto": "O senado federal aprovou nesta terça a medida econômica em votação apertada no congresso nacional.",
        "fonte": {"id": "portal-test", "nome": "Portal Test"},
        "tipo_conteudo": "noticia", "veredito": "VERDADEIRO"} for i in range(4)]
    idx_ver = Indice.de_artigos(docs)
    estado = {"atual": 0, "max": 0, "n": 0}
    trava = threading.Lock()

    def fake_resumir(fonte, afirmacao):
        with trava:
            estado["atual"] += 1
            estado["max"] = max(estado["max"], estado["atual"])
        time.sleep(0.05)
        with trava:
            estado["atual"] -= 1
            estado["n"] += 1
        return {"resumo": "A peça sustenta a aprovação no senado.", "erro": None, "motor": "llm-juiz"}

    def fake_termometro(resumos, afirmacao):
        return [{"score": 70, "sustenta": 0.7, "refuta": 0.0, "relevante": True,
                 "erro": None, "motor": "llm-juiz"} for _ in resumos]

    monkeypatch.setattr(juiz_llm, "resumir", fake_resumir)
    monkeypatch.setattr(juiz_llm, "termometro", fake_termometro)
    import factcheck_mvp.afirmacoes as mod_af
    monkeypatch.setattr(mod_af, "extrair_afirmacoes",
                        lambda texto, max_n=0, usar_llm=True: ([Afirmacao(texto="Senado aprova medida econômica", indice=0)], "fake"))
    import factcheck_mvp.padroes_llm as mod_p
    monkeypatch.setattr(mod_p, "analisar_padroes",
                        lambda texto, usar=True: {"padroes": [], "motor": "fake"})
    pipe = Pipeline(Catalogo.carregar(), idx_ver, Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""))
    asyncio.run(pipe.executar(
        EntradaConsulta(tipo="titulo", conteudo="Senado aprova medida econômica"),
        usar_llm=True))
    assert estado["n"] == 4 and estado["max"] <= 4, estado


def test_irrelevantes_nao_viram_cobertura_nem_veredito(monkeypatch):
    """Caso água da torneira: 3 selos julgados irrelevantes (fora do tema) não
    geram 'cobertura ampla', não ancoram corpo e o veredito contém em
    indeterminada com limitação honesta."""
    docs = [{
        "url": f"https://portal.test/agua-golpes-{i}", "titulo": f"Água da torneira: golpes usam benefício {i}",
        "corpo_texto": "Quadrilha usa contas de água da torneira para aplicar golpes com páginas falsas na internet e roubar dados de benefício social.",
        "fonte": {"id": "portal-test", "nome": "Portal Test"},
        "tipo_conteudo": "noticia", "veredito": "VERDADEIRO"} for i in range(3)]
    idx_ver = Indice.de_artigos(docs)

    def fake_resumir(fonte, afirmacao):
        return {"resumo": "FORA_DO_TEMA", "erro": None, "motor": "llm-juiz"}

    def fake_termometro(resumos, afirmacao):
        return [{"score": 0, "sustenta": 0.0, "refuta": 0.0, "relevante": False,
                 "erro": None, "motor": "llm-juiz"} for _ in resumos]

    monkeypatch.setattr(juiz_llm, "resumir", fake_resumir)
    monkeypatch.setattr(juiz_llm, "termometro", fake_termometro)
    import factcheck_mvp.afirmacoes as mod_af
    monkeypatch.setattr(mod_af, "extrair_afirmacoes",
                        lambda texto, max_n=0, usar_llm=True: ([Afirmacao(texto="Faz bem tomar água da torneira", indice=0)], "fake"))
    import factcheck_mvp.padroes_llm as mod_p
    monkeypatch.setattr(mod_p, "analisar_padroes",
                        lambda texto, usar=True: {"padroes": [], "motor": "fake"})
    pipe = Pipeline(Catalogo.carregar(), idx_ver, Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="titulo", conteudo="Faz bem tomar água da torneira"),
        usar_llm=True))
    assert all(f.relevante is False for f in rel.fontes if f.tipo_fonte == "veredito")
    assert not any(s.rotulo == "cobertura ampla" for s in rel.sinais)
    assert not any(s.motor == "veredito-existente" for s in rel.sinais)
    assert rel.propensao == "indeterminada"
    assert any("nada sobre o tema" in lim for lim in rel.limitacoes)


def test_teto_juiz_no_pipeline(monkeypatch):
    """5+ selos no índice, teto=2: no máximo 2 resumos (corte respeitado)."""
    docs = [{
        "url": f"https://portal.test/noticia-{i}", "titulo": f"Senado aprova medida econômica {i}",
        "corpo_texto": "O senado federal aprovou nesta terça a medida econômica em votação apertada no congresso nacional.",
        "fonte": {"id": "portal-test", "nome": "Portal Test"},
        "tipo_conteudo": "noticia", "veredito": "VERDADEIRO"} for i in range(4)]
    docs += [{
        "url": f"https://esp.test/{i}", "titulo": f"Clima e esporte {i}",
        "corpo_texto": "Previsão do tempo para o fim de semana com chuva no sul e futebol no domingo.",
        "fonte": {"id": "esp", "nome": "ESP"},
        "tipo_conteudo": "noticia", "veredito": None} for i in range(4)]
    idx_ver = Indice.de_artigos(docs)
    n_resumos = []

    def fake_resumir(fonte, afirmacao):
        n_resumos.append(1)
        return {"resumo": "A peça sustenta a aprovação no senado.", "erro": None, "motor": "llm-juiz"}

    def fake_termometro(resumos, afirmacao):
        return [{"score": 70, "sustenta": 0.7, "refuta": 0.0, "relevante": True,
                 "erro": None, "motor": "llm-juiz"} for _ in resumos]

    import factcheck_mvp.config as cfg
    monkeypatch.setattr(cfg, "JUIZ_MAX_NOTICIAS", 2)
    monkeypatch.setattr(juiz_llm, "resumir", fake_resumir)
    monkeypatch.setattr(juiz_llm, "termometro", fake_termometro)
    import factcheck_mvp.afirmacoes as mod_af
    monkeypatch.setattr(mod_af, "extrair_afirmacoes",
                        lambda texto, max_n=0, usar_llm=True: ([Afirmacao(texto="Senado aprova medida econômica", indice=0)], "fake"))
    import factcheck_mvp.padroes_llm as mod_p
    monkeypatch.setattr(mod_p, "analisar_padroes",
                        lambda texto, usar=True: {"padroes": [], "motor": "fake"})
    pipe = Pipeline(Catalogo.carregar(), idx_ver, Indice(),
                    serpapi=camada.SerpAPIClient(api_key=""))
    rel = asyncio.run(pipe.executar(
        EntradaConsulta(tipo="titulo", conteudo="Senado aprova medida econômica"),
        usar_llm=True))
    assert len(n_resumos) == 2, f"teto furado: {len(n_resumos)} resumos"
    assert any(f.score_juiz == 70 for f in rel.fontes)
    assert any(s.motor == "veredito-existente" for s in rel.sinais)
