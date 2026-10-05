"""Confiabilidade da fonte: catálogo curado + natureza do site + tráfego real (Tranco).

Tráfego sozinho não mede confiabilidade: Instagram e YouTube estão no topo do
mundo e o conteúdo é de usuário; Lupa e Aos Fatos têm pouco tráfego global e são
as fontes mais confiáveis que temos. Por isso a ordem de precedência é:

  1. curada          portal curado do catálogo (veículo/agência revisado pela equipe)
  2. plataforma      rede social, vídeo, fórum, hospedagem de blog/documento (UGC)
  3. institucional   órgão público, Judiciário, Legislativo, ensino (.gov.br, .jus.br …)
  4. alto_trafego    entre os TRAFEGO_RANK_MAX domínios mais acessados (Tranco)
  5. baixo_trafego   o resto (fora do ranking ou abaixo do corte)

Quem usa: `decisao` (peso do voto: plataforma e baixo_trafego pesam menos; os
demais, como antes) e `pipeline._selecionar` (fontes confiáveis vão primeiro ao
juiz). Nada é descartado por confiabilidade: a busca, a leitura e o juiz seguem
iguais. Dados: data/trafego_tranco.csv.gz (scripts/atualizar_trafego.py).
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from . import config
from .catalogo import dominio_registravel

log = logging.getLogger("factcheck.confiabilidade")

CURADA = "curada"
PLATAFORMA = "plataforma"
INSTITUCIONAL = "institucional"
ALTO_TRAFEGO = "alto_trafego"
BAIXO_TRAFEGO = "baixo_trafego"
NIVEIS = (CURADA, PLATAFORMA, INSTITUCIONAL, ALTO_TRAFEGO, BAIXO_TRAFEGO)
# Bastam para cravar alta/baixa na decisão (os outros dois só votam junto com estes).
CONFIAVEIS = frozenset({CURADA, INSTITUCIONAL, ALTO_TRAFEGO})

# Conteúdo publicado por usuários: muito acessado, mas sem responsabilidade editorial.
PLATAFORMAS = frozenset("""
facebook.com instagram.com threads.com threads.net x.com twitter.com tiktok.com kwai.com
youtube.com youtu.be vimeo.com dailymotion.com twitch.tv rumble.com
whatsapp.com telegram.org t.me linkedin.com pinterest.com reddit.com quora.com tumblr.com
discord.com snapchat.com bsky.app gettr.com truthsocial.com
blogspot.com blogger.com wordpress.com medium.com substack.com wixsite.com webnode.com
scribd.com slideshare.net academia.edu researchgate.net issuu.com
webnovel.com wattpad.com change.org avaaz.org
translate.google.com docs.google.com drive.google.com sites.google.com
""".split())

# Domínio-base institucional (sufixo): governo, Justiça, Legislativo, MP, ensino.
_SUFIXOS_INSTITUCIONAIS = (".gov.br", ".jus.br", ".leg.br", ".mp.br", ".def.br", ".mil.br",
                           ".edu.br", ".gov", ".edu", ".int")
_INSTITUCIONAIS = frozenset({"gov.br", "jus.br", "leg.br", "mp.br", "scielo.br", "scielo.org",
                             "fiocruz.br", "who.int", "paho.org", "un.org", "ibge.gov.br"})

# Peso relativo do voto por nível (o juiz e a busca não mudam).
# curada/institucional/alto_trafego: exatamente o que já valia antes (1,0 / 0,6 / 0,6).
# Plataforma 0,45 (inclui perfis oficiais de veículos e agências) e site pouco acessado 0,3:
# no eval de decisão (eval/snapshots/a3-dev.jsonl, 69 casos), junto com a trava de
# CONFIAVEIS em decisao.py: erros graves 3 -> 1, acerto+parcial 0,536 -> 0,565 e
# acerto exato 0,275 -> 0,246 (altas sustentadas só por posts viram média); varredura no PR.
FATOR_POSTURA = {CURADA: 1.0, INSTITUCIONAL: 0.6, ALTO_TRAFEGO: 0.6,
                 PLATAFORMA: 0.45, BAIXO_TRAFEGO: 0.3}
FATOR_VEREDITO = {CURADA: 1.0, INSTITUCIONAL: 0.8, ALTO_TRAFEGO: 0.8,
                  PLATAFORMA: 0.55, BAIXO_TRAFEGO: 0.4}
# Bônus na fila do juiz (somado ao overlap/selo/curada de _selecionar).
BONUS_SELECAO = {CURADA: 0.0, INSTITUCIONAL: 0.1, ALTO_TRAFEGO: 0.1,
                 PLATAFORMA: -0.15, BAIXO_TRAFEGO: -0.1}

ROTULO = {CURADA: "veículo verificado pela equipe",
          INSTITUCIONAL: "órgão público ou instituição",
          ALTO_TRAFEGO: "site muito acessado",
          PLATAFORMA: "rede social ou plataforma de usuários",
          BAIXO_TRAFEGO: "site pouco acessado"}

_ARQ = Path(__file__).resolve().parent / "data" / "trafego_tranco.csv.gz"
_ranks: Optional[Dict[str, int]] = None
_trava = threading.Lock()


def _carregar() -> Dict[str, int]:
    global _ranks
    with _trava:
        if _ranks is None:
            ranks: Dict[str, int] = {}
            try:
                with gzip.open(_ARQ, "rt", encoding="utf-8") as f:
                    for linha in f:
                        d, _, p = linha.strip().partition(",")
                        if d and p.isdigit():
                            ranks[d] = int(p)
            except OSError as e:  # sem o arquivo: ninguém vira alto_trafego (fail-safe)
                log.warning("tráfego indisponível (%s): sites fora do catálogo contam como pouco acessados", e)
            _ranks = ranks
        return _ranks


def posicao(url_ou_dominio: str) -> Optional[int]:
    """Posição Tranco do domínio-base (None = fora do recorte guardado)."""
    return _carregar().get(dominio_registravel(url_ou_dominio or ""))


def _host(url_ou_dominio: str) -> str:
    from urllib.parse import urlparse
    s = (url_ou_dominio or "").strip().lower()
    host = (urlparse(s).hostname or "") if "://" in s else s.split("/")[0]
    return host[4:] if host.startswith("www.") else host


def classificar(url: str, curada: bool = False) -> str:
    """Nível de confiabilidade da fonte (ver docstring do módulo)."""
    if curada:
        return CURADA
    host = _host(url)
    base = dominio_registravel(host)
    if host in PLATAFORMAS or base in PLATAFORMAS:
        return PLATAFORMA
    if base in _INSTITUCIONAIS or host in _INSTITUCIONAIS or host.endswith(_SUFIXOS_INSTITUCIONAIS):
        return INSTITUCIONAL
    pos = posicao(base)
    if pos is not None and pos <= (config.TRAFEGO_RANK_MAX or 200_000):
        return ALTO_TRAFEGO
    return BAIXO_TRAFEGO


def metadados() -> Dict[str, object]:
    """Id da lista Tranco em uso (para telemetria e para citar em comparações)."""
    try:
        return json.loads((_ARQ.parent / "trafego_tranco.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
