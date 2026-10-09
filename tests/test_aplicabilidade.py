import pytest

from factcheck_mvp import aplicabilidade


def test_aplicavel_exige_corpo_citacao_selo_data():
    from factcheck_mvp import aplicabilidade
    ok, motivo = aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", "Café cura câncer", "2026-09-22", "2026-10-09")
    assert ok is True
    assert aplicabilidade.e_aplicavel("REFUTA", True, False, "FALSO", "Café cura câncer", "2026-09-22", "2026-10-09")[0] is False
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, None, "Café cura câncer", "2026-09-22", "2026-10-09")[0] is False
    assert aplicabilidade.e_aplicavel("NAO_TRATA", True, True, "FALSO", "Café cura câncer", "2026-09-22", "2026-10-09")[0] is False
    assert aplicabilidade.e_aplicavel("REFUTA", False, True, "FALSO", "Café cura câncer", "2026-09-22", "2026-10-09")[0] is False


def test_data_incompativel_barra_bolsonaro():
    from factcheck_mvp import aplicabilidade
    assert aplicabilidade.data_compativel("Bolsonaro recebeu alta do hospital hoje", "2020-01-01", "2026-10-09") is False
    assert aplicabilidade.data_compativel("Bolsonaro recebeu alta do hospital hoje", None, "2026-10-09") is True
    assert aplicabilidade.data_compativel("Café cura câncer", "2020-01-01", "2026-10-09") is True


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


# --- Item E4 (decisão da usuária, 09/10): data explícita vence o marcador relativo ---
def test_data_explicita_da_afirmacao_vence_o_hoje_do_ato_de_dizer():
    """"O deputado disse hoje que a ponte caiu em 2019": o "hoje" é do ato de dizer. Sem janela."""
    t = "O deputado disse hoje que a ponte caiu em 2019"
    assert aplicabilidade.janela_temporal(t) == 2  # o marcador existe; a regra é que não se aplica
    assert aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-10-09") is None
    # frase com o ano na afirmação e o "hoje" no texto inteiro (2 frases): nenhuma herda o "hoje"
    texto = "O deputado disse hoje que a ponte caiu. A ponte foi inaugurada em 2019."
    assert aplicabilidade.janela_da_afirmacao("A ponte foi inaugurada em 2019", texto, 2, "2026-10-09") is None


def test_marcador_sem_data_explicita_continua_valendo():
    assert aplicabilidade.janela_da_afirmacao("Bolsonaro recebeu alta do hospital hoje",
                                              "Bolsonaro recebeu alta do hospital hoje", 1, "2026-10-09") == 2


def test_ano_igual_ao_da_referencia_nao_desliga_o_marcador():
    """Decisão documentada: "hoje, em 2026, ..." (ano = ano da referência) segue sendo o fato de hoje.
    Só ano diferente da referência aponta outro tempo e desliga o marcador."""
    assert aplicabilidade.janela_da_afirmacao("Hoje, em 2026, o ministro caiu",
                                              "Hoje, em 2026, o ministro caiu", 1, "2026-10-09") == 2
    assert aplicabilidade.janela_da_afirmacao("Hoje, em 2025, o ministro caiu",
                                              "Hoje, em 2025, o ministro caiu", 1, "2026-10-09") is None


def test_data_completa_igual_a_referencia_mantem_e_diferente_desliga():
    assert aplicabilidade.janela_da_afirmacao("Hoje, 09/10/2026, o ministro caiu",
                                              "Hoje, 09/10/2026, o ministro caiu", 1, "2026-10-09") == 2
    # dia 8 (outro dia) é data explícita: o "hoje" não descreve o fato; o marco (dia 8) mede
    assert aplicabilidade.janela_da_afirmacao("Hoje, dia 8, o time ganhou",
                                              "Hoje, dia 8, o time ganhou", 1, "2026-10-09") is None
    assert aplicabilidade.marco_da_afirmacao("Hoje, dia 8, o time ganhou",
                                             "Hoje, dia 8, o time ganhou", 1, "2026-10-09") == ("2026-10-08", 2)


def test_data_explicita_sem_referencia_conhecida_tambem_desliga():
    """Sem referência não dá para confirmar que a data coincide com o "hoje": conta como explícita."""
    assert aplicabilidade.janela_da_afirmacao("O deputado disse hoje que a ponte caiu em 2019",
                                              "O deputado disse hoje que a ponte caiu em 2019", 1) is None


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


@pytest.mark.parametrize("texto", [
    "2/3 dos eleitores aprovam o projeto",          # fração (N/M seguido de "dos")
    "A taxa de 3/4 do PIB caiu",                     # fração sem contexto de data
    "O placar foi 3/1 para o time",                  # placar, sem contexto de data
    "A eleição será no dia 25 de outubro",           # agendamento: verbo no futuro
    "A votação será no dia 20",                      # agendamento
    "Dia 8 de março é o Dia da Mulher",              # data comemorativa, não data do fato
    "O jogo foi dia 8 de 2026",                      # dia com ano e sem mês: não é data
])
def test_marco_do_evento_rejeita_fracoes_agendamento_e_comemorativa(texto):
    assert aplicabilidade.marco_do_evento(texto, "2026-10-09") is None


def test_marco_do_evento_intervalo_usa_o_inicio():
    """'de 2 a 8 de outubro': o início do intervalo é o marco. Fonte posterior ao início não é
    outro episódio (fica sem desconto); só fonte bem anterior ao início é descontada."""
    assert aplicabilidade.marco_do_evento("O evento foi de 2 a 8 de outubro", "2026-10-09") == ("2026-10-02", 2)
    assert aplicabilidade.marco_do_evento("A feira foi de 30 de setembro a 2 de outubro", "2026-10-09") == (
        "2026-09-30", 2)


def test_marco_do_evento_ano_inferido_limitado_a_60_dias():
    """Ano inferido com ocorrência a mais de 60 dias da referência não é confiável: não ancora.
    Ano explícito vale qualquer que seja a distância."""
    assert aplicabilidade.marco_do_evento("A festa foi dia 8 de março", "2026-10-09") is None
    assert aplicabilidade.marco_do_evento("A festa foi dia 8 de março de 2026", "2026-10-09") == ("2026-03-08", 2)


def test_e_aplicavel_com_marco_barra_fonte_de_outro_episodio():
    """O gate usa o mesmo marco da decisão (Task 5 + 4b): fonte de 2026-09-01 está 35 dias além do
    evento de 2026-10-08 (folga 2); fonte do próprio dia do evento é aplicável."""
    marco = ("2026-10-08", 2)
    texto = "O jogo foi dia 8 e o time ganhou"
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", texto, "2026-09-01",
                                      "2026-10-09", janela=None, marco=marco) == (False, "data incompatível")
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", texto, "2026-10-08",
                                      "2026-10-09", janela=None, marco=marco) == (True, "aplicável")
    assert aplicabilidade.medida_da_afirmacao(None, marco, "2026-09-01", "2026-10-09") == (2, 35)


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
    assert aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", "Café cura câncer", "2020-01-01", "2026-10-09")[0] is True


def test_dias_excedentes_da_janela_mede_com_janela_explicita():
    assert aplicabilidade.dias_excedentes_da_janela(2, "2026-10-01", "2026-10-09") == (2, 6)
    assert aplicabilidade.dias_excedentes_da_janela(2, "2026-10-08", "2026-10-09") == (2, 0)
    assert aplicabilidade.dias_excedentes_da_janela(None, "2026-10-01", "2026-10-09") is None
    assert aplicabilidade.dias_excedentes_da_janela(2, "2026-10-01", None) is None
    assert aplicabilidade.dias_excedentes_da_janela(2, None, "2026-10-09") is None


# --- E4 revisão (itens 1-3, 7): normalizar_data — meia-noite UTC, totalidade, formatos comuns ---
@pytest.mark.parametrize("valor,esperado", [
    ("1970-01-01T00:00:00Z", None),                          # sentinela com hora 00:00 UTC
    ("2000-01-01T00:00:00+00:00", None),                     # idem, offset explícito
    ("2026-01-01T00:00:00+00:00", ("2026-12-31", "ano")),    # -01-01 à data LITERAL = placeholder de ano
    ("2022-09-06T00:00:00.000+00:00", ("2022-09-06", "dia")),  # meia-noite UTC = só data: não recua p/ 05/09 BRT
    ("2022-09-06T00:00:00Z", ("2022-09-06", "dia")),
    ("2024-02-23T00:00:00-03:00", ("2024-02-23", "dia")),    # meia-noite já em BRT: nada a converter
])
def test_normalizar_data_meia_noite_utc_e_so_data(valor, esperado):
    assert aplicabilidade.normalizar_data(valor, None) == esperado


@pytest.mark.parametrize("valor,ancora", [
    ("0001-01-01T00:00:00+05:00", None),          # converter p/ BRT estoura o calendário
    ("há 999999999 dias", "2026-10-09"),          # N absurdo
    ("há 99999999999999999999 dias", "2026-10-09"),
    ("há 100000 anos", "2026-10-09"),             # cap de N / ano mínimo
    ("2026-02-30", None),                         # dia inexistente
    ("2026-13-01T10:00:00Z", None),
    ("31/02/2026", None),
    ("0000-01-01", None),
    ("há 3 dias", "2026-13-40"),                  # âncora inválida
    ("ontem à tarde", None),
])
def test_normalizar_data_nunca_levanta(valor, ancora):
    assert aplicabilidade.normalizar_data(valor, ancora) is None  # sem exceção


@pytest.mark.parametrize("valor,ancora,esperado", [
    ("Tue, 06 Sep 2022 00:00:00 GMT", None, ("2022-09-06", "dia")),      # RFC 2822 (ingestor.py:_data_iso)
    ("Tue, 06 Sep 2022 22:30:00 -0300", None, ("2022-09-06", "dia")),
    ("06 Sep 2022", None, ("2022-09-06", "dia")),
    ("Mar 3, 2024", None, ("2024-03-03", "dia")),
    ("31/12/2025 10:00", None, ("2025-12-31", "dia")),
    ("26 de jan. de 2012 às 10:00", None, ("2012-01-26", "dia")),
    ("1º de março de 2020", None, ("2020-03-01", "dia")),
    ("há um dia", "2026-10-09", ("2026-10-08", "dia")),
    ("há uma semana", "2026-10-09", ("2026-10-02", "dia")),
    ("2021-05", None, ("2021-05-31", "ano")),                             # mês: fim do mês, precisão ano
    ("2026-09-28 20:40:30 UTC", None, ("2026-09-28", "dia")),             # 17:40 BRT
])
def test_normalizar_data_formatos_comuns(valor, ancora, esperado):
    assert aplicabilidade.normalizar_data(valor, ancora) == esperado


def test_motivo_do_fallback_separa_relativa_sem_ancora_de_formato_ilegivel():
    assert aplicabilidade.motivo_data_ilegivel("há 3 dias") == "sem âncora"
    assert aplicabilidade.motivo_data_ilegivel("2 dias atrás") == "sem âncora"
    assert aplicabilidade.motivo_data_ilegivel("ontem à tarde") == "formato não reconhecido"


@pytest.mark.parametrize("valor,esperado", [
    ("2025-08-12T09:00:00-03:00", "2025-08-12"),
    ("2021", None),                 # só ano não serve de "hoje" da página
    ("2000-01-01", None),           # sentinela
    (None, None),
    ("ontem", None),
])
def test_referencia_de_pagina_so_com_dia(valor, esperado):
    assert aplicabilidade.referencia_de_pagina(valor) == esperado


# --- E4 revisão (item 8): marcador literal e consistência com janela_temporal ---
@pytest.mark.parametrize("texto,marcador", [
    ("Bolsonaro recebeu alta do hospital hoje", "hoje"),
    ("O ministro caiu esta manhã", "esta manha"),
    ("Nesta segunda-feira o STF decidiu", "nesta segunda-feira"),
    ("Na semana passada o dólar bateu R$ 7", "semana passada"),
    ("Anteontem houve um terremoto em Natal", "anteontem"),
    ("Agora em outubro a conta subiu", "agora em outubro"),
    ("Hoje em dia ninguém lê jornal", None),
    ("Café cura câncer", None),
])
def test_marcador_temporal_devolve_o_literal_que_define_a_janela(texto, marcador):
    assert aplicabilidade.marcador_temporal(texto) == marcador
    assert (aplicabilidade.janela_temporal(texto) is None) == (marcador is None)


def test_marcador_temporal_acompanha_janela_temporal_em_todos_os_casos():
    frases = ["Hoje em dia ninguém lê jornal", "Até hoje a obra não terminou", "O ministro caiu esta manhã",
              "Há pouco o presidente renunciou", "Anteontem houve um terremoto em Natal",
              "Nesta segunda-feira o STF decidiu", "Neste domingo houve apagão em SP",
              "Na semana passada o dólar bateu R$ 7", "Bolsonaro recebeu alta do hospital hoje",
              "A partir de agora o Pix será taxado", "Agora em setembro cai a chuva"]
    for f in frases:
        m = aplicabilidade.marcador_temporal(f)
        assert (m is None) == (aplicabilidade.janela_temporal(f) is None), f


def test_normalizar_data_total_em_entrada_aleatoria_determinista():
    """Totalidade (E4 revisão): 3000 strings aleatórias com pedaços de data (seed fixa) nunca levantam."""
    import random
    rnd = random.Random(20261009)
    pedacos = ["2026", "2021", "0001", "9999", "-", "/", ".", " ", "T", "t", ":", "10", "00", "59", "30",
               "+", "Z", "UTC", "h", "há", "ha", "dia", "dias", "ano", "anos", "mes", "semana", "um", "uma",
               "de", "jan.", "Sep", "Mar", ",", "atras", "ago", "99999999", "x", "ºº"]
    for _ in range(3000):
        valor = "".join(rnd.choice(pedacos) for _ in range(rnd.randint(1, 7)))
        ancora = rnd.choice([None, "2026-10-09", "2026-09-28 20:40:30 UTC", "lixo", "0001-01-01T00:00:00+05:00"])
        resultado = aplicabilidade.normalizar_data(valor, ancora)  # não pode levantar
        assert resultado is None or (isinstance(resultado, tuple) and resultado[1] in ("dia", "ano"))


# --- E4 revisão (M6): nada do gate lê o dia de hoje; M-9: limite do marco nas janelas efetivas ---
def test_referencia_e_obrigatoria_no_gate_e_em_data_compativel():
    """M6 (relógio latente): sem `referencia` explícita não há medida por data (o gate não usa o relógio)."""
    with pytest.raises(TypeError):
        aplicabilidade.e_aplicavel("REFUTA", True, True, "FALSO", "Café cura câncer", "2026-09-22")
    with pytest.raises(TypeError):
        aplicabilidade.data_compativel("Café cura câncer", "2026-09-22")


def test_janelas_efetivas_registram_o_limite_do_marco(monkeypatch):
    """M-9: E4_MARCO_MAX_DIAS é parâmetro efetivo da medida e vai para `parametros["e4"]["janelas"]`."""
    from factcheck_mvp import config
    assert aplicabilidade.marco_do_evento("A festa foi dia 8 de março", "2026-04-20") == ("2026-03-08", 2)  # 43 dias, limite 60
    assert "E4_MARCO_MAX_DIAS" in aplicabilidade.janelas_efetivas()
    monkeypatch.setattr(config, "E4_MARCO_MAX_DIAS", 30)
    assert aplicabilidade.janelas_efetivas()["E4_MARCO_MAX_DIAS"] == 30
    assert aplicabilidade.marco_do_evento("A festa foi dia 8 de março", "2026-04-20") is None  # 43 > 30


# --- E4 revisão (I-1, M-5): intervalo ancora no INÍCIO; "1º" (o ordinal vira "o" na normalização) ---
@pytest.mark.parametrize("texto", [
    "A operação ocorreu entre 2 e 8 de outubro",
    "A operação ocorreu entre os dias 2 e 8 de outubro",
    "A feira ocorreu dias 2 a 8 de outubro",
    "A feira ocorreu de 2 até 8 de outubro",
    "A feira ocorreu de 2 a 8 de outubro",
])
def test_intervalo_de_dias_ancora_no_inicio(texto):
    assert aplicabilidade.marco_do_evento(texto, "2026-10-09") == ("2026-10-02", 2)


def test_intervalo_entre_meses_ancora_no_inicio():
    assert aplicabilidade.marco_do_evento("A feira ocorreu entre 30 de setembro e 2 de outubro",
                                          "2026-10-09") == ("2026-09-30", 2)


@pytest.mark.parametrize("texto", ["O ato foi em 1º de março", "O ato foi no dia 1º de março"])
def test_ordinal_primeiro_conta_como_dia(texto):
    assert aplicabilidade.marco_do_evento(texto, "2026-03-10") == ("2026-03-01", 2)


# --- E4 revisão (I-2, M-4): fração ("8/10 dos casos") não é data; dd/mm e ISO sem contexto contam ---
@pytest.mark.parametrize("texto,ref", [
    ("Em 8/10 dos casos o remédio falhou", "2026-10-09"),     # sem o filtro ancoraria 2026-10-08
    ("Em 9/10 dos casos o remédio falhou", "2026-10-09"),
    ("No 1/2 tempo o time ganhou", "2026-02-05"),             # sem o filtro ancoraria 2026-02-01
    ("Em 1/2 hora o paciente melhorou", "2026-01-03"),        # sem o filtro ancoraria 2026-01-01
])
def test_fracao_com_contexto_nao_ancora_data(texto, ref):
    assert aplicabilidade.marco_do_evento(texto, ref) is None


def test_fracao_nao_desliga_o_hoje_da_afirmacao():
    t = "Hoje, em 8/10 dos casos o remédio falhou"
    assert aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-10-09") == 2
    assert aplicabilidade.marco_da_afirmacao(t, t, 1, "2026-10-09") is None


@pytest.mark.parametrize("texto", ["Hoje, 2/3 do jogo foi ruim", "Hoje, 3/4 dos eleitores votaram"])
def test_fracao_sem_contexto_nao_desliga_o_hoje(texto):
    assert aplicabilidade.janela_da_afirmacao(texto, texto, 1, "2026-10-09") == 2


def test_dd_mm_sem_contexto_e_data_explicita_no_hoje():
    """M-4: "Hoje, 08/10, ..." — 08/10 é outro dia que não a referência: o "hoje" é do ato de dizer.
    Igual à referência ("09/10") não desliga."""
    t = "Hoje, 08/10, o ministro caiu"
    assert aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-10-09") is None
    t2 = "Hoje, 09/10, o ministro caiu"
    assert aplicabilidade.janela_da_afirmacao(t2, t2, 1, "2026-10-09") == 2


def test_iso_sem_contexto_e_data_explicita_no_hoje():
    t = "Hoje, 2026-03-05, o ministro caiu"
    assert aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-10-09") is None
    t2 = "Hoje, 2026-10-09, o ministro caiu"
    assert aplicabilidade.janela_da_afirmacao(t2, t2, 1, "2026-10-09") == 2


def test_iso_completa_ancora_o_marco_como_data_com_ano():
    assert aplicabilidade.marco_do_evento("O ato foi em 2026-10-08", "2026-10-09") == ("2026-10-08", 2)


# --- E4 revisão (I-3): recorrente ("todo dia 5") e comemorativa não são data do fato ---
@pytest.mark.parametrize("texto,ref", [
    ("A taxa cai todo dia 5", "2026-09-07"),
    ("O imposto é pago todo dia 5", "2026-09-07"),
    ("O benefício é pago todo dia 10", "2026-10-09"),
    ("A taxa cai dia 5 do mês", "2026-09-07"),
    ("A taxa cai dia 5 de cada mês", "2026-09-07"),
])
def test_data_recorrente_nao_ancora_o_marco(texto, ref):
    assert aplicabilidade.marco_do_evento(texto, ref) is None


@pytest.mark.parametrize("texto,ref", [
    ("Dia Internacional da Mulher: 8 de março", "2026-03-10"),
    ("O Dia das Mães cai em 8 de maio", "2026-05-10"),
    ("Dia das Mães: 8 de maio", "2026-05-10"),
    ("A cerimônia de 8 de março, Dia Internacional da Mulher", "2026-03-10"),
    ("Dia 8 de março é o Dia da Mulher", "2026-03-10"),
])
def test_data_comemorativa_nao_ancora_o_marco(texto, ref):
    assert aplicabilidade.marco_do_evento(texto, ref) is None


def test_comemorativa_nao_desliga_o_hoje():
    t = "Hoje, Dia Internacional da Mulher, 8 de março, o ministro falou"
    assert aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-03-10") == 2


def test_recorrente_nao_desliga_o_hoje():
    t = "O benefício é pago todo dia 10 e hoje o banco falhou"
    assert aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-10-09") == 2
    assert aplicabilidade.marco_da_afirmacao(t, t, 1, "2026-10-09") is None


# --- E4 revisão (M-3): ano só desliga o "hoje" com preposição antes (em/de/desde/até) ou em data completa ---
def test_ano_solto_nao_desliga_o_hoje():
    t = "Hoje, 2020 pessoas morreram"
    assert aplicabilidade.janela_da_afirmacao(t, t, 1, "2026-10-09") == 2


@pytest.mark.parametrize("texto", ["Hoje, em 2020 pessoas morreram", "Hoje, desde 2020, pessoas morreram"])
def test_ano_com_preposicao_diferente_da_referencia_desliga(texto):
    assert aplicabilidade.janela_da_afirmacao(texto, texto, 1, "2026-10-09") is None


# --- E4 revisão (I-5): travessão/meia-risca colados não colam palavras ("ontem—o" são duas) ---
@pytest.mark.parametrize("texto,janela", [
    ("Ontem—o ministro caiu", 3),
    ("Ontem–o ministro caiu", 3),
    ("O ministro caiu hoje—segundo fontes", 2),
    ("Hoje…o time ganhou", 2),
])
def test_travessao_colado_separa_palavras(texto, janela):
    assert aplicabilidade.janela_temporal(texto) == janela


def test_marcador_com_travessao_colado_devolve_so_a_palavra():
    assert aplicabilidade.marcador_temporal("Ontem—o ministro caiu") == "ontem"


# --- E4 revisão (M-2): "segunda dose", "há pouco mais que", "de hoje em diante" não são momento ---
@pytest.mark.parametrize("texto", [
    "Nesta segunda dose da vacina foi aplicada",
    "Esta quarta dose da vacina chegou",
    "Nessa segunda chance o time virou",
    "Há pouco mais que dez anos o país mudou",
    "De hoje em diante o Pix será taxado",
])
def test_falsos_marcadores_m2_nao_contam(texto):
    assert aplicabilidade.janela_temporal(texto) is None
