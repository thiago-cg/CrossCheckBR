"""Índice BM25: veredito precisa ser encontrável pelo texto da afirmação."""
from factcheck_mvp.indice import Indice


def _doc(titulo, corpo="", veredito=None):
    return {"url": "https://exemplo.test/" + titulo[:10], "titulo": titulo, "corpo_texto": corpo,
            "fonte": {"id": "lupa", "nome": "Lupa"}, "tipo_conteudo": "checagem", "veredito": veredito}


def test_busca_encontra_veredito():
    idx = Indice.de_artigos([
        _doc("É falso que as bets foram proibidas no Brasil", "apostas seguem reguladas", "FALSO"),
        _doc("Receita de bolo de cenoura", "farinha, ovos e cenoura"),
    ])
    hits = idx.buscar("bets são proibidas no brasil?", top_k=3)
    assert hits and hits[0]["doc"]["veredito"] == "FALSO"


def test_busca_sem_match_retorna_vazio():
    idx = Indice.de_artigos([_doc("Receita de bolo de cenoura", "farinha e ovos")])
    assert idx.buscar("bets são proibidas no brasil?") == []
