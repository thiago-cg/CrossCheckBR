"""Extração em cascata (extracao.extrair) + deep crawl (aprofundar) sem rede."""
import asyncio
import json

import pytest

from factcheck_mvp import aprofundar as ap
from factcheck_mvp import extracao as ex
from factcheck_mvp import replay, telemetria

FRASE = ("O Estadão Verifica investigou a publicação e concluiu que o conteúdo é enganoso, "
         "porque a lei citada foi sancionada em outro contexto. ")
CORPO_LONGO = FRASE * 8  # ~900 chars, texto corrido


def _pagina(corpo_p="", jsonld=None, extra=""):
    ld = (f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>'
          if jsonld else "")
    return (f"<html><head><title>Título da página</title>{ld}</head><body>"
            f"<nav><a href='/a'>Política</a><a href='/b'>Economia</a></nav>"
            f"{corpo_p}{extra}<footer>Todos os direitos reservados</footer></body></html>")


def _ps(n=12, frase=FRASE):
    return "".join(f"<p>{frase}Parágrafo {i}.</p>" for i in range(n))


@pytest.fixture
def eventos(monkeypatch):
    capt = []
    monkeypatch.setattr(telemetria, "evento", lambda tipo, **d: capt.append((tipo, d)))
    monkeypatch.setattr(telemetria, "fallback",
                        lambda onde, motivo, **d: capt.append(("fallback", {"onde": onde, "motivo": str(motivo), **d})))
    return capt


# ----------------------------------------------------------------------------- cascata
def test_jsonld_article_body_vence_e_traz_metadados(eventos):
    ld = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": "Manchete &amp; cia",
          "datePublished": "2026-09-25T10:00:00-03:00", "author": {"@type": "Person", "name": "Fulana"},
          "articleBody": CORPO_LONGO}
    r = ex.extrair(_pagina("<p>curto</p>", ld), "https://exemplo.com/x")
    assert r.metodo == "jsonld" and r.ok
    assert r.titulo == "Manchete & cia" and r.data_pub.startswith("2026-09-25") and r.autor == "Fulana"
    assert r.veredito_pagina is None


def test_claimreview_vira_veredito_pagina_independente_do_corpo(eventos):
    ld = {"@context": "https://schema.org", "@type": ["Review", "ClaimReview"],
          "claimReviewed": "Lula criou as bets", "reviewRating": {"@type": "Rating", "alternateName": "Enganoso"},
          "author": {"@type": "Organization", "name": "Estadão Verifica"}}
    r = ex.extrair(_pagina(f"<article>{_ps()}</article>", ld), "https://exemplo.com/verifica/x")
    assert r.metodo == "trafilatura" and r.ok
    assert r.veredito_pagina and r.veredito_pagina["selo_original"] == "Enganoso"
    assert r.veredito_pagina["veredito"] == "ENGANOSO"
    assert "jsonld: sem articleBody" in r.avisos


def test_articlebody_curto_cede_para_trafilatura(eventos):
    ld = {"@type": "NewsArticle", "headline": "H", "articleBody": "Resumo curto da matéria."}
    r = ex.extrair(_pagina(f"<article>{_ps()}</article>", ld))
    assert r.metodo == "trafilatura"
    assert any(a.startswith("jsonld: curto") for a in r.avisos)


def test_seletor_do_catalogo_quando_trafilatura_falha(monkeypatch, eventos):
    monkeypatch.setattr(ex, "_trafilatura", lambda h, u: ({}, None))
    html = _pagina(extra=f"<div class='materia'>{_ps()}</div>")
    r = ex.extrair(html, "", seletor_corpo="div.materia")
    assert r.metodo == "seletor" and r.ok and "Parágrafo 11" in r.texto


def test_regex_quando_nada_mais_serve_e_decodifica_entidades(monkeypatch, eventos):
    monkeypatch.setattr(ex, "_trafilatura", lambda h, u: ({}, None))
    frase = "Informa&ccedil;&atilde;o checada &#8211; a conclus&atilde;o &eacute; falsa &amp;#8211; ok. " * 2
    r = ex.extrair(_pagina(_ps(10, frase)))
    assert r.metodo == "regex" and r.ok
    assert "Informação checada – a conclusão é falsa – ok." in r.texto
    assert "&" not in r.texto


def test_falha_explicita_com_parcial_e_fallback(eventos):
    r = ex.extrair(_pagina("<p>Só uma frase curta de texto que não basta para a matéria.</p>"),
                   "https://exemplo.com/y")
    assert r.metodo == "falha" and not r.ok
    assert any(t == "fallback" and d["onde"] == "extracao" for t, d in eventos)


def test_html_vazio_nao_levanta(eventos):
    r = ex.extrair("", "")
    assert r.metodo == "falha" and r.texto == "" and r.avisos == ["html vazio"]


def test_menu_nao_vence(monkeypatch, eventos):
    monkeypatch.setattr(ex, "_trafilatura", lambda h, u: ({}, None))
    itens = "".join(f"<li><a href='/s{i}'>Seção número {i}</a></li>" for i in range(80))
    r = ex.extrair(f"<html><body><div class='menu'><ul>{itens}</ul></div></body></html>",
                   seletor_corpo="div.menu")
    assert r.metodo == "falha"
    assert any("navegação" in a for a in r.avisos)


def test_parece_navegacao():
    assert ex.parece_navegacao("\n".join(f"Item {i}" for i in range(30)))
    assert ex.parece_navegacao(" ".join(["palavra"] * 200))  # sem frases
    assert ex.parece_navegacao(CORPO_LONGO) is None


def test_jsonld_ausente_pula_nivel_com_aviso(monkeypatch, eventos):
    import builtins
    real = builtins.__import__

    def falso_import(nome, globais=None, locais=None, fromlist=(), nivel=0):
        if fromlist and "jsonld" in fromlist:
            raise ImportError("jsonld ainda não existe")
        return real(nome, globais, locais, fromlist, nivel)

    monkeypatch.setattr(builtins, "__import__", falso_import)
    r = ex.extrair(_pagina(f"<article>{_ps()}</article>"))
    assert r.metodo == "trafilatura"
    assert any(a.startswith("jsonld: módulo indisponível") for a in r.avisos)
    assert any(t == "fallback" and d["onde"] == "extracao.jsonld" for t, d in eventos)


# ----------------------------------------------------------------------------- ReaderLM (opt-in)
class _RespLLM:
    def __init__(self, conteudo, finish="stop", status=200):
        self.status_code, self._c, self._f, self.text = status, conteudo, finish, ""

    def json(self):
        return {"choices": [{"message": {"content": self._c}, "finish_reason": self._f}]}


def _sem_niveis_deterministicos(monkeypatch):
    monkeypatch.setattr(ex, "_trafilatura", lambda h, u: ({}, None))
    monkeypatch.setattr(ex, "paragrafos_regex", lambda h, n=60: [])


def test_readerlm_desligado_por_padrao(monkeypatch, eventos):
    monkeypatch.delenv("EXTRACAO_READERLM", raising=False)
    _sem_niveis_deterministicos(monkeypatch)
    chamado = []
    monkeypatch.setattr(replay, "llm_post", lambda *a, **k: chamado.append(1))
    r = ex.extrair(_pagina(_ps()))
    assert r.metodo == "falha" and not chamado


def test_readerlm_ancorado_vence_e_alucinacao_descartada(monkeypatch, eventos):
    monkeypatch.setenv("EXTRACAO_READERLM", "1")
    _sem_niveis_deterministicos(monkeypatch)
    capt = {}

    def falso_post(url, payload, **kw):
        capt.update(url=url, payload=payload, kw=kw)
        saida = {"titulo": "Título da página", "veredito": "Enganoso",
                 "afirmacao_checada": "Marcianos compraram o Congresso ontem",  # não está na página
                 "corpo": (FRASE * 6).strip(), "autor": "Unknown"}
        return _RespLLM("```json\n" + json.dumps(saida, ensure_ascii=False) + "\n```")

    monkeypatch.setattr(replay, "llm_post", falso_post)
    ld = {"@type": "NewsArticle", "headline": "x"}  # JSON-LD deve sair do HTML enviado
    r = ex.extrair(_pagina(f"<article>{_ps(6)}</article>", ld))
    assert r.metodo == "readerlm" and r.ok
    assert r.veredito_pagina["selo_original"] == "Enganoso" and r.veredito_pagina["origem"] == "readerlm"
    assert r.veredito_pagina["afirmacao_checada"] is None  # alucinação descartada
    assert any("afirmacao_checada não ancorado" in a for a in r.avisos)
    p = capt["payload"]
    assert capt["url"] == "http://127.0.0.1:8889/v1/chat/completions"
    assert p["temperature"] == 0 and p["max_tokens"] == 1024 and p["repetition_penalty"] == 1.08
    assert [m["role"] for m in p["messages"]] == ["user"]
    conteudo = p["messages"][0]["content"]
    assert conteudo.startswith("Extract the specified information from a list of news threads")
    assert "The JSON schema is as follows:```json" in conteudo
    assert "ld+json" not in conteudo and "<nav" not in conteudo


def test_readerlm_length_e_falha(monkeypatch, eventos):
    monkeypatch.setenv("EXTRACAO_READERLM", "1")
    _sem_niveis_deterministicos(monkeypatch)
    monkeypatch.setattr(replay, "llm_post", lambda *a, **k: _RespLLM("{\"corpo\": \"aaa", finish="length"))
    r = ex.extrair(_pagina(_ps()))
    assert r.metodo == "falha"
    assert any("finish_reason=length" in a for a in r.avisos)
    assert any(t == "fallback" and d["onde"] == "extracao.readerlm" for t, d in eventos)


def test_readerlm_html_grande_demais(monkeypatch, eventos):
    monkeypatch.setenv("EXTRACAO_READERLM", "1")
    monkeypatch.setenv("READERLM_MAX_CHARS", "100")
    _sem_niveis_deterministicos(monkeypatch)
    monkeypatch.setattr(replay, "llm_post", lambda *a, **k: pytest.fail("não devia chamar"))
    r = ex.extrair(_pagina(_ps()))
    assert r.metodo == "falha" and any("grande demais" in a for a in r.avisos)


def test_ancorado():
    assert ex.ancorado("conclusão é enganoso", "O texto diz que a conclusão é Enganoso.")
    assert not ex.ancorado("marcianos invadiram", "O texto diz que a conclusão é Enganoso.")
    assert not ex.ancorado("", "qualquer")


# ----------------------------------------------------------------------------- trecho
def test_montar_trecho_preserva_conclusao_no_fim():
    paras = ([f"Parágrafo de contexto número {i} sobre assuntos variados da cidade." for i in range(80)]
             + ["Conclusão: é falso que a vacina altera o DNA humano."])
    texto = "\n".join(paras)
    t = ap.montar_trecho(texto, "vacina altera DNA", limite=1500)
    assert len(t) <= 1520
    assert t.startswith("Parágrafo de contexto número 0")
    assert "Conclusão: é falso que a vacina altera o DNA humano." in t
    assert "[…]" in t
    assert ap.montar_trecho("curto", "x") == "curto"


# ----------------------------------------------------------------------------- aprofundar
class _Resp:
    def __init__(self, html, status=200, headers=None, truncado=False):
        self.status_code, self.headers = status, headers or {}
        self.content, self.truncado = html.encode(), truncado

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _CatVazio:
    def por_dominio(self, url):
        return None


def test_aprofundar_le_fora_do_catalogo_com_ua_navegador_e_5mb(monkeypatch, eventos):
    vistos = {}

    async def falso_get(url, max_bytes=None, headers=None, timeout=10.0, **kw):
        vistos[url] = (max_bytes, headers)
        if url.endswith("/curta"):
            return _Resp(_pagina("<p>Resumo curto de trinta palavras que antes contava como corpo lido.</p>"))
        return _Resp(_pagina(f"<article>{_ps()}<p>Conclusão: vacina não altera DNA.</p></article>"),
                     headers={"content-length": "9000000"}, truncado=True)

    monkeypatch.setattr(replay, "ahttp_get", falso_get)
    monkeypatch.setattr(ap, "_url_segura", lambda u: True)
    monkeypatch.delenv("DEEP_CRAWL_MAX_BYTES", raising=False)
    ap._cache.clear()
    cands = [{"url": "https://naocatalogado.com.br/materia", "_afirmacao": "vacina altera DNA"},
             {"url": "https://naocatalogado.com.br/curta"}]
    out = asyncio.run(ap.aprofundar(cands, _CatVazio(), por_afirm=3, budget_total=10))
    ok = out["https://naocatalogado.com.br/materia"]
    assert ok.corpo_lido and ok.metodo == "trafilatura" and ok.erro is None
    assert len(ok.texto_completo) >= 500 and ok.titulo and ok.trecho_corpo
    curta = out["https://naocatalogado.com.br/curta"]
    assert not curta.corpo_lido and curta.metodo == "falha" and "corpo curto" in curta.erro
    mb, hdr = vistos["https://naocatalogado.com.br/materia"]
    assert mb == 5_000_000 and "Mozilla" in hdr["User-Agent"]
    ext = [d for t, d in eventos if t == "fonte" and d.get("estagio") == "extracao"]
    assert {d["decisao"] for d in ext} == {"mantida", "descartada"}
    assert all("metodo" in d and "n_chars" in d for d in ext)
    ap._cache.clear()


def test_aprofundar_teto_por_env_e_ssrf(monkeypatch, eventos):
    monkeypatch.setenv("DEEP_CRAWL_MAX_BYTES", "123")
    assert ap.max_bytes_padrao() == 123
    monkeypatch.setenv("DEEP_CRAWL_MAX_BYTES", "lixo")
    assert ap.max_bytes_padrao() == 5_000_000
    r = asyncio.run(ap._baixar("http://127.0.0.1/admin", _CatVazio(), 1.0, 1000))
    assert not r.corpo_lido and "SSRF" in r.erro


def test_aprofundar_redirect_inseguro(monkeypatch, eventos):
    async def falso_get(url, **kw):
        return _Resp("", status=302, headers={"location": "http://169.254.169.254/meta"})

    monkeypatch.setattr(replay, "ahttp_get", falso_get)
    monkeypatch.setattr(ap, "_url_segura", lambda u: "169.254" not in u)
    r = asyncio.run(ap._baixar("https://site.com/x", _CatVazio(), 1.0, 1000))
    assert not r.corpo_lido and "redirect inseguro" in r.erro


def test_corpo_lido_contrato_campos():
    c = ap.CorpoLido(url="u")
    for campo in ("url", "final_url", "trecho_corpo", "corpo_lido", "erro", "texto_completo",
                  "metodo", "titulo", "data_pub", "veredito_pagina"):
        assert hasattr(c, campo)


# ----------------------------------------------------------------------------- HTML real (fixtures)
@pytest.mark.parametrize("nome,metodo,veredito", [
    ("aosfatos", "trafilatura", "FALSO"),
    ("boatos", "trafilatura", None),
    ("comprova", "trafilatura", "FALSO"),
    ("efarsas", "trafilatura", None),
    ("estadao_verifica", "trafilatura", "ENGANOSO"),
    ("g1_fato_ou_fake", "trafilatura", None),
    ("lupa", "jsonld", None),
])
def test_paginas_reais_de_checagem(nome, metodo, veredito, eventos):
    import gzip
    from pathlib import Path
    arq = Path(__file__).parent / "fixtures" / "jsonld" / f"{nome}.html.gz"
    if not arq.exists():
        pytest.skip("fixture ausente")
    r = ex.extrair(gzip.open(arq).read().decode("utf-8", "ignore"))
    assert r.metodo == metodo and r.ok, r.avisos
    assert ex.parece_navegacao(r.texto) is None and r.titulo
    assert (r.veredito_pagina or {}).get("veredito") == veredito
