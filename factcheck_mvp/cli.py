"""CLI p/ rodar e inspecionar o pipeline (pensada p/ o Claude Code iterar).

  python3 -m factcheck_mvp.cli checar "Café cura câncer" [--tipo titulo] [--json] [--modo replay]
  python3 -m factcheck_mvp.cli trace last                    # resumo compacto do run
  python3 -m factcheck_mvp.cli trace <run_id> --eventos llm,fallback   # eventos crus
  python3 -m factcheck_mvp.cli runs [--n 10]                 # runs recentes

.env é carregado por `config` (python-dotenv) se existir; nada é exigido.
Telemetria: runs/<run_id>/{trace.jsonl,resultado.json} (TELEMETRIA_DIR).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from . import config, replay, telemetria  # config carrega .env
from .schemas import EntradaConsulta

log = logging.getLogger("factcheck.cli")
DATA = Path(__file__).resolve().parent / "data"


def classificar(texto: str, tipo: Optional[str] = None) -> EntradaConsulta:
    """Mesma regra do fallback da API: link | título (<=140, 1 linha) | texto."""
    t = (texto or "").strip()
    if tipo:
        return EntradaConsulta(tipo=tipo, conteudo=t[:20000])
    if t.startswith(("http://", "https://", "www.")):
        return EntradaConsulta(tipo="link", conteudo=t[:2000])
    if len(t) <= 140 and "\n" not in t:
        return EntradaConsulta(tipo="titulo", conteudo=t)
    return EntradaConsulta(tipo="texto", conteudo=t[:20000])


def construir_pipeline(serpapi=None, detector=None):
    """Pipeline igual ao da API: catálogo + índice de checagens (data/checagens.jsonl),
    que só é consultado com INDICE_CHECAGENS=1 (atalho opcional; núcleo = busca aberta)."""
    from .catalogo import Catalogo
    from .indice import Indice
    from .pipeline import Pipeline
    from .serpapi_layer import SerpAPIClient

    return Pipeline(Catalogo.carregar(), Indice.de_checagens(), Indice(), serpapi or SerpAPIClient(), detector)


class catalogo_isolado:
    """Aponta catalogo.json/catalogo_propostas.json p/ cópias temporárias.

    O pipeline atual grava sites descobertos direto no catalogo.json (achado
    A5 do review): em execuções de desenvolvimento/eval isso sujaria o
    repositório e mudaria o resultado das execuções seguintes.
    """

    def __enter__(self):
        import shutil
        import tempfile
        from . import catalogo as _cat, descoberta_site as _ds
        self._tmp = tempfile.TemporaryDirectory(prefix="catalogo-isolado-")
        base = Path(self._tmp.name)
        self._antes = (_cat.DEFAULT_CATALOGO, _ds.DEFAULT_PROPOSTAS)
        novo_cat, novo_prop = base / "catalogo.json", base / "catalogo_propostas.json"
        shutil.copy(_cat.DEFAULT_CATALOGO, novo_cat)
        if Path(_ds.DEFAULT_PROPOSTAS).exists():
            shutil.copy(_ds.DEFAULT_PROPOSTAS, novo_prop)
        _cat.DEFAULT_CATALOGO, _ds.DEFAULT_PROPOSTAS = novo_cat, novo_prop
        return base

    def __exit__(self, *a):
        from . import catalogo as _cat, descoberta_site as _ds
        _cat.DEFAULT_CATALOGO, _ds.DEFAULT_PROPOSTAS = self._antes
        self._tmp.cleanup()
        return False


def _silenciar_logs(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    for nome in ("httpx", "httpcore"):  # httpx loga URLs com chave em INFO
        logging.getLogger(nome).setLevel(logging.WARNING)


def cmd_checar(a) -> int:
    entrada = classificar(a.texto, a.tipo)
    if a.modo:
        replay.definir_modo(a.modo)
    if a.catalogo_real:
        pipe = construir_pipeline()
        rel = asyncio.run(pipe.executar(entrada, usar_llm=not a.sem_llm))
    else:
        with catalogo_isolado():
            pipe = construir_pipeline()
            rel = asyncio.run(pipe.executar(entrada, usar_llm=not a.sem_llm))
    rid = telemetria.ultimo_run_id()
    if a.json:
        print(json.dumps({"run_id": rid, "relatorio": rel.model_dump(mode="json")},
                         ensure_ascii=False, indent=1))
        return 0
    uso = replay.uso_serpapi()
    print(f"nivel: {rel.propensao}")
    print(f"why: {rel.why_1linha}")
    print(f"justificativa: {telemetria.truncar(rel.justificativa, 400)}")
    if rid:
        print(f"run_id: {rid}")
        print(f"trace: {telemetria.diretorio() / rid / 'trace.jsonl'}")
        print(f"(resumo: python3 -m factcheck_mvp.cli trace {rid})")
    else:
        print("run_id: (telemetria desligada: TELEMETRIA=0)")
    teto = f"saldo {uso['restantes']}/{uso['orcamento']}" if uso["orcamento"] is not None else "sem teto"
    print(f"serpapi: {uso['usadas_processo']} busca(s) live nesta execução; {uso['usadas']} no total "
          f"({teto}; {uso['arquivo']})")
    return 0


def cmd_trace(a) -> int:
    try:
        if a.eventos:
            tipos: List[str] = [t.strip() for t in a.eventos.split(",") if t.strip()]
            for e in telemetria.eventos_de(a.run_id, tipos):
                print(json.dumps(e, ensure_ascii=False))
        else:
            print(telemetria.resumir_run(a.run_id))
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


def cmd_runs(a) -> int:
    runs = telemetria.listar_runs(a.n)
    if not runs:
        print(f"(nenhum run em {telemetria.diretorio()})")
        return 0
    print(f"{'run_id':<24} {'data':<20} {'nivel':<13} {'fb':>3} {'dur':>7}  entrada")
    for r in runs:
        dur = telemetria._fmt_ms(r.get("dur_ms"))
        ent = telemetria.truncar(" ".join(str(r.get("entrada") or "").split()), 60)
        caso = f"[{r['caso']}] " if r.get("caso") else ""
        print(f"{r['run_id']:<24} {str(r.get('data'))[:19]:<20} {str(r.get('nivel')):<13} "
              f"{r.get('n_fallbacks', 0):>3} {dur:>7}  {caso}{ent}{'' if r.get('completo') else ' (incompleto)'}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m factcheck_mvp.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="logs INFO no stderr")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("checar", help="roda o pipeline numa entrada")
    c.add_argument("texto")
    c.add_argument("--tipo", choices=["texto", "titulo", "link"])
    c.add_argument("--json", action="store_true", help="relatório completo em JSON")
    c.add_argument("--modo", choices=list(replay.MODOS), help="IO_MODO p/ esta execução")
    c.add_argument("--sem-llm", action="store_true", help="usar_llm=False (só determinístico)")
    c.add_argument("--catalogo-real", action="store_true",
                   help="deixa o pipeline gravar no catalogo.json real (padrão: cópia temporária)")
    c.set_defaults(fn=cmd_checar)

    t = sub.add_parser("trace", help="resumo (ou eventos crus) de um run")
    t.add_argument("run_id", nargs="?", default="last")
    t.add_argument("--eventos", help="tipos separados por vírgula (ex. llm,fallback)")
    t.set_defaults(fn=cmd_trace)

    r = sub.add_parser("runs", help="lista runs recentes")
    r.add_argument("--n", type=int, default=10)
    r.set_defaults(fn=cmd_runs)

    a = ap.parse_args(argv)
    _silenciar_logs(a.verbose)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
