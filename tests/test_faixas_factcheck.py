"""Faixas do agregador (evidência mista não sai "baixa") + Google Fact Check."""
from factcheck_mvp import config, factcheck_api
from factcheck_mvp.agregador import agregar, verificar_neutralidade
from factcheck_mvp.schemas import SinalAnalise


def _s(motor, rotulo, valor, conf):
    return SinalAnalise(motor=motor, rotulo=rotulo, valor=valor, confianca=conf)


def _placar(n_ref, n_sus):
    """Sinais como o pipeline monta: confiança proporcional ao placar."""
    frac_ref = n_ref / (n_ref + n_sus)
    sinais = [_s("modelo-fake", "modelo mock (PLACEHOLDER)", "0.5", 0.4)]
    if n_ref:
        sinais.append(_s("corroboracao", "fontes refutam a afirmação",
                         f"{n_ref} fonte(s) contestam a afirmação", round(0.8 * frac_ref, 3)))
    if n_sus:
        sinais.append(_s("corroboracao", "fontes sustentam a afirmação (só título)",
                         f"{n_sus} manchete(s) sustentam", round(0.5 * (1 - frac_ref if n_ref else 1), 3)))
    return sinais


def test_maioria_contesta_nao_sai_baixa():
    # Caso Selic: 3 veículos contestam x 1 sustenta saía "baixa" (verde).
    assert agregar(_placar(3, 1))["propensao"] in ("media", "alta")


def test_todos_contestam_sai_alta():
    assert agregar(_placar(4, 0))["propensao"] == "alta"


def test_todos_sustentam_sai_baixa():
    assert agregar(_placar(0, 3))["propensao"] == "baixa"


def test_empate_sai_indeterminada():
    assert agregar(_placar(1, 1))["propensao"] in ("indeterminada", "media")


def test_texto_fala_em_propensao_sem_veredito():
    out = agregar(_placar(3, 1))
    assert "propensão média de ser fake news" in out["justificativa"].lower()
    assert "não um veredito" in out["justificativa"]
    assert "(corroboracao)" not in out["justificativa"]
    assert verificar_neutralidade(out["justificativa"]) == []


_RESPOSTA = {"claims": [{
    "text": "Governo vai confiscar a poupança dos brasileiros",
    "claimant": "posts em redes sociais",
    "claimDate": "2024-05-01T00:00:00Z",
    "claimReview": [
        {"publisher": {"name": "Aos Fatos", "site": "aosfatos.org"},
         "url": "https://www.aosfatos.org/noticias/falso-confisco/",
         "title": "Governo não vai confiscar poupança", "textualRating": "Falso",
         "reviewDate": "2024-05-02T00:00:00Z"},
        {"publisher": {"name": "Lupa"}, "url": "", "textualRating": "Falso"},  # sem URL: descarta
    ]}]}


def test_factcheck_converte_para_doc_do_indice():
    docs = factcheck_api._para_docs(_RESPOSTA)
    assert len(docs) == 1
    d = docs[0]
    assert d["tipo_conteudo"] == "checagem" and d["veredito"] == "Falso"
    assert d["fonte"]["nome"] == "Aos Fatos"
    assert "confiscar a poupança" in d["corpo_texto"] and "Falso" in d["corpo_texto"]


def test_factcheck_sem_chave_nao_chama_rede(monkeypatch):
    monkeypatch.setattr(config, "FACTCHECK_API_KEY", "")
    import httpx

    def _proibido(*a, **k):
        raise AssertionError("não deveria chamar a rede sem chave")
    monkeypatch.setattr(httpx, "get", _proibido)
    assert factcheck_api.buscar("Governo vai confiscar a poupança") == []


def test_factcheck_erro_de_rede_degrada(monkeypatch):
    monkeypatch.setattr(config, "FACTCHECK_API_KEY", "x")
    factcheck_api._cache.clear()
    import httpx

    def _falha(*a, **k):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(httpx, "get", _falha)
    assert factcheck_api.buscar("Governo vai confiscar a poupança") == []
