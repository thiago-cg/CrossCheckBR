"""LLM-juiz de 4 classes com citação verificada (fase 2; sem resumidor intermediário).

Uma chamada por lote de itens estruturados, por afirmação:

    {i, veiculo, dominio, tipo_fonte, data, titulo, veredito_pagina?, trecho}

`trecho` = título + corpo (até JUIZ_TRECHO_MAX chars), priorizando as frases com
termos da afirmação. O juiz classifica a postura da PÁGINA frente ao NÚCLEO da
afirmação (forma afirmativa; a polaridade do usuário é aplicada depois, na
decisão) em SUSTENTA | REFUTA | RELATA_SEM_ENDOSSO | NAO_TRATA e copia uma
citação literal. O código confere a citação no texto que o juiz viu
(match normalizado/fuzzy >= JUIZ_CITACAO_MIN) e rebaixa para NAO_TRATA se ela não
existir. Relevância é derivada da classe. JSON validado com Pydantic, 1 retry
com mensagem de correção (llm.chat_json).

Nunca levanta. Sem LLM: cada item volta com `classe=None` e
`motor="fallback-sem-juiz"` — a decisão ignora (nível indeterminada).
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator

from . import config, llm

log = logging.getLogger("factcheck.juiz")

CLASSES = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO", "NAO_TRATA")
COM_POSTURA = ("SUSTENTA", "REFUTA")
TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")
MOTOR_FALLBACK = "fallback-sem-juiz"

_STOP = frozenset("""a o os as um uma de do da dos das em no na nos nas por pelo pela para com sem que se
e ou mas como mais foi sao ser ter tem esta este isso essa ao aos nao sim sobre entre quando todo toda
""".split())


# ----------------------------------------------------------------------------- texto
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def termos(texto: str) -> set:
    return {t for t in _norm(texto).split() if len(t) >= 4 and t not in _STOP}


def montar_trecho(titulo: str, corpo: str, afirmacao: str, limite: int = 0) -> str:
    """Título + corpo até `limite` chars. Corpo longo: lide (2 frases) + frases com mais
    termos da afirmação, na ordem original, separadas por ' … '."""
    limite = limite or config.JUIZ_TRECHO_MAX or 3000
    titulo = re.sub(r"\s+", " ", titulo or "").strip()
    corpo = re.sub(r"\s+", " ", corpo or "").strip()
    if corpo.startswith(titulo) and titulo:
        corpo = corpo[len(titulo):].strip()
    cabeca = (titulo + "\n") if titulo else ""
    resto = max(0, limite - len(cabeca))
    if len(corpo) <= resto:
        return (cabeca + corpo).strip()
    frases = [f for f in re.split(r"(?<=[.!?])\s+", corpo) if f]
    alvo = termos(afirmacao)
    pontos = [(len(alvo & termos(f)), -i, i) for i, f in enumerate(frases)]
    escolhidas = {0, 1} & set(range(len(frases)))
    usado = sum(len(frases[i]) + 3 for i in escolhidas)
    for p, _, i in sorted(pontos, reverse=True):
        if i in escolhidas or p == 0:
            continue
        if usado + len(frases[i]) + 3 > resto:
            continue
        escolhidas.add(i)
        usado += len(frases[i]) + 3
    partes, ant = [], -1
    for i in sorted(escolhidas):
        if ant >= 0 and i != ant + 1:
            partes.append("…")
        partes.append(frases[i])
        ant = i
    return (cabeca + " ".join(partes))[:limite].strip()


def verificar_citacao(citacao: str, texto: str) -> float:
    """Score em [0,1] de a citação existir no texto. Citação com reticências ("A ... B"):
    cada pedaço com >= 3 palavras é conferido e vale o pior. Ver `_score_trecho`."""
    partes = [p for p in re.split(r"\.\.\.|…|\[\.\.\.\]", citacao or "") if len(_norm(p).split()) >= 3]
    if not partes:
        return 0.0
    return min(_score_trecho(p, texto) for p in partes)


def _score_trecho(citacao: str, texto: str) -> float:
    """1.0 = substring normalizada; senão, melhor razão (SequenceMatcher) contra janelas
    do texto de mesmo tamanho."""
    c, t = _norm(citacao), _norm(texto)
    toks_c = c.split()
    if len(toks_c) < 3 or not t:
        return 0.0
    if c in t:
        return 1.0
    toks_t = t.split()
    n = len(toks_c)
    ancoras = set(toks_c[:3]) | set(toks_c[-3:])
    melhor = 0.0
    for i, tok in enumerate(toks_t):
        if tok not in ancoras:
            continue
        for ini in (i, i - n + 1):
            for tam in (n - 1, n, n + 1):
                a, b = max(0, ini), max(0, ini) + max(1, tam)
                if a >= len(toks_t):
                    continue
                r = SequenceMatcher(None, c, " ".join(toks_t[a:b]), autojunk=False).ratio()
                if r > melhor:
                    melhor = r
                    if melhor >= 0.999:
                        return 1.0
    return round(melhor, 4)


# ----------------------------------------------------------------------------- schema
class ItemJuiz(BaseModel):
    i: int
    pagina_diz: str = ""
    # Opcional: item sem classe vira "não julgado" em vez de derrubar o lote inteiro
    classe: Optional[Literal["SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO", "NAO_TRATA"]] = None
    citacao: str = ""

    @field_validator("classe", mode="before")
    @classmethod
    def _classe(cls, v):
        if v is None or str(v).strip() == "":
            return None
        k = _norm(str(v or "")).upper().replace(" ", "_")
        if k.startswith("RELATA"):
            return "RELATA_SEM_ENDOSSO"
        if k in ("NAO_TRATA", "NAO_TRATA_DO_TEMA", "FORA_DO_TEMA", "NAO"):
            return "NAO_TRATA"
        return k

    @field_validator("citacao", "pagina_diz", mode="before")
    @classmethod
    def _cit(cls, v):
        return "" if v is None else str(v)


class RespostaJuiz(BaseModel):
    itens: List[ItemJuiz]

    @model_validator(mode="before")
    @classmethod
    def _lista(cls, v):
        if isinstance(v, list):
            return {"itens": v}
        if isinstance(v, dict) and "itens" not in v:
            listas = [x for x in v.values() if isinstance(x, list)]
            if listas:
                return {"itens": listas[0]}
            if "classe" in v:
                return {"itens": [v]}
        return v


INSTRUCAO = """AFIRMAÇÃO A VERIFICAR: "{afirmacao}"

Abaixo há {n} itens (páginas encontradas numa busca). Para CADA item, leia o título e o trecho e diga o que a PÁGINA afirma sobre a AFIRMAÇÃO, escolhendo exatamente uma classe:
- SUSTENTA: a página afirma, com a própria voz, a MESMA coisa que a AFIRMAÇÃO (mesmo sujeito, mesma ação, mesmo efeito), ou traz fato que a confirma diretamente.
- REFUTA: a página afirma, com a própria voz, que a AFIRMAÇÃO é falsa, enganosa ou sem comprovação (desmente, contesta, classifica como falsa). Uma checagem que diz "é falso que ..." sobre esta afirmação REFUTA.
- RELATA_SEM_ENDOSSO: a página trata desta afirmação, ou de uma afirmação parecida mas diferente, ou relata que alguém a fez, sem dizer se ela é verdadeira ou falsa.
- NAO_TRATA: a página não trata desta afirmação (outro assunto, ou só palavras parecidas).

Regras:
1. Julgue só pelo título e pelo trecho do item; não use conhecimento próprio.
2. Parecido não é igual: "reduz o risco de" não é "cura"; "pode estar associado a" não é "causa"; estudo em células ou animais não é efeito comprovado em pessoas; outra vacina, outra data ou outro lugar não é a mesma afirmação. Nesses casos use RELATA_SEM_ENDOSSO.
3. Página que cita a AFIRMAÇÃO como exemplo de boato, mentira ou desinformação nunca SUSTENTA: é REFUTA se diz que ela é falsa, senão RELATA_SEM_ENDOSSO.
4. "veredito_pagina", quando existe, é o selo que a própria página deu à alegação que ela checou; considere-o só se essa alegação for a mesma da AFIRMAÇÃO.
5. "pagina_diz": uma frase curta, em português, com o que a página afirma sobre o assunto.
6. "citacao": copie LITERALMENTE, sem mudar nenhuma palavra e sem usar reticências, um trecho contínuo e curto (10 a 30 palavras) do título ou do trecho do item que justifique a classe. Não parafraseie e não traduza. Para NAO_TRATA use "".
7. Responda SOMENTE com JSON, em português, neste formato, com um objeto por item, na ordem:
{{"itens": [{{"i": 0, "pagina_diz": "<frase curta>", "classe": "<SUSTENTA|REFUTA|RELATA_SEM_ENDOSSO|NAO_TRATA>", "citacao": "<trecho copiado do item>"}}]}}

ITENS:
{itens}"""


def _item_prompt(i: int, it: Dict[str, Any]) -> Dict[str, Any]:
    d = {"i": i, "veiculo": it.get("veiculo") or "", "dominio": it.get("dominio") or "",
         "tipo_fonte": it.get("tipo_fonte") or "", "data": it.get("data") or "",
         "titulo": it.get("titulo") or ""}
    if it.get("veredito_pagina"):
        d["veredito_pagina"] = it["veredito_pagina"]
    d["trecho"] = it.get("trecho") or ""
    return d


def _vazio(motivo: str, motor: str = MOTOR_FALLBACK) -> Dict[str, Any]:
    return {"classe": None, "citacao": "", "citacao_score": None, "citacao_verificada": None,
            "rebaixado": False, "motor": motor, "modelo": "", "erro": motivo}


def julgar_lote(afirmacao: str, itens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Julga até JUIZ_LOTE itens numa chamada. Cada item precisa de `trecho` (texto que o
    juiz verá e contra o qual a citação é conferida). Retorna, por item, na ordem:
    {classe, citacao, citacao_score, citacao_verificada, rebaixado, motor, modelo, erro}."""
    if not itens:
        return []
    n = len(itens)
    linhas = "\n".join(json.dumps(_item_prompt(i, it), ensure_ascii=False) for i, it in enumerate(itens))
    prompt = INSTRUCAO.format(afirmacao=(afirmacao or "").strip()[:500], n=n, itens=linhas)

    def _cobertura(r: RespostaJuiz):
        idx = {x.i for x in r.itens if 0 <= x.i < n and x.classe is not None}
        if len(idx) < max(1, (n + 1) // 2):
            faltam = [i for i in range(n) if i not in idx]
            raise ValueError(f"faltaram os itens (ou a classe deles) {faltam}; devolva um objeto com "
                             f"\"classe\" para cada i de 0 a {n - 1}")
        return r

    res = llm.chat_json([{"role": "user", "content": prompt}], RespostaJuiz, finalidade="juiz",
                        max_tokens=config.JUIZ_MAX_TOKENS or 4000, validar=_cobertura)
    if not res.ok:
        log.warning("juiz falhou: %s", (res.erro or "")[:150])
        return [_vazio(res.erro or "LLM indisponível") for _ in itens]
    por_i: Dict[int, ItemJuiz] = {}
    for x in res.dados.itens:
        if 0 <= x.i < n and x.i not in por_i and x.classe is not None:
            por_i[x.i] = x
    motor = f"llm-juiz:{res.motor}"
    minimo = config.JUIZ_CITACAO_MIN or 0.9
    saida = []
    for i, it in enumerate(itens):
        x = por_i.get(i)
        if x is None:
            saida.append(_vazio("juiz não devolveu este item", motor="fallback-juiz-item"))
            continue
        out = {"classe": x.classe, "citacao": x.citacao.strip()[:400], "pagina_diz": x.pagina_diz[:300],
               "citacao_score": None,
               "citacao_verificada": None, "rebaixado": False, "motor": motor, "modelo": res.modelo,
               "erro": None, "classe_original": x.classe}
        if x.classe in TRATA:
            texto_visto = f"{it.get('titulo') or ''}\n{it.get('trecho') or ''}"
            sc = verificar_citacao(x.citacao, texto_visto)
            out["citacao_score"] = sc
            out["citacao_verificada"] = sc >= minimo
            if sc < minimo:
                out["classe"] = "NAO_TRATA"
                out["rebaixado"] = True
                out["erro"] = f"citação não encontrada no texto da fonte (score {sc:.2f} < {minimo})"
        saida.append(out)
    return saida


def julgar(afirmacao: str, itens: List[Dict[str, Any]], lote: int = 0) -> List[Dict[str, Any]]:
    """`julgar_lote` em lotes de JUIZ_LOTE (sequencial). Mesma saída, na ordem."""
    lote = max(1, lote or config.JUIZ_LOTE or 5)
    saida: List[Dict[str, Any]] = []
    for k in range(0, len(itens), lote):
        saida += julgar_lote(afirmacao, itens[k:k + lote])
    return saida


def postura_para_relevante(classe: Optional[str]) -> Optional[bool]:
    if classe is None:
        return None
    return classe in TRATA
