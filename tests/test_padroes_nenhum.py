"""Regressão do bug C5 do PR2: LLM local respondendo NENHUM não pode quebrar o unpack.

Na API atual (`analisar_padroes -> dict`, `_via_llm -> tuple`), NENHUM vira lista
vazia válida — sem cair no regex por acidente nem levantar no unpack.
"""
from factcheck_mvp import llm, padroes_llm


def _fake_local(respostas):
    fila = list(respostas)

    def fake(messages, max_tokens, timeout_s, finalidade):
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, "modelo-fake"
    return fake


def test_parse_nenhum_variacoes():
    assert padroes_llm._parse("NENHUM") == []
    assert padroes_llm._parse("Nenhum.") == []
    assert padroes_llm._parse("  nenhum  ") == []


def test_via_llm_nenhum_devolve_tupla(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local(["NENHUM"]))
    out = padroes_llm._via_llm("URGENTE compartilhe!!!")
    assert out == ([], "modelo-fake:modelo-fake") or (out[0] == [] and isinstance(out, tuple))


def test_analisar_padroes_nenhum_nao_cai_no_regex_sem_motivo(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local(["NENHUM"]))
    out = padroes_llm.analisar_padroes("texto neutro sem nenhum padrão", usar_llm=True)
    assert out["padroes"] == [] and "modelo-fake" in out["motor"]
