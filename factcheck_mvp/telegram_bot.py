"""RF01: bot do Telegram. Entrada texto/título/link (RF02/RF03), progresso e
resposta neutra com fontes, % do modelo e etapas (RF07/RF09/RF11/RNF02/RNF04).

Privacidade: o texto enviado é consultado em bases externas (SerpAPI/Google).
Sem fotos/vídeos: mídia recebe orientação, não silêncio (RF03).
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import config
from .pipeline import Pipeline
from .schemas import EntradaConsulta, RelatorioChecagem

log = logging.getLogger("factcheck.telegram")

AJUDA = (
    "Envie para checar (só texto — não aceito fotos nem vídeos):\n"
    "• o texto da notícia, ou\n"
    "• o título, ou\n"
    "• o link (se for de um dos 20 portais monitorados, leio direto; senão, envie o texto).\n\n"
    "Devolvo propensão (baixa/média/alta), % do modelo, evidências com links e o passo a passo. "
    "Nunca digo que algo 'é falso' ou 'é verdade' — ajudo você a avaliar.\n\n"
    "Privacidade: o texto é comparado com bases públicas de checagem e notícias "
    "(inclui consulta web externa). Não envie dados pessoais."
)

MIDIA_MSG = (
    "Ainda não analiso fotos, vídeos nem áudios (RF03). "
    "Envie o texto, o título ou o link da notícia que eu checo para você."
)


def classificar_entrada(texto: str) -> EntradaConsulta:
    t = (texto or "").strip()
    if t.startswith(("http://", "https://", "www.")):
        return EntradaConsulta(tipo="link", conteudo=t[:2000])
    if len(t) <= 140 and "\n" not in t:
        return EntradaConsulta(tipo="titulo", conteudo=t)
    return EntradaConsulta(tipo="texto", conteudo=t[:20000])


def _texto_link(url: str, catalogo, timeout: int = 15) -> str | None:
    """Baixa link SOMENTE de domínio do catálogo (allow-list anti-SSRF).

    Teto de 1,5 MB, timeout curto, e a URL final (após redirects) precisa
    continuar no catálogo. Fora disso: None (pipeline registra limitação).
    """
    import httpx

    try:
        from .catalogo import _dominio as _dom
    except Exception:  # pragma: no cover
        return None
    if not catalogo.por_dominio(url):
        return None
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as cli:
            r = cli.get(url, headers={"User-Agent": "factcheck-mvp/0.1"})
            final = str(r.url)
            if not catalogo.por_dominio(final):
                return None
            html = r.text[:1_500_000]
        titulo = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)[:25]
        limpo = re.sub(r"<[^>]+>", " ", " ".join(paras))
        limpo = re.sub(r"\s+", " ", limpo).strip()[:8000]
        cabeca = (titulo.group(1).strip()[:200] + "\n\n") if titulo else ""
        return (cabeca + limpo) or None
    except Exception:
        return None


def formatar(rel: RelatorioChecagem) -> str:
    linhas = [f"Propensão: {rel.propensao.upper()}", "", rel.justificativa, ""]
    for s in rel.sinais:  # RF09: % do modelo e confianças sempre visíveis
        if s.motor == "modelo-fake":
            linhas.append(f"Modelo de detecção: {s.valor} de propensão ({s.rotulo}).")
        elif s.confianca is not None:
            linhas.append(f"{s.rotulo}: {s.valor} (confiança {s.confianca:.0%}).")
    if rel.sinais:
        linhas.append("")
    if rel.fontes:
        linhas.append("Fontes consultadas:")
        for f in rel.fontes[:8]:
            rot = f" [selo do portal: {f.veredito}]" if f.veredito else ""
            linhas.append(f"• {f.portal_nome or 'web'}{rot}: {f.titulo[:90]}")
            if f.url:
                linhas.append(f"  {f.url}")
        linhas.append("")
    if rel.etapas:  # RF11 no canal principal: resumo nome:status
        linhas.append("Passo a passo: " + "; ".join(f"{e.nome}:{e.status}" for e in rel.etapas) + ".")
        linhas.append("")
    if rel.limitacoes:
        for i, lim in enumerate(rel.limitacoes[:3], 1):
            linhas.append(f"Limitação {i}: {lim}")
        linhas.append("")
    linhas.append("Para avaliar você mesmo:")
    linhas += [f"• {p}" for p in rel.perguntas_guia[:3]]
    texto = "\n".join(linhas)
    return texto[:3900].rsplit("\n", 1)[0]  # corta em quebra de linha, nunca no meio da URL


async def _start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(AJUDA)


async def _midia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(MIDIA_MSG)


async def _checar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not (update.message.text or "").strip():
        return
    pipe: Pipeline = context.application.bot_data["pipeline"]
    try:
        entrada = classificar_entrada(update.message.text or "")
    except Exception:
        await update.message.reply_text("Mensagem muito curta. " + AJUDA)
        return
    aviso = await update.message.reply_text("Recebi. Começando a checagem…")
    editados = 0

    async def progresso(msg: str) -> None:
        nonlocal editados
        editados += 1
        if editados > 3:  # evita flood/429 no Telegram
            return
        try:
            await aviso.edit_text(msg[:400])
        except Exception:
            pass

    try:
        texto_extra = ""
        if entrada.tipo == "link":
            texto_extra = _texto_link(entrada.conteudo, pipe.catalogo) or ""
            if texto_extra:
                entrada = EntradaConsulta(tipo="texto", conteudo=f"{entrada.conteudo}\n\n{texto_extra}"[:20000])
        rel = await pipe.executar(entrada, progresso=progresso)
        if entrada.tipo == "link" and not texto_extra:
            rel.limitacoes.append("Link fora dos portais monitorados ou inacessível: analisei o endereço como referência; envie o texto para checagem completa.")
        await aviso.edit_text(formatar(rel))
    except Exception:
        uid = update.effective_user.id if update.effective_user else "?"
        log.exception("falha na checagem (user=%s)", uid)  # detalhe só no servidor
        try:
            await aviso.edit_text("Não consegui concluir. Tente de novo em instantes.")
        except Exception:
            pass


def main() -> None:
    if not config.TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN vazio (veja .env.mvp.example).")
    from .api import pipeline as fabrica

    app = Application.builder().token(config.TELEGRAM_TOKEN).build()
    app.bot_data["pipeline"] = fabrica()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.ATTACHMENT, _midia))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _checar))
    log.info("bot no ar. /start para ajuda.")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
