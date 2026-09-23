"""Instância única — arquivo isolado (diagnóstico de sandbox)."""
import json
import os


def _kill_fake(vivos: set[int]):
    def kill(pid: int, sig: int) -> None:
        if pid in vivos:
            return None
        raise OSError(87, "invalid")
    return kill


def test_instancia_unica_iso(tmp_path, monkeypatch):
    # sandbox manda SIGINT após os.kill(pai)+os.kill(morto) reais; simula vivos.
    from factcheck_mvp.telegram_bot import _instancia_unica
    vivo_pai = os.getppid()
    monkeypatch.setattr(os, "kill", _kill_fake({vivo_pai}))
    arq = str(tmp_path / "bot.lock")
    assert _instancia_unica(arq) is True
    open(arq, "w").write(json.dumps({"pid": vivo_pai}))
    assert _instancia_unica(arq) is False
    open(arq, "w").write(json.dumps({"pid": 999999999}))
    assert _instancia_unica(arq) is True
