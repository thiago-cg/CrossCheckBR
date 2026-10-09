"""Simulação de usuário: sorteia frases rotuladas, envia ao CrossCheckBR pelo `POST /checar`
(o mesmo endpoint do bot/web) e mede o resultado contra o gabarito.

Este módulo não faz rede por conta própria além do cliente HTTP passado a `executar_caso`;
as partes puras (sorteio, classificação, métricas, verificações, HTML) têm testes offline em
tests/test_simulacao.py. O script `scripts/simular_usuario.py` usa a rede e tem custo — NÃO
entra na suíte de testes.

Recursos da Fase E (docs/PROXIMOS_PASSOS.md) que ainda não existem são lidos se aparecerem
no relatório da API e, se não, marcados como "não implementado":
  onde_encontrado ("Base de checagem" | "Outras fontes (não encontrado na base)"),
  alerta_data (regra de notícia antiga apresentada como atual).
"""
from __future__ import annotations

import html
import json
import random
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

RAIZ = Path(__file__).resolve().parent.parent
ARQ_FRASES = RAIZ / "eval" / "frases_simulacao.json"

# Composição pedida para n=50 (proporcional para outros n).
COMPOSICAO = {"checadas_falsas": 20, "verdadeiras": 15, "fora_da_base": 10, "antigas_como_atuais": 5}
NOMES_CATEGORIA = {"checadas_falsas": "Checadas como falsas/enganosas",
                   "verdadeiras": "Verdadeiras",
                   "fora_da_base": "Agências fora da base (caminho da web)",
                   "antigas_como_atuais": "Antigas apresentadas como atuais"}
LIMIAR_ALTA = 0.7    # propensão (p = decisao.prob) para contar "alta"
LIMIAR_BAIXA = 0.3   # e "baixa" (o sistema usa 0,75/0,25 para o nível que o usuário vê)
NAO_IMPLEMENTADO = "não implementado"

# --------------------------------------------------------------------------- sorteio


def carregar_frases(caminho: Path = ARQ_FRASES) -> List[Dict[str, Any]]:
    return json.loads(caminho.read_text(encoding="utf-8"))["frases"]


def cotas(n: int, composicao: Dict[str, int] = COMPOSICAO) -> Dict[str, int]:
    """Divide n proporcionalmente à composição (maiores restos ganham as sobras)."""
    total = sum(composicao.values())
    brutas = {c: n * k / total for c, k in composicao.items()}
    q = {c: int(v) for c, v in brutas.items()}
    for c in sorted(brutas, key=lambda c: brutas[c] - q[c], reverse=True)[: n - sum(q.values())]:
        q[c] += 1
    return q


def sortear(frases: List[Dict[str, Any]], n: int, seed: int) -> tuple:
    """-> (amostra, faltas). Determinístico para (frases, n, seed). Nunca completa uma categoria
    com frases de outra: o que faltar é devolvido em `faltas` e vai para o relatório."""
    rng = random.Random(seed)
    por_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in sorted(frases, key=lambda f: f["id"]):
        por_cat[f["categoria"]].append(f)
    amostra, faltas = [], {}
    for cat, k in cotas(n).items():
        pool = por_cat.get(cat, [])
        escolhidas = rng.sample(pool, min(k, len(pool)))
        if len(pool) < k:
            faltas[cat] = k - len(pool)
        amostra += escolhidas
    rng.shuffle(amostra)  # ordem de envio aleatória, como um usuário real
    return amostra, faltas


def estimar_buscas(amostra: List[Dict[str, Any]], por_caso: float = 3.0, max_por_caso: int = 9) -> Dict[str, int]:
    """Estimativa de chamadas à SerpAPI. Hoje toda checagem vai à web (a Fase E pula a web quando
    a base resolve). Típico observado: 2-3 por checagem; teto do agente: AGENTE_MAX_BUSCAS=9."""
    return {"tipico": round(len(amostra) * por_caso), "maximo": len(amostra) * max_por_caso}

# --------------------------------------------------------------------------- um caso


def _run_do_caso(dir_runs: Path, texto: str, desde: float) -> Optional[Path]:
    """Acha o run da telemetria desta checagem (mesma entrada, criado depois de `desde`)."""
    if not dir_runs.exists():
        return None
    for d in sorted(dir_runs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.stat().st_mtime < desde - 2:
            break
        try:
            meta = json.loads((d / "resultado.json").read_text(encoding="utf-8")).get("meta", {})
        except (OSError, ValueError):
            continue
        if (meta.get("entrada") or "").strip() == texto.strip():
            return d
    return None


def passos_do_run(dir_run: Optional[Path]) -> Dict[str, Any]:
    """Passo a passo da checagem, lido do trace da telemetria: etapas (com o tempo desde a
    anterior), chamadas de LLM (finalidade, modelo, latência, erro) e julgamento de cada fonte."""
    vazio = {"etapas": [], "llm": [], "juiz_fontes": []}
    if not dir_run:
        return vazio
    try:
        eventos = [json.loads(l) for l in (dir_run / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return vazio
    etapas, llm, juiz, ant = [], [], [], 0.0
    for e in eventos:
        d, t = e.get("dados") or {}, e.get("t_rel_ms") or 0.0
        if e.get("tipo") == "etapa":
            etapas.append({"nome": d.get("nome"), "status": d.get("status"), "detalhe": d.get("detalhe"),
                           "ms": round(t - ant)})
            ant = t
        elif e.get("tipo") == "llm":
            llm.append({"finalidade": d.get("finalidade"), "motor": d.get("motor"), "modelo": d.get("modelo"),
                        "ms": round(d.get("latencia_ms") or 0), "erro": d.get("erro")})
        elif e.get("tipo") == "fonte" and d.get("estagio") == "juiz":
            juiz.append({"url": d.get("url"), "classe": d.get("classe"), "motivo": d.get("motivo")})
    return {"etapas": etapas, "llm": llm, "juiz_fontes": juiz}


def buscas_serpapi_do_run(dir_run: Optional[Path]) -> Optional[int]:
    """Buscas reais (não cache) à SerpAPI registradas no trace do run."""
    if not dir_run:
        return None
    n = 0
    try:
        for linha in (dir_run / "trace.jsonl").read_text(encoding="utf-8").splitlines():
            e = json.loads(linha)
            d = e.get("dados") or {}
            if e.get("tipo") == "http" and "serpapi.com" in (d.get("url") or "") and d.get("cache") == "live":
                n += 1
    except (OSError, ValueError):
        return None
    return n


def executar_caso(frase: Dict[str, Any], enviar: Callable[[Dict[str, Any]], Dict[str, Any]],
                  dir_runs: Path = RAIZ / "runs",
                  uso_serpapi: Optional[Callable[[], Optional[int]]] = None) -> Dict[str, Any]:
    """Envia a frase pelo endpoint do usuário e devolve o registro do caso. Nunca levanta."""
    registro: Dict[str, Any] = {"frase": frase, "erro": None}
    t0, inicio = time.time(), time.time()
    antes = uso_serpapi() if uso_serpapi else None
    try:
        rel = enviar({"tipo": "titulo" if len(frase["texto"]) <= 140 else "texto", "conteudo": frase["texto"]})
    except Exception as e:  # registra e segue para a próxima frase
        registro.update(erro=f"{type(e).__name__}: {e}"[:400], tempo_s=round(time.time() - t0, 1))
        return registro
    registro["tempo_s"] = round(time.time() - t0, 1)
    dir_run = _run_do_caso(dir_runs, frase["texto"], inicio)
    buscas = buscas_serpapi_do_run(dir_run)
    if buscas is None and uso_serpapi and antes is not None:
        depois = uso_serpapi()
        buscas = (depois - antes) if depois is not None else None
    registro.update(run_id=dir_run.name if dir_run else None, serpapi=buscas, passos=passos_do_run(dir_run),
                    **extrair(rel))
    return registro


def extrair(rel: Dict[str, Any]) -> Dict[str, Any]:
    """Campos do relatório da API que a simulação mede."""
    dec = rel.get("decisao") or {}
    urls_que_votaram = {u for v in dec.get("votos", []) or [] for u in v.get("urls", []) or []}
    fontes = [{"url": f.get("url"), "veiculo": f.get("portal_nome"), "titulo": f.get("titulo"),
               "postura": f.get("postura"), "veredito": f.get("veredito"),
               "lida_integralmente": bool(f.get("corpo_lido")), "confiabilidade": f.get("confiabilidade"),
               "votou": f.get("url") in urls_que_votaram}
              for f in rel.get("fontes", []) or []]
    selos = [{"agencia": f["veiculo"], "selo": f["veredito"]} for f in fontes if f["veredito"]]
    alerta = rel.get("alerta_data", NAO_IMPLEMENTADO) if "alerta_data" in rel else NAO_IMPLEMENTADO
    if alerta == NAO_IMPLEMENTADO and any("notícia antiga" in (l or "").lower() for l in rel.get("limitacoes", [])):
        alerta = True
    return {
        "nivel": rel.get("propensao"), "prob": dec.get("prob"),
        "header": rel.get("header", ""), "why": rel.get("why_1linha", ""),
        "justificativa": rel.get("justificativa", ""), "limitacoes": rel.get("limitacoes", []),
        "onde_encontrado": rel.get("onde_encontrado", NAO_IMPLEMENTADO),
        "alerta_data": alerta, "selos": selos, "fontes": fontes,
        "vereditos_aplicados": dec.get("vereditos_aplicados", []) or [],
    }

# --------------------------------------------------------------------------- avaliação


def classificar(reg: Dict[str, Any]) -> str:
    """acerto | falso_alarme | boato_nao_detectado | evidencia_insuficiente | inconclusivo | erro."""
    if reg.get("erro"):
        return "erro"
    if reg.get("nivel") == "indeterminada":
        return "acerto" if reg["frase"]["rotulo"] == "sem_evidencia" else "evidencia_insuficiente"
    p, rot = reg.get("prob"), reg["frase"]["rotulo"]
    if p is None:
        return "inconclusivo"
    if rot in ("falso", "enganoso"):
        return "acerto" if p >= LIMIAR_ALTA else ("boato_nao_detectado" if p <= LIMIAR_BAIXA else "inconclusivo")
    if rot == "verdadeiro":
        return "acerto" if p <= LIMIAR_BAIXA else ("falso_alarme" if p >= LIMIAR_ALTA else "inconclusivo")
    return "inconclusivo"  # sem_evidencia com nível definido


def origem_resposta(reg: Dict[str, Any], urls_base: set) -> str:
    """'base' | 'web' | 'nenhuma'. Usa o campo da API (Fase E); sem ele, deriva: algum voto veio
    de uma checagem da base → base; senão, houve busca na web → web."""
    onde = reg.get("onde_encontrado")
    if onde and onde != NAO_IMPLEMENTADO:
        return "base" if onde.lower().startswith("base") else "web"
    if any(f["votou"] and _chave(f["url"]) in urls_base for f in reg.get("fontes", [])):
        return "base"
    return "web" if (reg.get("serpapi") or 0) > 0 or reg.get("fontes") else "nenhuma"


def _chave(url: str) -> str:
    try:
        from factcheck_mvp.ingestor import chave_url
        return chave_url(url or "")
    except Exception:
        return (url or "").lower()


def indicadores(regs: List[Dict[str, Any]], urls_base: set) -> Dict[str, Any]:
    ok = [r for r in regs if not r.get("erro") and r.get("resultado") != "nao_executado"]
    res = Counter(r["resultado"] for r in ok)
    rot = Counter(r["frase"]["rotulo"] for r in ok)
    origens = Counter(origem_resposta(r, urls_base) for r in ok)
    datas = [r for r in ok if r["frase"]["categoria"] == "antigas_como_atuais"]
    n_ok = len(ok)

    def pct(a, b):
        return round(100 * a / b, 1) if b else None
    return {
        "total": len(regs), "sucessos": n_ok, "erros": sum(1 for r in regs if r.get("erro")),
        "nao_executados": sum(1 for r in regs if r.get("resultado") == "nao_executado"),
        "taxa_acerto": pct(res["acerto"], n_ok),
        "falsos_alarmes": res["falso_alarme"], "falsos_alarmes_pct_verdadeiras": pct(res["falso_alarme"], rot["verdadeiro"]),
        "boatos_nao_detectados": res["boato_nao_detectado"],
        "boatos_nao_detectados_pct_falsas": pct(res["boato_nao_detectado"], rot["falso"] + rot["enganoso"]),
        "evidencia_insuficiente_pct": pct(res["evidencia_insuficiente"], n_ok),
        "inconclusivos": res["inconclusivo"],
        "resolvido_base_pct": pct(origens["base"], n_ok), "resolvido_web_pct": pct(origens["web"], n_ok),
        "acerto_casos_data": f"{sum(1 for r in datas if r['resultado'] == 'acerto')}/{len(datas)}" if datas else None,
        "regra_data_acionada": f"{sum(1 for r in datas if r.get('alerta_data') is True)}/{len(datas)}" if datas else None,
        "tempo_medio_s": round(sum(r.get("tempo_s") or 0 for r in ok) / n_ok, 1) if n_ok else None,
        "serpapi_total": sum(r.get("serpapi") or 0 for r in regs),
    }

# --------------------------------------------------------------------------- regras

_RE_SELO_ATRIBUIDO = re.compile(r"Selo d[oa]s?\s+[^:]{2,60}:\s*[A-ZÁÉÍÓÚÃÕÇ_ #]+")
_RE_SELO_SOLTO = re.compile(r"\b(FALSO|FALSA|VERDADEIRO|VERDADEIRA|ENGANOSO|ENGANOSA|SATIRA|SÁTIRA|"
                            r"SEM_EVIDENCIA|DISTORCIDO|#FAKE|#FATO)\b")


def conclusao(reg: Dict[str, Any]) -> str:
    return " ".join(x for x in (reg.get("header"), reg.get("why"), reg.get("justificativa")) if x)


def verificar_regras(regs: List[Dict[str, Any]], urls_base: set) -> List[Dict[str, Any]]:
    """As 4 verificações pedidas. Cada uma: {nome, ok, violacoes: [{id, detalhe}], nota}."""
    from factcheck_mvp.agregador import verificar_neutralidade
    ok = [r for r in regs if not r.get("erro") and r.get("resultado") != "nao_executado"]
    neutro, selo, titulo, web = [], [], [], []
    for r in ok:
        cid, texto = r["frase"]["id"], conclusao(r)
        sem_selos = _RE_SELO_ATRIBUIDO.sub("", texto)       # selo atribuído é permitido
        achados = verificar_neutralidade(sem_selos)
        binarios = [a for a in achados if a in ("é falso", "é falsa", "é verdade", "é verdadeiro", "é mentira")]
        if binarios:
            neutro.append({"id": cid, "detalhe": ", ".join(binarios)})
        soltos = _RE_SELO_SOLTO.findall(sem_selos)
        if soltos:
            selo.append({"id": cid, "detalhe": f"selo sem 'Selo da <agência>:' — {', '.join(sorted(set(soltos)))}"})
        so_titulo = [f["url"] for f in r.get("fontes", []) if f["votou"] and not f["lida_integralmente"]]
        if so_titulo:
            titulo.append({"id": cid, "detalhe": f"{len(so_titulo)} fonte(s) só de título votaram: {so_titulo[0]}"})
        base_aplicavel = any(_chave(v.get("url")) in urls_base for v in r.get("vereditos_aplicados", []))
        if base_aplicavel and (r.get("serpapi") or 0) > 0:
            web.append({"id": cid, "detalhe": f"selo aplicável da base e mesmo assim {r['serpapi']} busca(s) na web"})
    return [
        {"nome": "Nenhuma conclusão contém \"é falso\" ou \"é verdade\"", "ok": not neutro, "violacoes": neutro,
         "nota": "Verifica título, resumo e justificativa (a NOSSA conclusão), com selos atribuídos removidos."},
        {"nome": "Selos aparecem sempre atribuídos à agência (\"Selo do Aos Fatos: FALSO\")", "ok": not selo,
         "violacoes": selo, "nota": "Formato exigido pela Fase E (E5); antes dela a justificativa usa \"FALSO (Agência)\"."},
        {"nome": "Nenhuma fonte lida só pelo título influenciou a propensão", "ok": not titulo, "violacoes": titulo,
         "nota": "Regra da Fase E (E3); hoje fontes só de título votam com peso 0,7."},
        {"nome": "A web não foi acionada quando a base tinha checagem aplicável", "ok": not web, "violacoes": web,
         "nota": "Regra da Fase E (E1); hoje a base e a web rodam sempre."},
    ]

# --------------------------------------------------------------------------- relatório


def montar(regs: List[Dict[str, Any]], meta: Dict[str, Any], urls_base: set) -> Dict[str, Any]:
    for r in regs:
        r.setdefault("resultado", classificar(r))
        r["origem_resposta"] = origem_resposta(r, urls_base) if not r.get("erro") else None
    por_cat = {cat: indicadores([r for r in regs if r["frase"]["categoria"] == cat], urls_base)
               for cat in COMPOSICAO if any(r["frase"]["categoria"] == cat for r in regs)}
    return {"meta": meta, "geral": indicadores(regs, urls_base), "por_categoria": por_cat,
            "regras": verificar_regras(regs, urls_base), "casos": regs}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


_ROTULO_RESULTADO = {"acerto": "✅ acerto", "falso_alarme": "🚨 falso alarme", "boato_nao_detectado": "❌ boato não detectado",
                     "evidencia_insuficiente": "⚪ evidência insuficiente", "inconclusivo": "🟡 inconclusivo",
                     "erro": "💥 erro", "nao_executado": "⏭️ não executado"}
_INDICADORES = [("total", "Casos"), ("sucessos", "Sucessos"), ("erros", "Erros"), ("nao_executados", "Não executados"),
                ("taxa_acerto", "Taxa de acerto (%)"), ("falsos_alarmes", "Falsos alarmes"),
                ("boatos_nao_detectados", "Boatos não detectados"),
                ("evidencia_insuficiente_pct", "Evidência insuficiente (%)"),
                ("resolvido_base_pct", "Resolvido pela base (%)"), ("resolvido_web_pct", "Resolvido pela web (%)"),
                ("acerto_casos_data", "Acerto nos casos de data"), ("regra_data_acionada", "Regra de data acionada"),
                ("tempo_medio_s", "Tempo médio (s)"), ("serpapi_total", "Chamadas à SerpAPI")]


def _html_passos(p: Dict[str, Any]) -> str:
    """Passo a passo da chamada: etapas com tempo, chamadas de LLM e o que o juiz disse de cada fonte."""
    if not p.get("etapas"):
        return "<p class='muted'>Passo a passo indisponível (trace não encontrado).</p>"
    etapas = "".join(f"<li><b>{_e(x['nome'])}</b> [{_e(x['status'])}] +{x['ms'] / 1000:.1f}s — {_e(x['detalhe'])}</li>"
                     for x in p["etapas"])
    llm = "".join(f"<li>{_e(x['finalidade'])}: {_e(x['motor'])}/{_e(x['modelo'])} em {x['ms'] / 1000:.1f}s"
                  f"{' · <span class=bad>erro: ' + _e(x['erro'])[:160] + '</span>' if x.get('erro') else ''}</li>"
                  for x in p.get("llm", []))
    juiz = "".join(f"<li>{_e(x['classe'])} — {_e((x['motivo'] or '')[:200])}<br><span class='muted'>{_e(x['url'])}</span></li>"
                   for x in p.get("juiz_fontes", []))
    return (f"<details><summary>Passo a passo desta chamada ({len(p['etapas'])} etapas, {len(p.get('llm', []))} "
            f"chamada(s) de LLM)</summary><ol>{etapas}</ol>"
            + (f"<p><b>Chamadas de LLM</b></p><ul>{llm}</ul>" if llm else "")
            + (f"<p><b>Julgamento de cada fonte</b></p><ul>{juiz}</ul>" if juiz else "") + "</details>")


def html_relatorio(dados: Dict[str, Any]) -> str:
    m, g = dados["meta"], dados["geral"]
    cats = dados["por_categoria"]
    css = """
    :root{--fg:#1d2433;--muted:#5b6475;--bg:#fff;--card:#f5f7fa;--line:#dde2ea;--ok:#067647;--bad:#b42318;--warn:#b54708}
    @media (prefers-color-scheme:dark){:root{--fg:#e6e9ef;--muted:#a3abba;--bg:#14171c;--card:#1d2128;--line:#2e3440;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24}}
    body{font-family:system-ui,-apple-system,sans-serif;color:var(--fg);background:var(--bg);max-width:1200px;margin:0 auto;padding:16px;line-height:1.45}
    h1{font-size:1.5rem}h2{font-size:1.2rem;margin-top:2rem;border-bottom:1px solid var(--line);padding-bottom:.3rem}
    .muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
    .card{background:var(--card);border-radius:8px;padding:10px}.card b{display:block;font-size:1.3rem}
    table{border-collapse:collapse;width:100%;font-size:.9rem;display:block;overflow-x:auto}
    th,td{border-bottom:1px solid var(--line);padding:6px;text-align:left;vertical-align:top}
    .ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
    details{background:var(--card);border-radius:8px;padding:8px 12px;margin:8px 0}
    .alerta{border-left:4px solid var(--warn);padding:6px 10px;background:var(--card)}
    """
    partes = [f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>"
              f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>Simulação CrossCheckBR</title>"
              f"<style>{css}</style></head><body>",
              f"<h1>Simulação de usuário — CrossCheckBR</h1>"
              f"<p class='muted'>{_e(m['inicio'])} · seed <b>{_e(m['seed'])}</b> · n={_e(m['n'])} · API {_e(m['url'])} · "
              f"commit {_e(m.get('commit'))} · conjunto {_e(m['frases'])}</p>"]
    if m.get("faltas"):
        partes.append("<p class='alerta'>Faltaram frases rotuladas para cumprir a composição pedida: "
                      + ", ".join(f"{_e(NOMES_CATEGORIA[c])}: {k}" for c, k in m["faltas"].items())
                      + ". Complete <code>eval/frases_simulacao.json</code> (ver <code>scripts/gerar_frases_simulacao.py</code>).</p>")
    partes.append(f"<p class='muted'>Acerto: propensão p ≥ {LIMIAR_ALTA} para frases falsas/enganosas e p ≤ {LIMIAR_BAIXA} "
                  "para verdadeiras (p = probabilidade da decisão; o nível exibido ao usuário usa 0,75/0,25).</p>")
    if m.get("analise"):
        partes.append("<h2>Análise e melhorias</h2><ol>" + "".join(
            f"<li><b>{_e(x['titulo'])}</b> — {_e(x['detalhe'])}"
            + (" <span class='muted'>Casos: " + ", ".join(f"<a href='#{_e(c)}'>{_e(c)}</a>" for c in x.get('casos', []))
               + "</span>" if x.get("casos") else "") + "</li>" for x in m["analise"]) + "</ol>")
    partes.append("<h2>Resumo geral</h2><div class='cards'>" + "".join(
        f"<div class='card'><span class='muted'>{_e(rotulo)}</span><b>{_e(g.get(k))}</b></div>" for k, rotulo in _INDICADORES)
        + "</div>")
    partes.append("<h2>Por categoria</h2><table><tr><th>Indicador</th>"
                  + "".join(f"<th>{_e(NOMES_CATEGORIA[c])}</th>" for c in cats) + "</tr>"
                  + "".join(f"<tr><td>{_e(rotulo)}</td>" + "".join(f"<td>{_e(cats[c].get(k))}</td>" for c in cats) + "</tr>"
                            for k, rotulo in _INDICADORES) + "</table>")
    partes.append("<h2>Verificações de regras</h2><table><tr><th>Regra</th><th>Resultado</th><th>Violações</th></tr>")
    for v in dados["regras"]:
        viol = "".join(f"<div><a href='#{_e(x['id'])}'>{_e(x['id'])}</a>: {_e(x['detalhe'])}</div>" for x in v["violacoes"][:15])
        n_viol = len(v["violacoes"])
        mais = f"<div class='muted'>+{n_viol - 15}</div>" if n_viol > 15 else ""
        status = "✅ ok" if v["ok"] else f"❌ {n_viol} caso(s)"
        partes.append(f"<tr><td>{_e(v['nome'])}<div class='muted'>{_e(v['nota'])}</div></td>"
                      f"<td class='{'ok' if v['ok'] else 'bad'}'>{status}</td><td>{viol}{mais}</td></tr>")
    partes.append("</table>")
    partes.append("<h2>Tabela de casos</h2><table><tr><th>#</th><th>Frase</th><th>Categoria</th><th>Rótulo</th>"
                  "<th>Propensão</th><th>Onde foi encontrado</th><th>Resultado</th><th>Tempo</th><th>SerpAPI</th></tr>")
    for i, r in enumerate(dados["casos"], 1):
        f = r["frase"]
        prop = "—" if r.get("prob") is None else f"{r.get('nivel')} (p={r['prob']:.2f})"
        partes.append(f"<tr><td>{i}</td><td><a href='#{_e(f['id'])}'>{_e(f['texto'])}</a></td>"
                      f"<td>{_e(NOMES_CATEGORIA[f['categoria']])}</td><td>{_e(f['rotulo'])}</td><td>{_e(prop)}</td>"
                      f"<td>{_e(r.get('onde_encontrado') if r.get('onde_encontrado') != NAO_IMPLEMENTADO else (r.get('origem_resposta') or '—') + ' (derivado)')}</td>"
                      f"<td>{_e(_ROTULO_RESULTADO.get(r['resultado'], r['resultado']))}</td>"
                      f"<td>{_e(r.get('tempo_s'))}</td><td>{_e(r.get('serpapi'))}</td></tr>")
    partes.append("</table>")
    revisar = [r for r in dados["casos"] if r["resultado"] in ("falso_alarme", "boato_nao_detectado", "erro")]
    partes.append(f"<h2>Casos para revisar ({len(revisar)})</h2>"
                  + ("" if revisar else "<p class='muted'>Nenhum erro, falso alarme ou boato não detectado.</p>"))
    for r in revisar:
        partes.append(f"<p class='bad'><a href='#{_e(r['frase']['id'])}'>{_e(_ROTULO_RESULTADO[r['resultado']])}</a> — "
                      f"{_e(r['frase']['texto'])}</p>")
    partes.append("<h2>Detalhes dos casos</h2>")
    for r in dados["casos"]:
        f = r["frase"]
        link_checagem = (f" · <a href='{_e(f['url_checagem'])}' target='_blank' rel='noopener'>checagem</a>"
                         if f.get("url_checagem") else "")
        fontes = "".join(
            f"<li>{'📄' if x['lida_integralmente'] else '📰 <i>não analisada integralmente</i>'} "
            f"{'🗳️ votou' if x['votou'] else ''} <b>{_e(x['veiculo'])}</b> [{_e(x['postura'])}] "
            f"{('· selo ' + _e(x['veredito'])) if x['veredito'] else ''} · {_e(x['confiabilidade'])} — "
            f"<a href='{_e(x['url'])}' target='_blank' rel='noopener'>{_e((x['titulo'] or x['url'])[:90])}</a></li>"
            for x in r.get("fontes", []))
        partes.append(
            f"<details id='{_e(f['id'])}' {'open' if r['resultado'] in ('falso_alarme', 'boato_nao_detectado', 'erro') else ''}>"
            f"<summary>{_e(_ROTULO_RESULTADO.get(r['resultado'], r['resultado']))} — {_e(f['texto'])}</summary>"
            f"<p><b>Esperado:</b> {_e(f['rotulo'])} · <b>Origem da frase:</b> {_e(f.get('origem'))}"
            f"{link_checagem} · {_e(f.get('nota'))}</p>"
            + (f"<p class='bad'><b>Erro:</b> {_e(r['erro'])}</p>" if r.get("erro") else
               f"<p><b>{_e(r.get('header'))}</b><br>{_e(r.get('why'))}</p><p>{_e(r.get('justificativa'))}</p>"
               f"<p><b>Onde foi encontrado:</b> {_e(r.get('onde_encontrado'))} · <b>Regra de data:</b> {_e(r.get('alerta_data'))} · "
               f"<b>Selos:</b> {_e(', '.join(s['agencia'] + ': ' + s['selo'] for s in r.get('selos', [])) or '—')} · "
               f"<b>Tempo:</b> {_e(r.get('tempo_s'))}s · <b>SerpAPI:</b> {_e(r.get('serpapi'))} · <b>run:</b> {_e(r.get('run_id'))}</p>"
               f"<ul>{fontes}</ul>" + _html_passos(r.get("passos") or {}))
            + "</details>")
    partes.append("</body></html>")
    return "\n".join(partes)


def resumo_terminal(dados: Dict[str, Any], pasta: Path) -> str:
    g = dados["geral"]
    linhas = [f"SIMULAÇÃO seed={dados['meta']['seed']} n={g['total']}: {g['sucessos']} ok, {g['erros']} erro(s), "
              f"{g['nao_executados']} não executado(s)"]
    linhas += [f"  {rotulo:32} {g.get(k)}" for k, rotulo in _INDICADORES[4:]]
    linhas += [f"  regra: {'ok ' if v['ok'] else 'FALHA'} {v['nome']} ({len(v['violacoes'])})" for v in dados["regras"]]
    linhas.append(f"relatório: {pasta / 'relatorio.html'}")
    return "\n".join(linhas)


def gravar(dados: Dict[str, Any], pasta: Path) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "dados.json").write_text(json.dumps(dados, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (pasta / "relatorio.html").write_text(html_relatorio(dados), encoding="utf-8")
    return pasta


def urls_da_base(caminho: Path = RAIZ / "factcheck_mvp" / "data" / "checagens.jsonl") -> set:
    from factcheck_mvp.ingestor import carregar_jsonl, chave_url
    return {chave_url(r.get("url", "")) for r in carregar_jsonl(caminho)}


def agora_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
