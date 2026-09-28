"""LLM local respondendo NENHUM devolvia `[]` (não tupla) e quebrava o unpack
em `analisar_padroes`, caindo no regex sem motivo."""
from factcheck_mvp import config


def test_padroes_local_nenhum_devolve_tupla(monkeypatch):
    import httpx
    from factcheck_mvp import padroes_llm

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "NENHUM"}}]}

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "UNSLOTH_MODEL_NAME", "local")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _R())
    out = padroes_llm.analisar_padroes("URGENTE compartilhe!!!", usar_llm=True)
    assert out == {"padroes": [], "motor": "llm-local"}
