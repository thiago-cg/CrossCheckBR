"""Experimento: o ReaderLM-v2 acha o veredito/afirmação de uma checagem SEM o JSON-LD?

Gabarito sem rotular à mão (review §4): páginas que publicam `ClaimReview`. Para cada
URL, o gabarito sai de `jsonld.veredito_da_pagina(html)` (selo do editor, normalizado por
`selos.normalizar`); o ReaderLM recebe o MESMO HTML com os blocos JSON-LD REMOVIDOS
(e a pré-limpeza oficial da Jina, que também tira todo <script>) e extrai, com schema,
`afirmacao_checada`, `veredito`, `trecho_evidencia`. Campo não ancorado no texto da
página (< 80% das palavras) é descartado como alucinação (`extracao.readerlm_campos`).

Métricas impressas:
  - acurácia do veredito normalizado (enum FALSO|ENGANOSO|…) sobre as páginas com
    gabarito normalizável — falha/descartado conta como erro;
  - acerto literal do selo (texto normalizado igual);
  - afirmação: cobertura de tokens do gabarito na extração ≥ 0,6;
  - taxa de falha (rede/HTTP/finish_reason=length/JSON inválido/HTML grande demais),
    campos descartados por não-ancoragem e latência média.

COMO RODAR
----------
1) AVISO: carregar o ReaderLM no Unsloth Studio pode TROCAR o modelo ativo do Studio
   (o LFM2.5 que o pipeline/juiz usa na porta 8888). Suba-o numa porta DEDICADA e
   confirme com o usuário antes:

       unsloth run -hf mradermacher/ReaderLM-v2-GGUF:Q8_0 --max-seq-length 65536 -np 1 \\
           --temperature 0 --repetition-penalty 1.08 --disable-tools --api-only -p 8889
       curl -s http://127.0.0.1:8889/v1/models      # use o "id" retornado em --modelo

   (-np 1: com 4 slots cada requisição recebe 1/4 do contexto e o HTML é truncado.)
   Para comparar quantizações, suba de novo com :Q6_K / :Q4_K_M e rode com --quant.

2) Rodar (default: URLs `origem=claimreview` de factcheck_mvp/data/checagens.jsonl +
   fixtures tests/fixtures/jsonld/*.html.gz com ClaimReview; até --n 30):

       python3 scripts/exp_readerlm.py --quant Q8_0                 # HTML inteiro limpo
       python3 scripts/exp_readerlm.py --quant Q8_0 --com-recorte   # recorte article/main/seletor
       python3 scripts/exp_readerlm.py --dry-run                    # sem ReaderLM: gabarito + tamanho

   Opções: --urls arquivo.txt (1 URL por linha), --n, --modelo, --base-url (ou env
   READERLM_BASE_URL, default http://127.0.0.1:8889/v1), --saida resultados.jsonl.
   O HTML é baixado via `replay.http_get` (respeita IO_MODO: `IO_MODO=record` grava
   cassetes em eval/cassettes/, `replay` roda offline). A chamada ao ReaderLM também passa
   por `replay.llm_post` (motor="readerlm"), então pode ser reproduzida.

Custo: 10 s a 2 min por página no M4 (estimado, r4_readerlm.md). Licença CC-BY-NC-4.0.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from factcheck_mvp import extracao, jsonld, replay, selos  # noqa: E402
from factcheck_mvp.aprofundar import HEADERS_NAVEGADOR, MAX_BYTES_PADRAO  # noqa: E402

CHECAGENS = RAIZ / "factcheck_mvp" / "data" / "checagens.jsonl"
FIXTURES = RAIZ / "tests" / "fixtures" / "jsonld"


def _urls_padrao(n: int) -> List[str]:
    urls: List[str] = []
    try:
        for linha in CHECAGENS.read_text(encoding="utf-8").splitlines():
            d = json.loads(linha)
            if d.get("origem") == "claimreview" and d.get("url") and d["url"] not in urls:
                urls.append(d["url"])
    except (OSError, ValueError):
        pass
    return urls[:n]


def _fixtures() -> List[Tuple[str, str]]:
    """(rótulo, html) das fixtures locais que têm ClaimReview (sem rede)."""
    saida = []
    for arq in sorted(FIXTURES.glob("*.html.gz")):
        html = gzip.open(arq).read().decode("utf-8", "ignore")
        if jsonld.veredito_da_pagina(html):
            saida.append((f"fixture:{arq.name}", html))
    return saida


def _baixar(url: str) -> Optional[str]:
    try:
        r = replay.http_get(url, headers=dict(HEADERS_NAVEGADOR), timeout=30, follow_redirects=True)
        if r.status_code != 200:
            print(f"  ! {url[:80]}: HTTP {r.status_code}", file=sys.stderr)
            return None
        return r.content[:MAX_BYTES_PADRAO].decode("utf-8", "ignore")
    except Exception as e:
        print(f"  ! {url[:80]}: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return None


def _cobertura(gabarito: Optional[str], extraido: Optional[str]) -> float:
    g = set(extracao._norm(gabarito or "").split())
    x = set(extracao._norm(extraido or "").split())
    return len(g & x) / len(g) if g else 0.0


def _seletor(url: str) -> Optional[str]:
    try:
        from factcheck_mvp.catalogo import Catalogo
        portal = Catalogo.carregar().por_url(url)
        return ((portal or {}).get("css_selectors") or {}).get("corpo")
    except Exception:
        return None


def _servidor_ok(cfg: Dict) -> bool:
    try:
        import curl_cffi.requests as _curl
        r = _curl.get(f"{cfg['base_url']}/models", timeout=3, impersonate="chrome",
                      headers={"Authorization": f"Bearer {cfg['api_key']}"})
        return r.status_code == 200
    except Exception:
        return False


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ReaderLM-v2 x gabarito ClaimReview (JSON-LD removido)")
    ap.add_argument("--quant", default="Q8_0", help="rótulo da quantização servida (Q8_0|Q6_K|Q4_K_M)")
    ap.add_argument("--com-recorte", action="store_true",
                    help="recorta article/main/seletor do catálogo antes (default: HTML inteiro limpo)")
    ap.add_argument("--urls", help="arquivo com 1 URL por linha (default: checagens.jsonl origem=claimreview)")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--sem-fixtures", action="store_true", help="não inclui tests/fixtures/jsonld")
    ap.add_argument("--modelo", default=None, help="id do modelo em /v1/models (env READERLM_MODEL)")
    ap.add_argument("--base-url", default=None, help="env READERLM_BASE_URL (default :8889/v1)")
    ap.add_argument("--dry-run", action="store_true", help="não chama o ReaderLM")
    ap.add_argument("--saida", default=None, help="grava resultados por página (JSONL)")
    a = ap.parse_args(argv)

    if a.base_url:
        os.environ["READERLM_BASE_URL"] = a.base_url
    if a.modelo:
        os.environ["READERLM_MODEL"] = a.modelo
    cfg = extracao._cfg_readerlm()
    if not a.dry_run and not _servidor_ok(cfg):
        print(f"ReaderLM fora do ar em {cfg['base_url']}. Suba-o numa porta dedicada (ver docstring; "
              "AVISO: carregar no Studio pode trocar o modelo ativo) ou use --dry-run.", file=sys.stderr)
        return 2

    urls = ([u.strip() for u in Path(a.urls).read_text().splitlines() if u.strip() and not u.startswith("#")]
            if a.urls else _urls_padrao(a.n))
    paginas: List[Tuple[str, str]] = [] if a.sem_fixtures else _fixtures()
    for u in urls:
        if len(paginas) >= a.n:
            break
        html = _baixar(u)
        if html:
            paginas.append((u, html))
    paginas = paginas[: a.n]

    linhas = []
    for rotulo, html in paginas:
        gab = jsonld.veredito_da_pagina(html, None if rotulo.startswith("fixture:") else rotulo)
        if not gab:
            print(f"  - {rotulo[:80]}: sem ClaimReview (fora do gabarito)", file=sys.stderr)
            continue
        seletor = _seletor(rotulo) if a.com_recorte and not rotulo.startswith("fixture:") else None
        limpo = extracao.limpar_html_readerlm(extracao.remover_jsonld(html), seletor, recortar=a.com_recorte)
        reg = {"url": rotulo, "quant": a.quant, "com_recorte": a.com_recorte, "chars_html_limpo": len(limpo),
               "tokens_estimados": len(limpo) // 4, "gab_selo": gab["selo_original"],
               "gab_veredito": gab["veredito"], "gab_afirmacao": gab["afirmacao_checada"]}
        if not a.dry_run:
            t0 = time.time()
            try:
                campos, avisos = extracao.readerlm_campos(html, seletor, com_corpo=False, cfg=cfg,
                                                          recortar=a.com_recorte)
                reg.update(erro=None, avisos=avisos, selo=campos.get("veredito"),
                           afirmacao=campos.get("afirmacao_checada"), trecho=campos.get("trecho_evidencia"))
            except extracao.FalhaReaderLM as e:
                reg.update(erro=str(e), avisos=[], selo=None, afirmacao=None, trecho=None)
            reg["latencia_s"] = round(time.time() - t0, 1)
            reg["veredito"] = selos.normalizar(reg["selo"], gab.get("agencia")) if reg["selo"] else None
            reg["acerto_veredito"] = bool(gab["veredito"]) and reg["veredito"] == gab["veredito"]
            reg["acerto_selo"] = extracao._norm(reg["selo"] or "") == extracao._norm(gab["selo_original"] or "")
            reg["cobertura_afirmacao"] = round(_cobertura(gab["afirmacao_checada"], reg["afirmacao"]), 2)
        linhas.append(reg)
        print(f"{rotulo[:70]:70} | gab={gab['veredito'] or gab['selo_original']!s:10} | "
              + ("dry-run" if a.dry_run else
                 f"readerlm={reg['veredito'] or reg['selo'] or ('ERRO: ' + (reg['erro'] or '-'))!s:.40} "
                 f"| afirm={reg['cobertura_afirmacao']:.2f} | {reg['latencia_s']}s")
              + f" | ~{reg['tokens_estimados']} tok")

    if a.saida:
        with open(a.saida, "w", encoding="utf-8") as f:
            for reg in linhas:
                f.write(json.dumps(reg, ensure_ascii=False) + "\n")
    n = len(linhas)
    print(f"\n{n} páginas com gabarito ClaimReview | quant={a.quant} | recorte={'sim' if a.com_recorte else 'não'}")
    if not n:
        return 1
    print(f"tokens de entrada estimados: média {sum(r['tokens_estimados'] for r in linhas) // n}, "
          f"máx {max(r['tokens_estimados'] for r in linhas)}")
    if a.dry_run:
        return 0
    com_enum = [r for r in linhas if r["gab_veredito"]]
    falhas = sum(1 for r in linhas if r["erro"])
    descartes = sum(len(r["avisos"]) for r in linhas)
    print(f"acurácia veredito (enum): {sum(r['acerto_veredito'] for r in com_enum)}/{len(com_enum)} "
          f"= {100 * sum(r['acerto_veredito'] for r in com_enum) / max(1, len(com_enum)):.0f}%")
    print(f"acerto literal do selo:   {sum(r['acerto_selo'] for r in linhas)}/{n}")
    print(f"afirmação (cobertura ≥ 0,6): {sum(r['cobertura_afirmacao'] >= 0.6 for r in linhas)}/{n}")
    print(f"falhas de chamada: {falhas}/{n} | campos descartados (não ancorados): {descartes}")
    lat = [r["latencia_s"] for r in linhas if not r["erro"]]
    if lat:
        print(f"latência média: {sum(lat) / len(lat):.1f}s (máx {max(lat):.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
