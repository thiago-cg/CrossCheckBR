"""Gera eval/frases_simulacao.json: o conjunto rotulado de onde a simulação sorteia.

Nenhum rótulo é inventado: cada frase vem de uma checagem de agência (data/checagens.jsonl)
ou de um caso já rotulado do eval (só split `dev`; o holdout fica intocado).

Frases longas e completas: das checagens do Boatos.org entra a DESCRIÇÃO do boato como circulou
("Boato – Matt Groening estaria preparando um episódio…"), sempre como frase inteira, até o ponto
final — nunca cortada no meio. Toda frase (de qualquer categoria) pode ganhar uma "moldura" de
mensagem real ("Recebi isso no grupo da família: “…” Alguém sabe se é verdade?"), aplicada por
igual a falsas e verdadeiras para o tamanho do texto não denunciar o rótulo. As molduras não usam
"hoje/agora/ontem", para não disparar a regra de notícia antiga. O campo `afirmacao` guarda o
texto sem moldura, para auditoria.

Categorias:
  checadas_falsas      descrição de boatos do Boatos.org + FALSO/ENGANOSO da base + casos do eval
  verdadeiras          VERDADEIRO da base (selo de agência) + casos verdadeiros do eval
  fora_da_base         casos `fora_do_indice` do eval (a checagem foi excluída da base de propósito)
  antigas_como_atuais  VERDADEIRO da base com mais de 1 ano, reescrita com "hoje/agora/ontem"
                       → rótulo `enganoso` (fato real, mas antigo, apresentado como atual)

Uso: python3 scripts/gerar_frases_simulacao.py [--data-ref 2026-10-07]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from factcheck_mvp.ingestor import carregar_excluidas, carregar_jsonl, chave_url  # noqa: E402

SAIDA = RAIZ / "eval" / "frases_simulacao.json"
MAX_DESCRICOES_BOATOS = 100       # descrições completas de boatos (Boatos.org), amostra fixa por hash
MAX_FALSAS_DA_BASE = 30           # alegações curtas "É falso que X" (Lupa e outras), para variar o formato
TAM_DESCRICAO = (60, 320)         # frase inteira entre 60 e 320 caracteres
# Moldura de mensagem real. Índice 0 = sem moldura. Nada de "hoje/agora/ontem" aqui.
MOLDURAS = (
    "{t}",
    "Recebi isso no grupo da família: “{t}” Alguém sabe se é verdade?",
    "Gente, estão compartilhando isso no WhatsApp: “{t}” Isso procede?",
    "Me mandaram essa mensagem e fiquei na dúvida: “{t}”",
    "Vi um post dizendo o seguinte: “{t}” É verdade ou é fake?",
    "Minha tia encaminhou isso no grupo: “{t}” Dá pra confiar?",
)
MOLDURAS_PERGUNTA = (0, 3)        # para texto que já termina em "?", molduras sem pergunta extra
_PREFIXOS_TEMPO = ("Hoje, ", "Agora: ", "Ontem, ")
N_ANTIGAS = 5
# Da base só entram checagens cujo TÍTULO separa sem ambiguidade a alegação do selo. Sem isso,
# a "afirmação" às vezes é o texto da CORREÇÃO ("Vídeo é da Malásia, não do Rio" com selo FALSO)
# e o gabarito sai invertido.
_TITULO_FALSA = re.compile(r"^\s*(?:é|e)\s+(?:falso|falsa|#?fake)\s+que\b|^\s*n[ãa]o\s+[ée]\s+verdade\s+que\b"
                           r"|#\s*boato\s*$", re.I)
_TITULO_VERDADEIRA = re.compile(r"^\s*(?:é|e)\s+(?:#?fato|verdade|verdadeiro|verdadeira)\b", re.I)


_MOLDURA = re.compile(r"^(?:mensagem|post|texto|v[íi]deo|corrente|[áa]udio)\s+(?:que\s+)?(?:diz|afirma|alega)\s+que\s+",
                      re.I)


# Verdadeiras escolhidas à mão entre as checagens VERDADEIRO da base cujo título não passa no filtro
# automático ("#CaiuNaRede: É verdadeiro que X"). Critério: selo VERDADEIRO sem ressalva ("mas…"),
# alegação conferida com o trecho da checagem. Texto = a alegação, sem a moldura do título.
# Revisão: Claude, 09/10/2026 — a equipe deve conferir (campo `revisao`).
VERDADEIRAS_REVISADAS = [
    ("https://www.agencialupa.org/jornalismo/2020/09/30/caiu-na-rede-cerveja-retarda-envelhecimento/",
     "Estudo aponta que a cerveja pode retardar o envelhecimento."),
    ("https://www.agencialupa.org/jornalismo/2020/10/21/caiu-na-rede-mulher-tubarao/",
     "Vídeo mostra uma mulher descendo de um tobogã no mar e dando de cara com um tubarão."),
    ("https://www.agencialupa.org/jornalismo/2020/11/04/caiu-na-rede-vacina-febre-tifoide/",
     "A vacina contra a febre tifoide levou 105 anos para ficar pronta."),
    ("https://www.agencialupa.org/jornalismo/2020/11/19/caiu-na-rede-oferenda-cemiterio/",
     "Deixaram uma oferenda com muitas notas de dinheiro no Cemitério de Inhaúma, no Rio de Janeiro."),
    ("https://www.agencialupa.org/jornalismo/2020/11/19/caiu-na-rede-paineis-de-energia-solar/",
     "Vídeo mostra uma grande área montanhosa coberta de painéis de energia solar."),
    ("https://www.agencialupa.org/jornalismo/2020/11/26/caiu-na-rede-propaganda-eleitoral/",
     "Fazer propaganda eleitoral nas redes sociais no dia da eleição é crime."),
    ("https://www.agencialupa.org/jornalismo/2020/12/03/caiu-na-rede-intervencao-cirurgica/",
     "Pelo Código Civil, ninguém pode ser obrigado a se submeter, com risco de vida, a tratamento médico "
     "ou intervenção cirúrgica."),
]


def _limpa(texto: str) -> str:
    """'Mensagem que diz que X' -> 'X' (é X que o usuário mandaria)."""
    t = _MOLDURA.sub("", (texto or "").strip())
    return t[:1].upper() + t[1:]


def _ruim(texto: str) -> bool:
    """Frase que não serve como mensagem de usuário: pergunta, manchete composta, hashtag de seção,
    descrição de conteúdo ("Mensagem sobre …"), curta ou longa demais."""
    t = (texto or "").strip()
    return (not 25 <= len(t) <= 180 or t.endswith("?") or " mas " in t.lower() or "#" in t
            or re.match(r"^(verificamos|checamos|veja|confira|mensagem sobre|post sobre|texto sobre)\b",
                        t, re.I) is not None)


def _ordem(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


# Abertura típica do texto do ARTIGO grudada na descrição do boato sem ponto entre os dois
# ("…no mural Nem bem o boato sobre…"): descrição assim não é frase completa e é descartada.
_EMENDA_ARTIGO = re.compile(r"[a-zà-ú0-9,] (?:Nesta|Neste|Nesse|Nos últimos|No pico|Nem bem|Uma mensagem|Um texto|"
                            r"Circula|Recentemente|Desde|Há alguns|Segundo|De acordo|Como sempre|Mais uma)\b")
_FIM_FRASE = re.compile(r"^(.+?[.!?])(?=\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ“\"]|\s*$)", re.S)


def _termos(t: str) -> set:
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z0-9]{4,}", t)}


def descricao_boato(reg: dict) -> str | None:
    """Primeira frase COMPLETA da descrição do boato no resumo do Boatos.org ("Boato – X."),
    ou None. Nunca corta no meio: ou a frase termina em . ! ? antes da próxima, ou é descartada."""
    t = (reg.get("trecho") or "").strip()
    m = re.match(r"^(?:Boato|Hoax)\s*[–—-]\s*(.+)$", t, re.S)
    if not m:
        return None
    corpo = re.split(r"…\s*Continue a ler|\.\.\.\s*Continue a ler", m.group(1))[0].strip()
    f = _FIM_FRASE.match(corpo)
    if not f:
        return None
    frase = re.sub(r"\s+", " ", f.group(1)).strip()
    if not TAM_DESCRICAO[0] <= len(frase) <= TAM_DESCRICAO[1] or "…" in frase or _EMENDA_ARTIGO.search(frase):
        return None
    # tem de falar do mesmo boato do título (senão é o começo do artigo, não a descrição)
    if len(_termos(frase) & _termos(reg.get("titulo") or "")) < 2:
        return None
    return frase


def emoldurar(texto: str, chave: str) -> tuple:
    """-> (mensagem, índice da moldura). Determinístico pela chave."""
    t = texto.strip()
    opcoes = MOLDURAS_PERGUNTA if t.endswith("?") else range(len(MOLDURAS))
    opcoes = list(opcoes)
    i = opcoes[int(_ordem("moldura:" + chave), 16) % len(opcoes)]
    if i and not re.search(r"[.!?…]$", t):
        t += "."
    return MOLDURAS[i].format(t=t), i


def _casos_eval():
    for arq in ("casos.jsonl", "casos_claimreview.jsonl"):
        for linha in (RAIZ / "eval" / arq).read_text(encoding="utf-8").splitlines():
            if linha.strip() and not linha.startswith("#"):
                c = json.loads(linha)
                if c.get("split", "dev") == "dev" and c["entrada"].get("tipo") != "link":
                    yield c


def gerar(data_ref: date) -> list:
    excl = carregar_excluidas()
    base = [r for r in carregar_jsonl(RAIZ / "factcheck_mvp" / "data" / "checagens.jsonl")
            if chave_url(r.get("url", "")) not in excl]
    frases, usadas = [], set()

    def add(cat, texto, rotulo, origem, agencia=None, data=None, url=None, nota="", revisao=None):
        msg, moldura = emoldurar(texto, url or origem + texto)
        frases.append({"id": f"{cat}:{len([f for f in frases if f['categoria'] == cat])}",
                       "categoria": cat, "texto": msg, "afirmacao": texto.strip(), "moldura": moldura,
                       "rotulo": rotulo, "origem": origem,
                       "agencia": agencia, "data": (data or "")[:10] or None, "url_checagem": url, "nota": nota,
                       # eval = rótulo escrito à mão; base = selo da agência + título inequívoco
                       "revisado_por_humano": origem.startswith("eval:"),
                       "revisao": revisao or ("caso do eval (rótulo escrito à mão)" if origem.startswith("eval:")
                                              else "automática: selo da agência + título inequívoco")})
        if url:
            usadas.add(chave_url(url))

    # casos do eval (rótulo escrito à mão, com nota)
    for c in _casos_eval():
        tags = c.get("tags") or []
        cat = ("fora_da_base" if "fora_do_indice" in tags else
               "checadas_falsas" if c["rotulo"] in ("falso", "enganoso") else
               "verdadeiras" if c["rotulo"] == "verdadeiro" else None)
        if cat:
            urls = c.get("urls_checagem") or []
            add(cat, c["entrada"]["conteudo"], c["rotulo"], f"eval:{c['id']}",
                url=urls[0] if urls else None, nota=(c.get("nota") or "")[:200])

    um_ano = (data_ref - timedelta(days=365)).isoformat()
    verdadeiras = sorted((dict(r, afirmacao_checada=_limpa(r.get("afirmacao_checada"))) for r in base
                          if r.get("veredito") == "VERDADEIRO" and _TITULO_VERDADEIRA.search(r.get("titulo") or "")),
                         key=lambda r: _ordem(r["url"]))
    verdadeiras = [r for r in verdadeiras if not _ruim(r["afirmacao_checada"])]
    antigas = [r for r in verdadeiras if (r.get("data_pub") or "9")[:10] < um_ano]
    for i, r in enumerate(antigas[:N_ANTIGAS]):
        af = r["afirmacao_checada"].strip()
        texto = _PREFIXOS_TEMPO[i % 3] + af  # sem forçar minúscula: "Bolsonaro", "TIM" são nomes
        add("antigas_como_atuais", texto, "enganoso", f"base:{r['agencia']}", r["agencia"], r.get("data_pub"),
            r["url"], nota=f"Fato confirmado pela agência em {(r.get('data_pub') or '')[:10]}, reescrito como atual.")
    por_url = {chave_url(r["url"]): r for r in base}
    for url, texto in VERDADEIRAS_REVISADAS:
        r = por_url.get(chave_url(url))
        if r and r.get("veredito") == "VERDADEIRO" and chave_url(url) not in usadas:
            add("verdadeiras", texto, "verdadeiro", f"base:{r['agencia']}", r["agencia"], r.get("data_pub"), r["url"],
                nota=f"Selo VERDADEIRO ({r.get('selo_original')}) da agência; título: {r['titulo'][:120]}",
                revisao="manual (Claude, 09/10/2026): alegação extraída do título e conferida com o trecho")
    for r in verdadeiras:
        if chave_url(r["url"]) not in usadas:
            add("verdadeiras", r["afirmacao_checada"], "verdadeiro", f"base:{r['agencia']}", r["agencia"],
                r.get("data_pub"), r["url"], nota=f"Selo VERDADEIRO ({r.get('selo_original')}) da agência.")

    # descrições completas de boatos (Boatos.org): o texto mais parecido com o que circula
    descricoes = sorted(((r, descricao_boato(r)) for r in base
                         if r.get("agencia") == "boatos" and r.get("veredito") == "FALSO"), key=lambda x: _ordem(x[0]["url"]))
    for r, frase in [x for x in descricoes if x[1]][:MAX_DESCRICOES_BOATOS]:
        add("checadas_falsas", frase, "falso", "base:boatos", "boatos", r.get("data_pub"), r["url"],
            nota="Descrição do boato pelo Boatos.org (selo FALSO); primeira frase completa do resumo.")

    falsas = sorted((dict(r, afirmacao_checada=_limpa(r.get("afirmacao_checada"))) for r in base
                     if r.get("veredito") in ("FALSO", "ENGANOSO") and _TITULO_FALSA.search(r.get("titulo") or "")),
                    key=lambda r: _ordem(r["url"]))
    falsas = [r for r in falsas if not _ruim(r["afirmacao_checada"]) and chave_url(r["url"]) not in usadas]
    for r in falsas[:MAX_FALSAS_DA_BASE]:
        add("checadas_falsas", r["afirmacao_checada"], r["veredito"].lower(), f"base:{r['agencia']}",
            r["agencia"], r.get("data_pub"), r["url"], nota=f"Selo {r['veredito']} ({r.get('selo_original')}) da agência.")
    return frases


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gera o conjunto rotulado da simulação de usuário")
    ap.add_argument("--data-ref", default=date.today().isoformat(), help="data de referência (AAAA-MM-DD)")
    a = ap.parse_args(argv)
    frases = gerar(date.fromisoformat(a.data_ref))
    SAIDA.write_text(json.dumps({"gerado_em": a.data_ref, "frases": frases}, ensure_ascii=False, indent=1) + "\n",
                     encoding="utf-8")
    from collections import Counter
    print(f"ok: {len(frases)} frases -> {SAIDA}")
    print(dict(Counter(f["categoria"] for f in frases)))
    tam = sorted(len(f["texto"]) for f in frases)
    print(f"tamanho das mensagens: mediana {tam[len(tam) // 2]} · mín {tam[0]} · máx {tam[-1]} caracteres")
    return 0


if __name__ == "__main__":
    sys.exit(main())
