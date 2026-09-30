"""Cliente LLM único: descoberta do modelo local, roteamento, retry de JSON, fallback."""
from typing import List

from pydantic import BaseModel

from factcheck_mvp import llm


class _R:
    def __init__(self, dados, status=200):
        self._d, self.status_code, self.text = dados, status, str(dados)

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP")


def test_modelo_local_usa_o_carregado(monkeypatch):
    llm.resetar_cache()
    monkeypatch.setattr(llm.config, "UNSLOTH_MODEL_NAME", "")
    monkeypatch.setattr(llm.replay, "http_get", lambda url, **k: _R({"data": [
        {"id": "outro/modelo", "loaded": False}, {"id": "unsloth/LFM2.5-8B-A1B-GGUF", "loaded": True}]}))
    assert llm.modelo_local() == "unsloth/LFM2.5-8B-A1B-GGUF"
    llm.resetar_cache()


def test_local_envia_temperatura_zero_e_finalidade(monkeypatch):
    llm.resetar_cache()
    monkeypatch.setattr(llm.config, "UNSLOTH_MODEL_NAME", "m-local")
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    enviados = []

    def fake_post(url, payload, **k):
        enviados.append((payload, k))
        return _R({"choices": [{"message": {"content": '{"ok": true}'}}]})
    monkeypatch.setattr(llm.replay, "llm_post", fake_post)
    r = llm.chat_json([{"role": "user", "content": "x"}], None, finalidade="teste")
    assert r.ok and r.dados == {"ok": True} and r.motor == "llm-local"
    payload, kw = enviados[0]
    assert payload["temperature"] == 0.0 and kw["finalidade"] == "teste" and kw["motor"] == "llm-local"
    assert payload["messages"][0]["role"] == "system" and "português" in payload["messages"][0]["content"]


def test_resposta_so_de_raciocinio_e_erro(monkeypatch):
    monkeypatch.setattr(llm.config, "UNSLOTH_MODEL_NAME", "m-local")
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm.replay, "llm_post", lambda url, payload, **k: _R(
        {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "The user..."}}]}))
    r = llm.chat_json([{"role": "user", "content": "x"}], None, finalidade="teste")
    assert not r.ok and "truncada" in r.erro


class _Item(BaseModel):
    a: int


def test_retry_com_correcao_e_desistencia(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    conversas = []

    def fake(messages, max_tokens, timeout_s, finalidade):
        conversas.append(messages)
        return '{"a": "não é número"}', "m"
    monkeypatch.setattr(llm, "_local", fake)
    r = llm.chat_json([{"role": "user", "content": "x"}], _Item, finalidade="t")
    assert not r.ok and len(conversas) == 2 and "JSON inválido após retry" in r.erro
    assert conversas[1][-2]["role"] == "assistant" and "formato pedido" in conversas[1][-1]["content"]


def test_openrouter_falha_cai_no_local(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "k")
    ordem = []

    def orr(messages, max_tokens, timeout_s, finalidade):
        ordem.append("openrouter")
        raise RuntimeError("teto LLM diário atingido")

    def loc(messages, max_tokens, timeout_s, finalidade):
        ordem.append("local")
        return '[{"a": 1}]', "m"
    monkeypatch.setattr(llm, "_openrouter", orr)
    monkeypatch.setattr(llm, "_local", loc)
    r = llm.chat_json([{"role": "user", "content": "x"}], List[_Item], finalidade="t")
    assert r.ok and r.motor == "llm-local" and ordem == ["openrouter", "local"]
    assert r.dados[0].a == 1


def test_extrair_json_tolera_cercas_e_texto():
    assert llm.extrair_json('```json\n{"x": 1}\n```') == {"x": 1}
    assert llm.extrair_json('Aqui está:\n[{"x": 2}] fim') == [{"x": 2}]
