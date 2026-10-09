"""Simula um usuário enviando N frases rotuladas ao CrossCheckBR e gera um relatório.

Usa o MESMO endpoint do bot e da web (`POST /checar`) de uma API em execução:
    .venv/bin/uvicorn factcheck_mvp.api:app --port 8000      # em outro terminal
    python3 scripts/simular_usuario.py --n 50 --seed 42        # mostra a amostra e a estimativa
    python3 scripts/simular_usuario.py --n 50 --seed 42 --confirmar --max-buscas 90

Tem custo (SerpAPI + OpenRouter): sem --confirmar só sorteia, estima e para.
Não faz parte da suíte de testes (as funções puras estão testadas em tests/test_simulacao.py).
Saída: reports/simulacao_AAAA-MM-DD_HHMM/{relatorio.html,dados.json}
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from eval import simulacao as sim  # noqa: E402


def _cota_serpapi_conta() -> int | None:
    """Buscas restantes na conta da SerpAPI (consultar a conta não gasta busca). Nunca imprime a chave."""
    try:
        import httpx
        chave = next((l.split("=", 1)[1].strip() for l in (RAIZ / ".env").read_text(encoding="utf-8").splitlines()
                      if l.startswith("SERPAPI_KEY=")), "")
        if not chave:
            return None
        d = httpx.get("https://serpapi.com/account.json", params={"api_key": chave}, timeout=15).json()
        return d.get("total_searches_left")
    except Exception:
        return None


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ, capture_output=True,
                              text=True).stdout.strip()
    except Exception:
        return "?"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="API do CrossCheckBR em execução")
    ap.add_argument("--concorrencia", type=int, default=1, help="checagens simultâneas (padrão 1: em sequência)")
    ap.add_argument("--max-buscas", type=int, default=None,
                    help="interrompe ao atingir N buscas na SerpAPI (casos restantes: não executados)")
    ap.add_argument("--confirmar", action="store_true", help="sem isto, só sorteia, estima e para")
    ap.add_argument("--frases", type=Path, default=sim.ARQ_FRASES)
    ap.add_argument("--mostrar", action="store_true", help="lista as frases sorteadas por inteiro antes de rodar")
    ap.add_argument("--saida", type=Path, default=RAIZ / "reports")
    a = ap.parse_args(argv)

    import httpx

    frases = sim.carregar_frases(a.frases)
    amostra, faltas = sim.sortear(frases, a.n, a.seed)
    est = sim.estimar_buscas(amostra)
    if a.mostrar:
        print("\nFrases sorteadas (texto completo, como serão enviadas):")
        for i, f in enumerate(amostra, 1):
            print(f"\n[{i:>2}] {sim.NOMES_CATEGORIA[f['categoria']]} · esperado: {f['rotulo']} · {f['origem']}\n     {f['texto']}")
        print()
    print(f"Amostra: {len(amostra)} frases (seed {a.seed})")
    for cat, k in sim.cotas(a.n).items():
        n_cat = sum(1 for f in amostra if f["categoria"] == cat)
        print(f"  {sim.NOMES_CATEGORIA[cat]:42} {n_cat}/{k}" + (f"  (faltaram {faltas[cat]})" if cat in faltas else ""))
    try:
        saude = httpx.get(f"{a.url}/saude", timeout=15).json()
    except Exception as e:
        print(f"\nAPI indisponível em {a.url} ({type(e).__name__}). Suba com:\n"
              "  .venv/bin/uvicorn factcheck_mvp.api:app --port 8000")
        return 2
    cota = _cota_serpapi_conta()
    print(f"\nEstimativa de chamadas à SerpAPI: ~{est['tipico']} (máximo {est['maximo']})")
    print(f"  cota restante na conta: {cota if cota is not None else '?'} · "
          f"teto diário do projeto: {saude.get('serpapi_uso_hoje')}/{saude.get('serpapi_cap')} usados hoje")
    if saude.get("serpapi_cap") and est["tipico"] > saude["serpapi_cap"] - (saude.get("serpapi_uso_hoje") or 0):
        print("  ATENÇÃO: a estimativa passa do teto diário do projeto (SERPAPI_DAILY_CAP): depois dele a\n"
              "  busca na web é pulada e os resultados mudam. Suba o teto no .env ou use --max-buscas.")
    if not a.confirmar:
        print("\nNada foi executado. Para rodar, repita com --confirmar (e, se quiser, --max-buscas N).")
        return 0

    inicio = datetime.now()
    meta = {"inicio": sim.agora_iso(), "seed": a.seed, "n": a.n, "url": a.url, "commit": _commit(),
            "frases": str(a.frases.relative_to(RAIZ)) if a.frases.is_relative_to(RAIZ) else str(a.frases),
            "faltas": faltas, "estimativa_serpapi": est, "cota_serpapi_antes": cota,
            "concorrencia": a.concorrencia, "max_buscas": a.max_buscas}

    def enviar(payload):
        r = httpx.post(f"{a.url}/checar", json=payload, timeout=200)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

    def uso():
        try:
            return httpx.get(f"{a.url}/saude", timeout=15).json().get("serpapi_uso_hoje")
        except Exception:
            return None

    trava, gastas = Lock(), [0]
    regs = [None] * len(amostra)

    def rodar(i):
        frase = amostra[i]
        with trava:
            if a.max_buscas is not None and gastas[0] >= a.max_buscas:
                regs[i] = {"frase": frase, "erro": None, "resultado": "nao_executado"}
                print(f"[{i + 1:>2}/{len(amostra)}] ⏭️  não executado (teto de {a.max_buscas} buscas)")
                return
        reg = sim.executar_caso(frase, enviar, uso_serpapi=uso if a.concorrencia == 1 else None)
        with trava:
            gastas[0] += reg.get("serpapi") or 0
            regs[i] = reg
        res = sim.classificar(reg)
        print(f"[{i + 1:>2}/{len(amostra)}] {sim._ROTULO_RESULTADO.get(res, res):26} "
              f"{reg.get('nivel') or '-':13} {reg.get('tempo_s')}s serpapi={reg.get('serpapi')}\n"
              f"        {frase['texto']}")  # frase inteira, sem corte

    with ThreadPoolExecutor(max_workers=max(1, a.concorrencia)) as ex:
        list(ex.map(rodar, range(len(amostra))))

    meta["fim"], meta["cota_serpapi_depois"] = sim.agora_iso(), _cota_serpapi_conta()
    dados = sim.montar(regs, meta, sim.urls_da_base())
    pasta = sim.gravar(dados, a.saida / f"simulacao_{inicio:%Y-%m-%d_%H%M}")
    print("\n" + sim.resumo_terminal(dados, pasta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
