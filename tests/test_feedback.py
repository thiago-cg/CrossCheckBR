"""Feedback 👍/👎 do bot: grava JSONL sem identificar o usuário."""
import json

from factcheck_mvp import config


def test_feedback_grava_jsonl_sem_id(tmp_path, monkeypatch):
    from factcheck_mvp import telegram_bot as tb
    arq = tmp_path / "fb.jsonl"
    monkeypatch.setattr(config, "FEEDBACK_PATH", str(arq))
    assert tb.registrar_feedback({"tipo": "titulo", "conteudo": "x", "propensao": "alta"}, "util")
    linha = json.loads(arq.read_text(encoding="utf-8").strip())
    assert linha["voto"] == "util" and linha["propensao"] == "alta"
    assert not any(k in linha for k in ("user", "user_id", "chat_id"))


def test_teclado_feedback_cabe_no_limite_do_telegram():
    from factcheck_mvp.telegram_bot import _teclado_feedback
    kb = _teclado_feedback("-1001234567890-9999999")
    for b in kb.inline_keyboard[0]:
        assert len(b.callback_data.encode()) <= 64
