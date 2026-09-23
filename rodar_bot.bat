@echo off
REM Sobe o bot do Telegram (fn_tic_bot) em primeiro plano, no .venv do projeto.
REM Feche esta janela para parar o bot. Token fica no .env (fora do git).
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe -m factcheck_mvp.telegram_bot
pause
