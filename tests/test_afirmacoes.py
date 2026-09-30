"""Afirmações em JSON: idioma e polaridade preservados, consulta de busca, nunca vazio."""
import json

from factcheck_mvp import afirmacoes, llm


def _local(respostas, vistos=None):
    fila = list(respostas)

    def fake(messages, max_tokens, timeout_s, finalidade):
        if vistos is not None:
            vistos.append(messages)
        return fila.pop(0), "m"
    return fake


def _setup(monkeypatch, respostas, vistos=None):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(llm, "_local", _local(respostas, vistos))


def test_json_com_polaridade_e_consulta(monkeypatch):
    _setup(monkeypatch, [json.dumps([{"afirmacao": "Café não cura câncer", "nucleo": "Café cura câncer",
                                      "polaridade": "nega", "consulta": "café cura câncer"}])])
    afs, motor = afirmacoes.extrair_afirmacoes("Café não cura câncer")
    assert motor.startswith("llm-local") and len(afs) == 1
    a = afs[0]
    assert (a.texto, a.nucleo, a.polaridade, a.consulta) == (
        "Café não cura câncer", "Café cura câncer", "nega", "café cura câncer")


def test_moldura_e_falso_que_forca_negacao_mesmo_se_o_llm_errar(monkeypatch):
    """Iteração 0: o LLM local removeu o 'É falso que' e passou a checar o boato."""
    _setup(monkeypatch, [json.dumps([{"afirmacao": "A vacina da covid altera o DNA", "nucleo": "",
                                      "polaridade": "afirma", "consulta": "vacina covid altera DNA"}])])
    afs, _ = afirmacoes.extrair_afirmacoes("É falso que a vacina da covid altera o DNA")
    assert afs[0].polaridade == "nega" and afs[0].nucleo == "A vacina da covid altera o DNA"


def test_ingles_ganha_retry_pedindo_portugues(monkeypatch):
    vistos = []
    _setup(monkeypatch, [
        json.dumps([{"afirmacao": "Electronic voting machines were frauded in the 2022 elections",
                     "nucleo": "", "polaridade": "afirma", "consulta": "voting machines fraud"}]),
        json.dumps([{"afirmacao": "As urnas eletrônicas foram fraudadas nas eleições de 2022",
                     "nucleo": "As urnas eletrônicas foram fraudadas nas eleições de 2022",
                     "polaridade": "afirma", "consulta": "urnas eletrônicas fraude eleições 2022"}])], vistos)
    afs, _ = afirmacoes.extrair_afirmacoes("As urnas eletrônicas foram fraudadas nas eleições de 2022")
    assert len(vistos) == 2 and "português" in vistos[1][-1]["content"]
    assert afs[0].texto.startswith("As urnas") and "2022" in afs[0].consulta


def test_vazio_do_llm_para_entrada_valida_usa_fallback_marcado(monkeypatch):
    _setup(monkeypatch, ["[]"])
    afs, motor = afirmacoes.extrair_afirmacoes("CAFÉ CURA CÂNCER!!! COMPARTILHE")
    assert motor == "fallback-regex" and afs and afs[0].texto == "Café cura câncer"


def test_llm_fora_usa_fallback(monkeypatch):
    monkeypatch.setattr(llm.config, "OPENROUTER_API_KEY", "")

    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(llm, "_local", boom)
    afs, motor = afirmacoes.extrair_afirmacoes("É verdade que o SUS vai acabar?")
    assert motor == "fallback-regex" and afs[0].texto == "O SUS vai acabar"


def test_normalizacao_caixa_alta_e_pontuacao():
    assert afirmacoes.normalizar_entrada("CAFÉ CURA CÂNCER!!! COMPARTILHE") == "Café cura câncer!"
    assert afirmacoes.normalizar_entrada("Vacina causa autismo???") == "Vacina causa autismo?"


def test_fallback_preserva_numeros_iniciais_e_polaridade():
    afs = afirmacoes._fallback("30 mil pessoas morreram de dengue em 2024. 2026 terá eleição presidencial.", 3)
    assert afs[0].texto.startswith("30 mil") and afs[1].texto.startswith("2026")
    neg = afirmacoes._fallback("É falso que a vacina da covid altera o DNA", 3)[0]
    assert neg.polaridade == "nega" and neg.nucleo == "A vacina da covid altera o DNA"
    assert neg.consulta == "vacina covid altera DNA"


def test_so_saudacao_sem_afirmacao():
    afs, motor = afirmacoes.extrair_afirmacoes("Bom dia! Compartilhe urgente com todos!!!", usar_llm=False)
    assert afs == [] and motor == "fallback-regex"


def test_nega_sem_negacao_no_texto_vira_afirma(monkeypatch):
    """Iteração 1: LLM marcou 'nega' em 'Vacina da covid altera o DNA humano' (inverteria a direção)."""
    _setup(monkeypatch, [json.dumps([{"afirmacao": "Vacina da covid altera o DNA humano",
                                      "nucleo": "Vacina da covid altera o DNA humano", "polaridade": "nega",
                                      "consulta": "vacina covid altera DNA humano"}])])
    a = afirmacoes.extrair_afirmacoes("Vacina da covid altera o DNA humano")[0][0]
    assert a.polaridade == "afirma" and a.nucleo == a.texto


def test_nega_com_nucleo_negado_deriva_nucleo(monkeypatch):
    _setup(monkeypatch, [json.dumps([{"afirmacao": "Café não cura câncer", "nucleo": "Café não cura câncer",
                                      "polaridade": "nega", "consulta": "café cura câncer"}])])
    a = afirmacoes.extrair_afirmacoes("Café não cura câncer")[0][0]
    assert a.polaridade == "nega" and a.nucleo == "Café cura câncer"


def test_consulta_sem_termos_inventados_nem_palavra_de_veredito(monkeypatch):
    _setup(monkeypatch, [json.dumps([{"afirmacao": "Café cura câncer", "nucleo": "Café cura câncer",
                                      "polaridade": "afirma", "consulta": "falso cafe cura cancer onco terapia"}])])
    a = afirmacoes.extrair_afirmacoes("Café cura câncer")[0][0]
    assert a.consulta == "Café cura câncer"
