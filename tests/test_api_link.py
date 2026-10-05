"""API e web leem o link como o bot: texto da página ou pedido do texto (sem checar só a URL)."""
from fastapi.testclient import TestClient

from factcheck_mvp import api
from factcheck_mvp.schemas import EntradaConsulta, RelatorioChecagem

URL = "https://www.aosfatos.org/noticias/exemplo/"


class _PipeFalso:
    catalogo = object()

    def __init__(self):
        self.recebidas = []

    async def executar(self, entrada: EntradaConsulta, **_):
        self.recebidas.append(entrada)
        return RelatorioChecagem(propensao="indeterminada", justificativa="j", consulta=entrada)


def _cliente(monkeypatch, texto_pagina):
    pipe = _PipeFalso()
    monkeypatch.setattr(api, "pipeline", lambda: pipe)
    monkeypatch.setattr(api, "_texto_link", lambda url, catalogo: texto_pagina)
    monkeypatch.setattr(api, "_rate_ok", lambda request: True)
    return TestClient(api.app), pipe


def test_link_lido_vai_ao_pipeline_como_texto(monkeypatch):
    cli, pipe = _cliente(monkeypatch, "Título da checagem\n\nCorpo da página.")
    r = cli.post("/checar", json={"tipo": "link", "conteudo": URL})
    assert r.status_code == 200
    assert pipe.recebidas[0].tipo == "texto"
    assert URL in pipe.recebidas[0].conteudo and "Corpo da página." in pipe.recebidas[0].conteudo


def test_link_nao_lido_pede_o_texto(monkeypatch):
    cli, pipe = _cliente(monkeypatch, None)
    r = cli.post("/checar", json={"tipo": "link", "conteudo": URL})
    assert r.status_code == 422 and "Não consegui ler esse link" in r.json()["detail"]
    assert pipe.recebidas == []  # não checa só o endereço


def test_web_link_nao_lido_pede_o_texto(monkeypatch):
    cli, pipe = _cliente(monkeypatch, None)
    r = cli.post("/checar-web", data={"conteudo": URL})
    assert r.status_code == 422 and "Não consegui ler esse link" in r.text
    assert pipe.recebidas == []


def test_texto_comum_nao_passa_pela_leitura(monkeypatch):
    cli, pipe = _cliente(monkeypatch, "não deveria ser usado")
    r = cli.post("/checar", json={"tipo": "texto", "conteudo": "Governo vai confiscar a poupança"})
    assert r.status_code == 200 and pipe.recebidas[0].conteudo == "Governo vai confiscar a poupança"
