# MVP — Chatbot de checagem cruzada (Telegram + API)

## Rodar
```bash
pip install -r requirements-mvp.txt
cp .env.mvp.example .env   # preencha TELEGRAM_TOKEN (SERPAPI_KEY opcional)
python -m pytest tests/ -q
uvicorn factcheck_mvp.api:app --port 8000 &
python -m factcheck_mvp.telegram_bot
```

## Testar a API sem Telegram
```bash
curl -X POST localhost:8000/checar -H 'Content-Type: application/json' \
  -d '{"tipo":"titulo","conteudo":"Bets são proibidas no Brasil"}'
```

## Decisões-guia
- **laya primeiro**: tipo/editoria/veredito e implicação (confirma/refuta) via laya; LLM local só onde há geração (afirmações, padrões) — sempre com fallback determinístico.
- **Sem denylist**: nenhum domínio bloqueado; filtro é piso de relevância por consulta.
- **Nunca binário**: propensão baixa/média/alta/indeterminada + recibo de etapas + fontes com links.
- **Mock explícito**: sem `FAKE_MODEL_PATH`, o detector é placeholder marcado `mock: true` e listado nas limitações.

## Privacidade
O texto enviado é comparado com bases públicas (índice local + SerpAPI/Google quando
`SERPAPI_KEY` configurada). Não envie dados pessoais; o bot avisa isso no `/start`.
Segredos só via ambiente (nunca em arquivos) — veja `.gitignore`.
