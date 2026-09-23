@echo off
REM CrossCheckBR bot via Agendador de Tarefas (sem pause: roda desanexado).
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe -m factcheck_mvp.telegram_bot >> bot_out.log 2>> bot_err.log
