"""Índice de checagens: tokenização PT, limiar relativo, flag INDICE_CHECAGENS, adaptador legado."""
from factcheck_mvp import indice
from factcheck_mvp.indice import Indice, checagem_como_doc, tokenizar_pt


def _reg(url, afirm, veredito="FALSO", titulo=None, trecho=""):
    return {"url": url, "titulo": titulo or f"É falso que {afirm}", "agencia": "lupa",
            "afirmacao_checada": afirm, "selo_original": "Falso", "veredito": veredito,
            "data_pub": "2026-09-20", "trecho": trecho, "origem": "rss-titulo"}


CORPUS = [
    _reg("https://a.test/1", "Lula gastou R$ 7,35 bilhões em viagens com Janja"),
    _reg("https://a.test/2", "Cristiano Ronaldo anunciou compra do Santa Cruz"),
    _reg("https://a.test/3", "Vacina contra covid causa infertilidade em mulheres"),
    _reg("https://a.test/4", "Urnas eletrônicas foram fraudadas nas eleições de 2022"),
    _reg("https://a.test/5", "Voto vale como prova de vida no INSS", "VERDADEIRO"),
    _reg("https://a.test/6", "Flávio Bolsonaro usou camiseta com frase contra nordestinos"),
    _reg("https://a.test/7", "Lula disse que pobres devem morrer cortando cana"),
    _reg("https://a.test/8", "Governo vai taxar o Pix a partir de janeiro"),
] + [_reg(f"https://a.test/r{i}", f"Assunto variado número {i} sobre política e economia do país") for i in range(12)]


def test_tokenizar_pt_tira_acento_stopword_moldura():
    toks = tokenizar_pt("É falso que as vacinas causam infertilidade? Vi no zap!")
    assert "falso" not in toks and "zap" not in toks and "que" not in toks
    assert all(t.isascii() for t in toks)
    if indice.radicalizacao_ativa():
        assert tokenizar_pt("fraudadas")[0] == tokenizar_pt("fraude")[0]


def test_parafrase_acha_checagem_no_topo(monkeypatch):
    monkeypatch.delenv("INDICE_CHECAGENS", raising=False)
    idx = Indice.de_checagens(CORPUS)
    hits = idx.buscar_checagens("vi no zap que o Lula torrou 7 bilhões viajando com a Janja", k=3)
    assert hits and hits[0]["url"] == "https://a.test/1"
    assert {"score", "score_rel", "veredito", "afirmacao_checada", "origem"} <= set(hits[0])
    assert 0 < hits[0]["score_rel"] <= 1
    hits = idx.buscar_checagens("CR7 comprou o time do Santa Cruz?")
    assert hits and hits[0]["url"] == "https://a.test/2"


def test_fora_do_tema_nao_retorna(monkeypatch):
    monkeypatch.delenv("INDICE_CHECAGENS", raising=False)
    idx = Indice.de_checagens(CORPUS)
    for q in ("ibuprofeno piora a dengue", "receita de bolo de cenoura", "terremoto no Japão hoje",
              "Lula inaugurou hospital em Pernambuco"):  # 1 termo em comum não basta
        assert idx.buscar_checagens(q) == [], q


def test_flag_desliga(monkeypatch):
    idx = Indice.de_checagens(CORPUS)
    monkeypatch.setenv("INDICE_CHECAGENS", "0")
    assert indice.habilitado() is False
    assert idx.buscar_checagens("Lula gastou 7 bilhões em viagens com Janja") == []
    monkeypatch.setenv("INDICE_CHECAGENS", "1")
    assert indice.habilitado() is True


def test_arquivo_ausente_e_vazio(tmp_path, monkeypatch):
    monkeypatch.delenv("INDICE_CHECAGENS", raising=False)
    assert Indice.de_checagens(tmp_path / "nao.jsonl").buscar_checagens("qualquer coisa") == []
    assert Indice.de_checagens([]).buscar_checagens("qualquer coisa") == []


def test_adaptador_legado():
    d = checagem_como_doc(CORPUS[0])
    assert d["tipo_conteudo"] == "checagem" and d["veredito"] == "FALSO"
    assert d["fonte"]["id"] == "lupa" and d["url"] == "https://a.test/1"


def test_api_legada_intacta():
    idx = Indice.de_artigos([{"titulo": "É falso que as bets foram proibidas", "corpo_texto": "",
                              "veredito": "FALSO"}])
    assert idx.buscar("bets proibidas")[0]["doc"]["veredito"] == "FALSO"
    assert len(idx) == 1


def test_benchmark_real_nao_regride(monkeypatch):
    """Gate do mini-benchmark sobre data/checagens.jsonl + eval/casos_claimreview.jsonl."""
    monkeypatch.delenv("INDICE_CHECAGENS", raising=False)
    import sys
    from pathlib import Path

    import pytest

    raiz = Path(__file__).resolve().parents[1]
    if not (indice.ARQ_CHECAGENS.exists() and (raiz / "eval" / "casos_claimreview.jsonl").exists()):
        pytest.skip("dados do benchmark ausentes")
    sys.path.insert(0, str(raiz / "scripts"))
    import bench_indice_checagens as b

    r = b.avaliar(Indice.de_checagens(), b._casos())
    assert r["recall@3"] >= 0.75 and r["taxa_fp"] <= 0.10, r


def test_tokens_alfanumericos_curtos_ficam():
    # sem isto "5G causa câncer" virava "causa câncer" e casava com qualquer boato sobre câncer
    toks = indice.tokenizar_pt("5G causa câncer; H1N1 e 4g")
    assert "5g" in toks and "h1n1" in toks and "4g" in toks


def test_numeros_da_alegacao_precisam_bater(monkeypatch):
    monkeypatch.delenv("INDICE_CHECAGENS", raising=False)
    regs = [{"url": f"https://c.test/{i}", "titulo": t, "afirmacao_checada": t, "agencia": "lupa",
             "veredito": "FALSO"} for i, t in enumerate([
                 "Lula e Janja gastaram R$ 62 bilhões em viagens com dinheiro público",
                 "Lula e Janja gastaram R$ 7 bilhões em viagens com dinheiro público"])]
    # ruído realista: documentos que casam 1 termo (o limiar é relativo à mediana do ruído)
    regs += [{"url": f"https://o.test/{i}", "titulo": f"Pacote de viagens para o litoral {i}",
              "afirmacao_checada": f"Agência vende viagens baratas {i}", "agencia": "boatos"} for i in range(30)]
    idx = Indice.de_checagens(regs)
    urls = [h["url"] for h in idx.buscar_checagens("Lula e Janja gastaram 7 bilhões em viagens", k=5)]
    assert urls == ["https://c.test/1"]  # a de R$ 62 bilhões não volta
    sem = [h["url"] for h in idx.buscar_checagens("Lula e Janja gastaram 7 bilhões em viagens", k=5,
                                                   limiar={"exigir_numeros": False})]
    assert set(sem) == {"https://c.test/0", "https://c.test/1"}  # sem a regra, as duas voltam


def test_numero_colado_em_nome_nao_conta():
    # "CR7", "5G", "Covid-19" são nomes: não exigem o número na checagem
    assert indice._RE_NUMERO.findall("CR7 e 5G e Covid-19 e H1N1") == []
    assert indice._RE_NUMERO.findall("gastou R$ 7,35 bilhões; reajuste de 15%") == ["7", "35", "15"]
