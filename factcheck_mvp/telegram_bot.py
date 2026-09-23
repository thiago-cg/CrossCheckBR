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


def _ler_lock(arq) -> int | None:
    """Lê PID dono com retries (fecha a race create-vs-write: escritor
    concorrente completa em ms; lock realmente stale nunca completa)."""
    import json
    import time

    for _ in range(10):
        try:
            pid = json.loads(arq.read_text(encoding="utf-8")).get("pid")
            return pid if isinstance(pid, int) else None
        except (OSError, ValueError):
            time.sleep(0.2)
    return None


def _instancia_unica(lock: str | None = None) -> bool:
    """Garante 1 poller: lockfile com PID vivo. True = esta é a dona.

    Duas instâncias brigam pelo getUpdates (Conflict) e se derrubam;
    a segunda sai sozinha. Lock stale (crash) é assumido via PID morto.
    """
    import json
    import os
    from pathlib import Path as _P

    arq = _P(lock) if lock else _P(__file__).resolve().parent.parent / "bot.lock"
    eu = os.getpid()
    try:
        with open(arq, "x") as f:
            f.write(json.dumps({"pid": eu}))
        return True
    except FileExistsError:
        pass
    outro = _ler_lock(arq)
    if outro is not None and outro != eu:
        try:
            os.kill(outro, 0)  # existe?
            log.warning("outra instância viva (pid=%s): saindo", outro)
            return False
        except (OSError, ProcessLookupError):
            pass  # stale: assume
    try:
        arq.write_text(json.dumps({"pid": eu}), encoding="utf-8")
        return True
    except OSError:
        return False


def _vigia_instancia_unica(intervalo_s: float = 20.0) -> None:
    """Watchdog: se o lock mudar de dono, sai (converge mesmo com race no
    arranque duplo — o perdedor desliga em até `intervalo_s`)."""
    import os
    import threading
    import time
    from pathlib import Path as _P

    eu = os.getpid()
    arq = _P(__file__).resolve().parent.parent / "bot.lock"

    def _vigiar():
        while True:
            time.sleep(intervalo_s)
            try:
                dono = _ler_lock(arq)
            except Exception:
                continue
            if dono is not None and dono != eu:
                log.warning("lock perdeu a dona (pid=%s, eu=%s): desligando", dono, eu)
                os._exit(3)

    t = threading.Thread(target=_vigiar, daemon=True)
    t.start()

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
    import re as _re
    t = (texto or "").strip()
    if t.startswith(("http://", "https://", "www.")):
        return EntradaConsulta(tipo="link", conteudo=t[:2000])
    # Rumor vago de 2ª mão: preserva como texto + flag fica no pipeline via regex;
    # mantém Literal compatível (texto/titulo/link) p/ não quebrar API/bot antigos.
    if len(t) <= 140 and "\n" not in t:
        return EntradaConsulta(tipo="titulo", conteudo=t)
    return EntradaConsulta(tipo="texto", conteudo=t[:20000])


RUMOR_MSG = ("Entendi — relato sem fonte nem certeza. Vou checar o núcleo factual "
             "como rumor de segunda-mão (se souber onde/quando ouviu, me diga).")


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
    emoji = {"baixa": "🟢", "media": "🟡", "alta": "🔴", "indeterminada": "⚪"}.get(rel.propensao, "⚪")
    header = getattr(rel, "header", "") or f"{emoji} Propensão {rel.propensao.upper()}"
    why = getattr(rel, "why_1linha", "")
    linhas = [header, "", why or rel.justificativa, ""]
    # Filtro único de homepages/seções (fonte: pipeline._eh_generica, sem drift)
    try:
        from .pipeline import _eh_generica as _gen
    except Exception:
        _gen = None

    def _generica(u: str, t: str = "", trecho: str = "") -> bool:
        if _gen is not None:
            try:
                return bool(_gen(u, t, trecho))
            except Exception:
                pass
        return False
    uteis = [f for f in (rel.fontes or [])
             if not _generica(f.url, f.titulo, getattr(f, "trecho_corpo", None) or "")
             and getattr(f, "relevante", None) is not False]
    if rel.fontes and not uteis:
        linhas.append("Sem evidência relevante encontrada — não compartilhe como verdade/falso.")
        linhas.append("")
    # Top 2-3 lado a lado: portal | veredito | corpo_lido | link + quote
    if uteis:
        linhas.append("Fontes lado a lado (compare):")
        for i, f in enumerate(uteis[:3], 1):
            selo = f" [selo: {f.veredito}]" if f.veredito else ""
            corpo = "📄 corpo lido" if getattr(f, "corpo_lido", False) else "📰 só título"
            term = f" 🌡{f.score_juiz:+d}" if getattr(f, "score_juiz", None) is not None else ""
            linhas.append(f"{i}. {f.portal_nome or 'web'}{selo} ({corpo}{term}): {f.titulo[:90]}")
            if getattr(f, "quote", None):
                linhas.append(f"   “{f.quote[:140]}”")
            if f.url:
                linhas.append(f"   {f.url}")
        if len(uteis) > 3:
            linhas.append(f"+{len(uteis)-3} fonte(s) no relatório completo.")
        linhas.append("")
    # RF09: % do modelo e confianças visíveis — resumo em 1 linha (check #1,
    # compacto); detalhes completos no JSON da API (/checar).
    # Placeholder sem cara de medição (round D): número mock não é exibido.
    for s in rel.sinais:
        if s.motor == "modelo-fake":
            if "PLACEHOLDER" in (s.rotulo or ""):
                linhas.append("Modelo de detecção: em treino (placeholder, sem número confiável).")
            else:
                linhas.append(f"Modelo de detecção: {s.valor} ({s.rotulo}).")
            break
    outros = [f"{s.motor} {s.confianca:.2f}" for s in rel.sinais if s.motor != "modelo-fake"]
    if outros:
        linhas.append("Sinais (confiança): " + "; ".join(outros[:6]) + ".")
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
        # Aviso de rumor de 2ª mão (usa a mesma regex do pipeline, sem drift)
        try:
            from .pipeline import RUMOR_RE as _RR
            if _RR.search(entrada.conteudo or ""):
                await aviso.edit_text(RUMOR_MSG[:400])
        except Exception:
            pass
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
    if not _instancia_unica():
        raise SystemExit("já há outra instância do bot rodando (bot.lock).")
    _vigia_instancia_unica()
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
