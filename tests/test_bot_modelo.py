"""Round D: placeholder do modelo sem cara de medição + instância única.

Imports dentro das funções: collection leve (torch via telegram_bot só no
teste — o killer de idle do ambiente derruba collection silenciosa longa).
"""
from factcheck_mvp.schemas import (
    EntradaConsulta, RelatorioChecagem, SinalAnalise,
)


def _rel_com_modelo(valor, rotulo):
    return RelatorioChecagem(
        propensao="indeterminada", justificativa="j",
        consulta=EntradaConsulta(tipo="texto", conteudo="algum texto aqui"),
        sinais=[SinalAnalise(motor="modelo-fake", rotulo=rotulo,
                             valor=valor, confianca=0.4)])


def test_placeholder_sem_numero():
    from factcheck_mvp.telegram_bot import formatar
    texto = formatar(_rel_com_modelo("0.5", "modelo mock-heuristico-v0 (PLACEHOLDER)"))
    assert "em treino" in texto
    assert "0.5 (modelo" not in texto


def test_modelo_real_mostra_numero():
    from factcheck_mvp.telegram_bot import formatar
    texto = formatar(_rel_com_modelo("0.83", "modelo distilbert-fake-v1"))
    assert "0.83" in texto


def test_instancia_unica(tmp_path, monkeypatch):
    import json
    import os
    from factcheck_mvp.telegram_bot import _instancia_unica

    def _kill(pid: int, sig: int) -> None:
        if pid == os.getppid():
            return None
        raise OSError(87, "invalid")

    monkeypatch.setattr(os, "kill", _kill)
    arq = str(tmp_path / "bot.lock")
    assert _instancia_unica(arq) is True  # primeira dona
    # outra instância viva (PID do pai): segunda sai
    open(arq, "w").write(json.dumps({"pid": os.getppid()}))
    assert _instancia_unica(arq) is False
    # lock stale (PID morto): assume
    open(arq, "w").write(json.dumps({"pid": 999999999}))
    assert _instancia_unica(arq) is True
