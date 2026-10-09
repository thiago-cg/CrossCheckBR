"""API e web leem o link como o bot: texto da página ou pedido do texto (sem checar só a URL)."""
from fastapi.testclient import TestClient

from factcheck_mvp import api
from factcheck_mvp.schemas import EntradaConsulta, RelatorioChecagem
from factcheck_mvp.telegram_bot import LinkLido

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
    monkeypatch.setattr(api, "_texto_link", lambda url, catalogo: LinkLido(texto_pagina, None) if texto_pagina else None)
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


# --- E4 revisão (item 9, A5): entrada por link usa a data da própria página; sem data, E4 desligado ---
def _pagina(texto, data_pub):
    return LinkLido(texto, data_pub)


def test_linklido_nao_e_str_e_leva_a_data_como_campo():
    """M3: a data é campo do objeto (antes era atributo de um str, que str.strip() e o fatiamento perdiam)."""
    from dataclasses import fields, is_dataclass
    from factcheck_mvp.telegram_bot import entrada_de_link
    lido = LinkLido(texto="Título\n\nCorpo.", data_pub="2021-07-18T10:00:00-03:00")
    assert not isinstance(lido, str) and is_dataclass(lido)
    assert [f.name for f in fields(lido)] == ["texto", "data_pub"]
    assert entrada_de_link(URL, lido).data_referencia == "2021-07-18"


def _cliente_com_pagina(monkeypatch, texto, data_pub):
    pipe = _PipeFalso()
    monkeypatch.setattr(api, "pipeline", lambda: pipe)
    monkeypatch.setattr(api, "_texto_link", lambda url, catalogo: _pagina(texto, data_pub))
    monkeypatch.setattr(api, "_rate_ok", lambda request: True)
    return TestClient(api.app), pipe


def test_link_com_data_da_pagina_vira_data_de_referencia(monkeypatch):
    cli, pipe = _cliente_com_pagina(monkeypatch, "Título\n\nCorpo.", "2021-07-18T10:00:00-03:00")
    r = cli.post("/checar", json={"tipo": "link", "conteudo": URL})
    assert r.status_code == 200
    e = pipe.recebidas[0]
    assert e.data_referencia == "2021-07-18" and e.sem_referencia_temporal is False


def test_link_sem_data_desliga_o_e4_sem_cair_no_relogio(monkeypatch):
    cli, pipe = _cliente_com_pagina(monkeypatch, "Corpo sem data.", None)
    r = cli.post("/checar", json={"tipo": "link", "conteudo": URL})
    assert r.status_code == 200
    e = pipe.recebidas[0]
    assert e.data_referencia is None and e.sem_referencia_temporal is True


def test_link_so_com_ano_ou_placeholder_nao_serve_de_referencia(monkeypatch):
    for data in ("2021", "2026-01-01"):  # ano não é o "hoje" da matéria; -01-01 é placeholder
        cli, pipe = _cliente_com_pagina(monkeypatch, "Corpo.", data)
        assert cli.post("/checar", json={"tipo": "link", "conteudo": URL}).status_code == 200
        assert pipe.recebidas[-1].sem_referencia_temporal is True and pipe.recebidas[-1].data_referencia is None


def test_web_link_com_data_tambem_vira_referencia(monkeypatch):
    cli, pipe = _cliente_com_pagina(monkeypatch, "Título\n\nCorpo.", "2021-07-18T10:00:00-03:00")
    cli.post("/checar-web", data={"conteudo": URL})
    assert pipe.recebidas[0].data_referencia == "2021-07-18"


class _SessaoFalsa:
    """curl_cffi.Session substituto: devolve o HTML fixo, sem rede."""
    html = ""

    def __init__(self, **_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None):
        class _Resp:
            pass
        r = _Resp()
        r.url, r.text = URL, self.html
        return r


class _CatalogoAceitaTudo:
    def por_dominio(self, url):
        return True


def test_texto_link_guarda_a_data_publicada_do_json_ld(monkeypatch):
    import curl_cffi.requests as _curl
    from factcheck_mvp import telegram_bot as tb
    _SessaoFalsa.html = ('<html><head><title>Checagem</title><script type="application/ld+json">'
                         '{"@context": "https://schema.org", "@type": "NewsArticle", "headline": "Checagem",'
                         ' "datePublished": "2021-07-18T10:00:00-03:00"}</script></head>'
                         '<body><p>' + "Texto da matéria checada. " * 20 + '</p></body></html>')
    monkeypatch.setattr(_curl, "Session", _SessaoFalsa)
    lido = tb._texto_link(URL, _CatalogoAceitaTudo())
    assert lido and "Texto da matéria" in lido.texto
    assert lido.data_pub == "2021-07-18T10:00:00-03:00"


def test_texto_link_sem_data_na_pagina_devolve_none_na_data(monkeypatch):
    import curl_cffi.requests as _curl
    from factcheck_mvp import telegram_bot as tb
    _SessaoFalsa.html = "<html><head><title>Sem data</title></head><body><p>" + "Texto. " * 60 + "</p></body></html>"
    monkeypatch.setattr(_curl, "Session", _SessaoFalsa)
    lido = tb._texto_link(URL, _CatalogoAceitaTudo())
    assert lido and lido.data_pub is None


def test_referencia_do_encaminhamento_e_o_dia_brt_da_mensagem_original():
    from datetime import datetime, timezone
    from factcheck_mvp import telegram_bot as tb
    assert tb.referencia_do_encaminhamento(datetime(2026, 10, 9, 2, 30, tzinfo=timezone.utc)) == "2026-10-08"
    assert tb.referencia_do_encaminhamento(datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)) == "2026-10-09"
    assert tb.referencia_do_encaminhamento(datetime(2026, 10, 9, 2, 30)) == "2026-10-08"  # naive = UTC
    assert tb.referencia_do_encaminhamento(None) is None


def test_data_da_pagina_sem_json_ld_vem_dos_metadados():
    from factcheck_mvp import extracao
    html = ('<html><head><title>Checagem de meta</title>'
            '<meta property="article:published_time" content="2021-07-18T10:00:00-03:00"></head>'
            '<body><article><h1>Checagem de meta</h1><p>' + "A matéria explica o caso com fontes. " * 12 +
            '</p></article></body></html>')
    assert extracao.data_publicacao_pagina(html, URL) == "2021-07-18"
    assert extracao.data_publicacao_pagina("<html><body><p>nada</p></body></html>", URL) is None
    assert extracao.data_publicacao_pagina("", URL) is None
