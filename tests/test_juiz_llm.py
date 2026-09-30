"""Juiz de 4 classes: prompt estruturado, citação verificada, retry de JSON, falha honesta."""
import json
import re

from factcheck_mvp import juiz_llm, llm


def _fake_local(respostas, vistos=None):
    """Substitui llm._local: devolve as respostas em sequência e guarda as conversas."""
    fila = list(respostas)

    def fake(messages, max_tokens, timeout_s, finalidade):
        if vistos is not None:
            vistos.append((finalidade, messages))
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, "modelo-fake"
    return fake


ITENS = [
    {"veiculo": "Estadão Verifica", "dominio": "estadao.com.br", "tipo_fonte": "checagem",
     "titulo": "É falso que café cura câncer",
     "veredito_pagina": {"selo": "Falso", "alegacao_checada": "Café cura câncer"},
     "trecho": "Post viral diz que café cura câncer. Segundo o INCA, não há evidência de que o café cure câncer."},
    {"veiculo": "G1", "dominio": "g1.globo.com", "tipo_fonte": "notícia", "titulo": "Dólar fecha em alta",
     "trecho": "A moeda americana subiu 1,2% nesta terça-feira."},
]


def test_citacao_exata_fuzzy_e_inexistente():
    texto = "Segundo o INCA, não há evidência científica de que o café cure qualquer tipo de câncer."
    assert juiz_llm.verificar_citacao("não há evidência científica de que o café cure", texto) == 1.0
    # acento/caixa/pontuação diferentes: normalizado
    assert juiz_llm.verificar_citacao("NAO HA EVIDENCIA cientifica de que o cafe cure!", texto) == 1.0
    # uma palavra trocada: fuzzy alto
    assert juiz_llm.verificar_citacao("não há evidência científica de que o café cura qualquer tipo", texto) >= 0.9
    # inventada
    assert juiz_llm.verificar_citacao("estudo de Harvard comprova que café cura tumores", texto) < 0.9
    assert juiz_llm.verificar_citacao("", texto) == 0.0


def test_prompt_estruturado_sem_resumidor_e_em_portugues(monkeypatch):
    vistos = []
    resp = json.dumps({"itens": [{"i": 0, "classe": "REFUTA", "citacao": "não há evidência de que o café cure câncer"},
                                 {"i": 1, "classe": "NAO_TRATA", "citacao": ""}]})
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local([resp], vistos))
    out = juiz_llm.julgar_lote("Café cura câncer", ITENS)
    fin, msgs = vistos[0]
    assert fin == "juiz" and len(vistos) == 1  # 1 chamada por lote, sem resumo intermediário
    prompt = msgs[-1]["content"]
    assert "português" in msgs[0]["content"] and "SUSTENTA" in prompt and "RELATA_SEM_ENDOSSO" in prompt
    assert '"veredito_pagina"' in prompt and '"veiculo": "Estadão Verifica"' in prompt
    assert '"relevante"' not in prompt
    assert out[0]["classe"] == "REFUTA" and out[0]["citacao_verificada"] is True
    assert out[1]["classe"] == "NAO_TRATA" and out[1]["citacao_verificada"] is None
    assert juiz_llm.postura_para_relevante(out[0]["classe"]) is True
    assert juiz_llm.postura_para_relevante(out[1]["classe"]) is False


def test_citacao_inexistente_rebaixa_para_nao_trata(monkeypatch):
    resp = json.dumps({"itens": [{"i": 0, "classe": "SUSTENTA", "citacao": "estudo prova que café cura câncer em 90% dos casos"},
                                 {"i": 1, "classe": "NAO_TRATA", "citacao": ""}]})
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local([resp]))
    out = juiz_llm.julgar_lote("Café cura câncer", ITENS)
    assert out[0]["classe"] == "NAO_TRATA" and out[0]["rebaixado"] is True
    assert out[0]["classe_original"] == "SUSTENTA" and out[0]["citacao_verificada"] is False
    assert "citação não encontrada" in out[0]["erro"]


def test_json_invalido_ganha_um_retry_com_correcao(monkeypatch):
    vistos = []
    boa = json.dumps([{"i": 0, "classe": "refuta", "citacao": "não há evidência de que o café cure câncer"},
                      {"i": 1, "classe": "NÃO TRATA", "citacao": ""}])
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local(["Claro! Aqui está: {itens: quebrado", boa], vistos))
    out = juiz_llm.julgar_lote("Café cura câncer", ITENS)
    assert len(vistos) == 2
    correcao = vistos[1][1][-1]["content"]
    assert "formato pedido" in correcao and "SOMENTE" in correcao
    assert [o["classe"] for o in out] == ["REFUTA", "NAO_TRATA"]  # lista crua e rótulos normalizados


def test_classe_fora_do_enum_invalida_e_falha_honesta(monkeypatch):
    ruim = json.dumps({"itens": [{"i": 0, "classe": "TALVEZ", "citacao": "x"}]})
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local([ruim, ruim]))
    out = juiz_llm.julgar_lote("Café cura câncer", ITENS)
    assert all(o["classe"] is None and o["motor"] == juiz_llm.MOTOR_FALLBACK for o in out)


def test_llm_fora_do_ar_nao_inventa_postura(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local([RuntimeError("conexão recusada")]))
    out = juiz_llm.julgar_lote("Café cura câncer", ITENS)
    assert [o["classe"] for o in out] == [None, None]
    assert all(o["motor"].startswith("fallback") for o in out)


def test_julgar_em_lotes(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    chamadas = []

    def fake(messages, max_tokens, timeout_s, finalidade):
        n = len(re.findall(r'^\{"i": ', messages[-1]["content"], re.M))
        chamadas.append(n)
        return json.dumps({"itens": [{"i": i, "classe": "NAO_TRATA", "citacao": ""} for i in range(n)]}), "m"
    monkeypatch.setattr(llm, "_local", fake)
    out = juiz_llm.julgar("Café cura câncer", ITENS * 6, lote=5)
    assert chamadas == [5, 5, 2] and len(out) == 12


def test_montar_trecho_prioriza_frases_com_termos():
    corpo = " ".join([f"Frase de enchimento número {i} sobre economia e esporte." for i in range(200)])
    corpo += " Conclusão: não há evidência de que o café cure câncer, diz o INCA."
    t = juiz_llm.montar_trecho("Título", corpo, "Café cura câncer", limite=800)
    assert len(t) <= 800 and t.startswith("Título")
    assert "café cure câncer" in t


def test_item_sem_classe_nao_derruba_o_lote(monkeypatch):
    resp = json.dumps({"itens": [{"i": 0, "classe": "REFUTA", "citacao": "não há evidência de que o café cure câncer"},
                                 {"i": 1, "pagina_diz": "fala do dólar"}]})
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _fake_local([resp]))
    out = juiz_llm.julgar_lote("Café cura câncer", ITENS)
    assert out[0]["classe"] == "REFUTA"
    assert out[1]["classe"] is None and out[1]["motor"] == "fallback-juiz-item"
