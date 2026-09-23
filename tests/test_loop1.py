"""Aprofundar SSRF/caps + OpenRouter fallback (sem rede: mocks)."""
from factcheck_mvp import aprofundar as ap
from factcheck_mvp import llm_openrouter as oo
from factcheck_mvp.catalogo import Catalogo


def test_url_insegura_bloqueada():
    for u in ["http://127.0.0.1/x", "http://10.0.0.1/", "ftp://exemplo.com/x",
              "http://exemplo.com:8080/x", "http://user@exemplo.com/"]:
        assert ap._url_segura(u) is False


def test_extrair_corpo():
    html = "<title>T</title><p>" + "palavra " * 100 + "</p>"
    assert len(ap.extrair_corpo(html)) > 200


def test_extrair_corpo_remove_boilerplate():
    menu = "<p>" + " ".join(f"<a href='/x{i}'>Link menu {i}</a>" for i in range(8)) + "</p>"
    materia = "<p>" + "fato apurado pela reportagem " * 20 + "</p>"
    html = (f"<html><head><title>Notícia</title></head><body><nav>{menu}</nav>"
            f"<article>{materia}</article><footer><p>Rodapé institucional</p></footer>"
            f"</body></html>")
    corpo = ap.extrair_corpo(html)
    assert "Link menu" not in corpo and "Rodapé" not in corpo
    assert "fato apurado" in corpo


def test_extrair_corpo_so_boilerplate_volta_curto():
    html = ("<html><body><nav><p><a href='/a'>Home</a> <a href='/b'>Editorias</a></p></nav>"
            "<footer><p>Todos os direitos</p></footer></body></html>")
    assert len(ap.extrair_corpo(html)) < 200  # vira erro corpo-curto no _baixar


def test_openrouter_cap_e_sem_chave(monkeypatch):
    monkeypatch.setattr(oo.config, "OPENROUTER_API_KEY", "")
    try:
        oo.chat([{"role": "user", "content": "oi"}])
        assert False, "deveria falhar sem chave"
    except RuntimeError:
        pass


def test_openrouter_fallback(monkeypatch):
    import httpx as _hx
    monkeypatch.setattr(oo.config, "OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(oo.config, "OPENROUTER_MODEL", "prim")
    monkeypatch.setattr(oo.config, "OPENROUTER_FALLBACK_MODEL", "fb")
    monkeypatch.setattr(oo, "_cap_estourado", lambda: False)

    def fake_post(modelo, messages, mt, ts):
        if modelo == "prim":
            raise RuntimeError("500")
        return "VAZIO", modelo

    # _post quebrado no primary: chat deve tentar fallback via _post real? mock _post
    calls = []
    def _fake(modelo, messages, max_tokens, timeout_s):
        calls.append(modelo)
        if modelo == "prim":
            raise _hx.HTTPError("500")
        return "linhas ok"
    monkeypatch.setattr(oo, "_post", _fake)
    txt, modelo = oo.chat([{"role": "user", "content": "x"}])
    assert txt == "linhas ok" and modelo == "fb" and calls == ["prim", "fb"]


def test_chat_repete_resposta_vazia_1x_no_mesmo_modelo(monkeypatch):
    """200 com conteúdo vazio (StreamLake): 1 retry no primary antes do fallback."""
    monkeypatch.setattr(oo.config, "OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(oo.config, "OPENROUTER_MODEL", "prim")
    monkeypatch.setattr(oo.config, "OPENROUTER_FALLBACK_MODEL", "fb")
    monkeypatch.setattr(oo, "_cap_estourado", lambda: False)
    calls = []

    def _fake(modelo, messages, max_tokens, timeout_s):
        calls.append(modelo)
        if len(calls) == 1:
            raise RuntimeError("resposta vazia do modelo prim")
        return "recuperado"

    monkeypatch.setattr(oo, "_post", _fake)
    txt, modelo = oo.chat([{"role": "user", "content": "x"}])
    assert txt == "recuperado" and modelo == "prim" and calls == ["prim", "prim"]
