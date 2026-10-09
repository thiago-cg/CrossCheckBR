import pytest

from factcheck_mvp import aplicabilidade


def test_aplicavel_exige_corpo_citacao_selo_data():
    from factcheck_mvp import aplicabilidade
    ok, motivo = aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", "Café cura câncer", "2026-09-22")
    assert ok is True
    assert aplicabilidade.e_aplicavel("REFUTA", True, False, "FALSO", "Café cura câncer", "2026-09-22")[0] is False
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, None, "Café cura câncer", "2026-09-22")[0] is False
    assert aplicabilidade.e_aplicavel("NAO_TRATA", True, True, "FALSO", "Café cura câncer", "2026-09-22")[0] is False
    assert aplicabilidade.e_aplicavel("REFUTA", False, True, "FALSO", "Café cura câncer", "2026-09-22")[0] is False


def test_data_incompativel_barra_bolsonaro():
    from factcheck_mvp import aplicabilidade
    assert aplicabilidade.data_compativel("Bolsonaro recebeu alta do hospital hoje", "2020-01-01") is False
    assert aplicabilidade.data_compativel("Bolsonaro recebeu alta do hospital hoje", None) is True
    assert aplicabilidade.data_compativel("Café cura câncer", "2020-01-01") is True


def test_dias_excedentes_mede_alem_da_janela():
    from factcheck_mvp import aplicabilidade
    assert aplicabilidade.dias_excedentes("recebeu alta hoje", "2026-10-08", "2026-10-09") == (2, 0)
    assert aplicabilidade.dias_excedentes("recebeu alta hoje", "2026-10-01", "2026-10-09") == (2, 6)
    assert aplicabilidade.dias_excedentes("Café cura câncer", "2020-01-01", "2026-10-09") is None
    assert aplicabilidade.dias_excedentes("recebeu alta hoje", None, "2026-10-09") is None


# --- Task 2 (E4): normalizar_data (imports locais p/ não conflitar com Tasks 3/4) ---
import pytest as _pytest2
from factcheck_mvp.aplicabilidade import normalizar_data as _normalizar_data2


@_pytest2.mark.parametrize("valor,ancora,esperado", [
    ("2026-09-22T02:00:00+00:00", None, ("2026-09-21", "dia")),   # 23h BRT do dia 21
    ("2024-02-23T16:09:57-03:00", None, ("2024-02-23", "dia")),
    ("2024-04-04 13:21:00", None, ("2024-04-04", "dia")),
    ("26 de jan. de 2012", None, ("2012-01-26", "dia")),
    ("3 de julho de 2026", None, ("2026-07-03", "dia")),
    ("08/10/2026", None, ("2026-10-08", "dia")),
    ("há 3 dias", "2026-10-09", ("2026-10-06", "dia")),
    ("2 dias atrás", "2026-10-09", ("2026-10-07", "dia")),
    ("há 5 horas", "2026-10-09", ("2026-10-09", "dia")),
    ("2021", None, ("2021-12-31", "ano")),
    ("2026-01-01", None, ("2026-12-31", "ano")),
])
def test_normalizar_data_formatos_reais(valor, ancora, esperado):
    assert _normalizar_data2(valor, ancora) == esperado


@_pytest2.mark.parametrize("valor", [None, "", "2000-01-01", "1970-01-01", "ontem à tarde", "há 3 dias"])
def test_normalizar_data_desconhecida_vira_none(valor):
    assert _normalizar_data2(valor, None) is None  # relativa sem âncora também


@pytest.mark.parametrize("texto,janela", [
    ("Hoje em dia ninguém lê jornal", None),
    ("Até hoje a obra não terminou", None),
    ("A partir de agora o Pix será taxado", None),
    ("O ministro caiu esta manhã", 2),
    ("Há pouco o presidente renunciou", 2),
    ("Anteontem houve um terremoto em Natal", 4),
    ("Nesta segunda-feira o STF decidiu", 8),
    ("Neste domingo houve apagão em SP", 8),
    ("Na semana passada o dólar bateu R$ 7", 15),
    ("Bolsonaro recebeu alta do hospital hoje", 2),
])
def test_janela_marcadores(texto, janela):
    assert aplicabilidade.janela_temporal(texto) == janela

@pytest.mark.parametrize("texto,janela", [
    # falsos positivos excluídos: "há pouco mais de N…" é quantidade, e "nos dias de hoje" é
    # a mesma classe de "hoje em dia" (não marca o momento do fato)
    ("Há pouco mais de dez anos o país mudou", None),
    ("Nos dias de hoje ninguém lê jornal impresso", None),
    # lacunas da mesma razão linguística das já existentes (semana / fim de semana / mês)
    ("Neste final de semana houve jogo", 8),
    ("Este domingo houve apagão em SP", 8),
    ("Esta segunda-feira o STF decidiu", 8),
    ("Esta semana o governo anunciou o pacote", 8),
    ("Nessa segunda o presidente viajou", 8),
    ("Nesse domingo houve apagão", 8),
    ("Na última semana o dólar caiu", 15),   # = "semana passada": mesmo período (semana anterior)
    ("Agora, em outubro, a inflação subiu", 32),
    ("Agora em outubro a inflação subiu", 32),
    ("Nesse mês houve queda da renda", 32),
    # "segunda" ordinal não é dia da semana
    ("Esta segunda parte do documentário é ruim", None),
])
def test_janela_marcadores_exclusoes_e_lacunas(texto, janela):
    assert aplicabilidade.janela_temporal(texto) == janela


def test_config_janela_invalida_cai_no_default_e_avisa(monkeypatch):
    from factcheck_mvp import config
    for bruto in ("0", "-3", "abc"):
        monkeypatch.setenv("X_TESTE_JANELA_E4", bruto)
        avisos = []
        assert config._janela_positiva("X_TESTE_JANELA_E4", 2, avisos=avisos) == 2
        assert len(avisos) == 1 and "X_TESTE_JANELA_E4" in avisos[0]
    monkeypatch.setenv("X_TESTE_JANELA_E4", "5")
    avisos = []
    assert config._janela_positiva("X_TESTE_JANELA_E4", 2, avisos=avisos) == 5 and not avisos


def test_janelas_efetivas_vem_da_config(monkeypatch):
    from factcheck_mvp import config
    monkeypatch.setattr(config, "E4_JANELA_SEMANA", 11)
    efetivas = aplicabilidade.janelas_efetivas()
    assert efetivas["E4_JANELA_SEMANA"] == 11 and efetivas["E4_JANELA_HOJE"] == config.E4_JANELA_HOJE
    assert aplicabilidade.janela_temporal("Nesta semana houve chuva") == 11


def test_janela_por_afirmacao_nao_vaza_para_outra_frase():
    t = "O deputado disse hoje que a ponte caiu em 2019. A obra custou 2 bilhões."
    assert aplicabilidade.janela_da_afirmacao("A obra custou 2 bilhões", t, 2) is None
    assert aplicabilidade.janela_da_afirmacao("Bolsonaro recebeu alta do hospital", "Bolsonaro recebeu alta do hospital hoje", 1) == 2


def test_dias_excedentes_sem_referencia_nao_mede():
    from factcheck_mvp import aplicabilidade
    assert aplicabilidade.dias_excedentes("recebeu alta hoje", "2020-01-01", None) is None


# --- Task 4b (E4): datas explícitas ancoram o EVENTO em D (frases sintéticas, fora do eval) ---
def test_marco_do_evento_data_explicita():
    assert aplicabilidade.marco_do_evento("O jogo foi dia 8", "2026-10-09") == ("2026-10-08", 2)
    assert aplicabilidade.marco_do_evento("A final foi 8 de outubro", "2026-10-09") == ("2026-10-08", 2)
    assert aplicabilidade.marco_do_evento("A prova ocorreu em 08/10", "2026-10-09") == ("2026-10-08", 2)
    assert aplicabilidade.marco_do_evento("A prova ocorreu em 08/10.", "2026-10-09") == ("2026-10-08", 2)
    assert aplicabilidade.marco_do_evento("O sorteio foi dia 8, às 8h", "2026-10-09") == ("2026-10-08", 2)


def test_marco_do_evento_ano_inferido_mais_recente():
    assert aplicabilidade.marco_do_evento("A festa foi dia 15", "2026-10-09") == ("2026-09-15", 2)
    assert aplicabilidade.marco_do_evento("O show foi 8 de outubro de 2023", "2026-10-09") == ("2023-10-08", 2)


def test_marco_excedente_pre_e_pos_evento():
    marco = ("2026-10-08", 2)
    assert aplicabilidade.dias_excedentes("O jogo foi dia 8", "2026-10-09", "2026-10-09", marco) == (2, 0)
    assert aplicabilidade.dias_excedentes("O jogo foi dia 8", "2026-10-08", "2026-10-09", marco) == (2, 0)
    assert aplicabilidade.dias_excedentes("O jogo foi dia 8", "2026-10-01", "2026-10-09", marco) == (2, 5)
    assert aplicabilidade.data_compativel("O jogo foi dia 8", "2026-10-09", "2026-10-09", marco) is True
    assert aplicabilidade.data_compativel("O jogo foi dia 8", "2020-01-01", "2026-10-09", marco) is False


def test_marco_do_evento_citada_ambigua_ou_futura_nao_ancora():
    assert aplicabilidade.marco_do_evento("Pague até dia 8", "2026-10-09") is None
    assert aplicabilidade.marco_do_evento("Parabéns! Aniversário dia 8", "2026-10-09") is None
    assert aplicabilidade.marco_do_evento("Chegou dia 5 e saiu dia 8", "2026-10-09") is None
    assert aplicabilidade.marco_do_evento("A final será 8 de outubro de 2027", "2026-10-09") is None
    assert aplicabilidade.marco_do_evento("Café cura câncer", "2026-10-09") is None
    assert aplicabilidade.marco_do_evento("Bolsonaro recebeu alta do hospital hoje", "2026-10-09") is None
    assert aplicabilidade.marco_do_evento("O jogo foi dia 8", None) is None


def test_dias_excedentes_sem_marco_mantem_comportamento():
    assert aplicabilidade.dias_excedentes("recebeu alta hoje", "2026-10-01", "2026-10-09") == (2, 6)
    assert aplicabilidade.dias_excedentes("recebeu alta hoje", "2026-10-01", "2026-10-09", None) == (2, 6)


# --- Task 5 (E4): gate E1 usa a mesma janela, referência e data da decisão (continua binário) ---
def test_gate_usa_o_hoje_do_texto_quando_a_afirmacao_perdeu_o_marcador():
    """O extrator pode reescrever a afirmação sem o "hoje": a janela vem do texto do usuário
    (1 afirmação) e a checagem de 2025 não é aplicável a um fato de 2026-10-09."""
    af = "Bolsonaro recebeu alta do hospital"
    janela = aplicabilidade.janela_da_afirmacao(af, "Bolsonaro recebeu alta do hospital hoje", 1)
    assert janela == 2
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", af, "2025-04-23",
                                      "2026-10-09", janela) == (False, "data incompatível")


def test_gate_sem_referencia_desconhecida_nao_barra_por_data():
    af = "Bolsonaro recebeu alta do hospital"
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", af, "2025-04-23",
                                      referencia=None, janela=2) == (True, "aplicável")


def test_gate_janela_none_explicita_nao_herda_o_hoje_de_outra_frase():
    """Afirmação sem marcador (janela None): o "hoje" de outra frase não barra a checagem."""
    texto = "A ponte caiu em 2019. Bolsonaro recebeu alta do hospital hoje."
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", "A ponte caiu em 2019", "2019-06-01",
                                      referencia="2026-10-09", janela=None) == (True, "aplicável")
    # sem `janela`, o E1 anterior continua: a janela vem do texto recebido
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", texto, "2025-04-23",
                                      referencia="2026-10-09") == (False, "data incompatível")


def test_gate_defaults_mantem_e1_sem_marcador():
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", "Café cura câncer", "2020-01-01")[0] is True


def test_dias_excedentes_da_janela_mede_com_janela_explicita():
    assert aplicabilidade.dias_excedentes_da_janela(2, "2026-10-01", "2026-10-09") == (2, 6)
    assert aplicabilidade.dias_excedentes_da_janela(2, "2026-10-08", "2026-10-09") == (2, 0)
    assert aplicabilidade.dias_excedentes_da_janela(None, "2026-10-01", "2026-10-09") is None
    assert aplicabilidade.dias_excedentes_da_janela(2, "2026-10-01", None) is None
    assert aplicabilidade.dias_excedentes_da_janela(2, None, "2026-10-09") is None
