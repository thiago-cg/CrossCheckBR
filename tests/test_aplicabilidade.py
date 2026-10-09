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
