"""Quebra do input em afirmações verificáveis (JSON), preservando idioma e polaridade.

Saída do LLM (validada com Pydantic, 1 retry com correção):
    [{"afirmacao": str, "nucleo": str, "polaridade": "afirma"|"nega", "consulta": str}]

- `afirmacao`: o que o USUÁRIO afirma, em português, com a polaridade dele
  ("É falso que a vacina altera o DNA" → "A vacina não altera o DNA").
- `nucleo`: a alegação na forma afirmativa ("A vacina altera o DNA"): é o que o
  juiz e os selos avaliam; `polaridade="nega"` faz a decisão inverter a direção.
- `consulta`: 5-8 palavras-chave em PT para a busca ("vacina covid altera DNA").

Nunca devolve vazio para entrada válida: CAIXA ALTA e pontuação excessiva são
normalizadas antes; LLM vazio/inválido → fallback determinístico, marcado como
`fallback-regex` (+ `telemetria.fallback`). Números iniciais são preservados
("30 mil" continua "30 mil").
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Literal, Optional

from pydantic import BaseModel, TypeAdapter, field_validator

from . import config, llm, telemetria
from .schemas import Afirmacao

# ----------------------------------------------------------------------------- normalização
_APELOS = re.compile(
    r"\b(compartilhe(m)?( já| agora| com todos)?|repasse(m)?( já)?|encaminhe(m)?|urgente|"
    r"não deixe de compartilhar|divulgue(m)?|espalhe(m)?)\b", re.I)
_SAUDACAO = re.compile(r"\b(bom dia|boa tarde|boa noite|olá|oi pessoal|inscreva-se)\b", re.I)
_MOLDURA_NEGA = re.compile(
    r"^\s*(?:(?:é|e)\s+(?:falso|falsa|mentira|fake|boato)|n[ãa]o\s+(?:é|e)\s+(?:verdade|fato|real)|"
    r"(?:é|e)\s+enganoso|desmentido\s*:|falso\s*:|fake\s*:)\s*(?:que\s+)?(.+)$", re.I | re.S)
_PERGUNTA = re.compile(r"^\s*(?:é\s+verdade\s+que|e\s+verdade\s+que|será\s+que|procede\s+que)\s+(.+?)\??\s*$",
                       re.I | re.S)

_STOP_PT = frozenset("""a o os as um uma uns umas de do da dos das em no na nos nas por pelo pela pelos pelas
para pra com sem que se e ou mas como mais menos foi eram será são é está estão esta este isto isso esse
essa aquele aquela muito tão já ainda também pode podem tem têm há ser ter seu sua seus suas ao aos à às
não sim sobre entre quando onde qual quais quem todo toda todos todas vai vão diz dizem afirma afirmam
""".split())
_STOP_EN = frozenset("the was were is are of and to in that for with has have been by on it this".split())


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def normalizar_entrada(texto: str) -> str:
    """CAIXA ALTA → caixa de frase; '!!!'/'???' → um sinal; apelos removidos; espaços colapsados."""
    t = re.sub(r"\s+", " ", (texto or "")).strip()
    t = re.sub(r"([!?.,;:])[!?.,;:]+", r"\1", t)
    letras = [c for c in t if c.isalpha()]
    if len(letras) >= 8 and sum(c.isupper() for c in letras) / len(letras) >= 0.7:
        t = t.lower()
        t = re.sub(r"(^|[.!?]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), t)
    t = _APELOS.sub(" ", t)
    t = _SAUDACAO.sub(" ", t)
    t = re.sub(r"\s+([!?.,;:])", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip(" ,;:-–—")
    t = re.sub(r"^[!?.,;:]+\s*", "", t)
    return t.strip()


def _cap(s: str) -> str:
    s = (s or "").strip()
    return (s[0].upper() + s[1:]) if s else s


def palavras_chave(texto: str, n: int = 8) -> str:
    """Consulta determinística: tokens de conteúdo (números preservados), em ordem."""
    toks = re.findall(r"[\wÀ-ÿ]+", texto or "")
    out: List[str] = []
    for t in toks:
        tl = t.lower()
        if tl in _STOP_PT or (len(tl) < 3 and not tl.isdigit()):
            continue
        if tl in (x.lower() for x in out):
            continue
        out.append(t)
        if len(out) >= n:
            break
    return " ".join(out)


def _parece_ingles(s: str) -> bool:
    toks = re.findall(r"[a-zà-ÿ]+", (s or "").lower())
    en = sum(1 for t in toks if t in _STOP_EN)
    pt = sum(1 for t in toks if t in _STOP_PT)
    return en >= 2 and en > pt


_NEGACAO = re.compile(r"\b(n[ãa]o|nunca|jamais|nem|falso|falsa|mentira|fake|boato|desmentid\w*|engan\w*)\b", re.I)


def _tirar_nao(frase: str) -> Optional[str]:
    """'Café não cura câncer' -> 'Café cura câncer' (só com exatamente um 'não')."""
    if len(re.findall(r"\bn[ãa]o\b", frase, re.I)) != 1:
        return None
    return _cap(re.sub(r"\s*\bn[ãa]o\b\s*", " ", frase, flags=re.I).strip())


def _termos_inventados(consulta: str, texto: str) -> List[str]:
    """Termos da consulta (>= 4 letras) que não aparecem no texto, se forem > 1/3 dos termos."""
    base = _sem_acento(texto).lower()
    toks = [t for t in re.findall(r"[\wÀ-ÿ]+", consulta or "") if len(t) >= 4]
    fora = [t for t in toks if _sem_acento(t).lower()[:5] not in base]
    return fora if toks and len(fora) * 3 > len(toks) else []


def _moldura_negacao(texto: str) -> Optional[str]:
    """'É falso que X' / 'Não é verdade que X' → X (núcleo afirmativo)."""
    m = _MOLDURA_NEGA.match(texto or "")
    if not m:
        return None
    x = re.sub(r"[.!?]+$", "", m.group(1)).strip()
    return _cap(x) if len(x) >= 5 else None


# ----------------------------------------------------------------------------- LLM
class _ItemLLM(BaseModel):
    afirmacao: str
    nucleo: str = ""
    polaridade: Literal["afirma", "nega"] = "afirma"
    consulta: str = ""

    @field_validator("polaridade", mode="before")
    @classmethod
    def _pol(cls, v):
        v = _sem_acento(str(v or "afirma")).strip().lower()
        return "nega" if v.startswith("neg") else "afirma"


_INSTRUCAO = """Extraia as afirmações factuais verificáveis do TEXTO (português do Brasil).

Responda SOMENTE com um array JSON, sem texto antes ou depois. Cada item:
{{"afirmacao": "...", "nucleo": "...", "polaridade": "afirma" ou "nega", "consulta": "..."}}

Regras:
1. "afirmacao": o que o TEXTO afirma, em português, com as palavras do texto, frase autocontida (sem pronomes soltos). Não acrescente fatos. NUNCA traduza para outro idioma. Preserve números e datas exatamente como estão (ex.: "30 mil", "2022").
2. Negação: se o TEXTO nega uma alegação ("É falso que X", "Não é verdade que X", "X não faz Y"), "afirmacao" mantém a negação, "nucleo" é a alegação X na forma afirmativa (sem a negação) e "polaridade" é "nega". Se não há negação, "nucleo" repete "afirmacao" e "polaridade" é "afirma".
3. Relato ("Fulano diz que X", "circula vídeo afirmando X") e pergunta ("É verdade que X?"): a afirmação verificável é X.
4. "consulta": 5 a 8 palavras-chave em português, tiradas do núcleo, para buscar o tema no Google (se o núcleo for curto, use todas as palavras importantes dele). Mantenha os acentos. Não invente termos que não estão no texto. Sem aspas, sem operadores, sem as palavras "falso", "verdade", "boato".
5. Ignore saudações, apelos ("compartilhe", "urgente") e opiniões. No máximo {n} itens. Se não houver nenhuma afirmação factual, responda [].

Exemplos:
TEXTO: "É falso que beber água gelada causa gripe"
[{{"afirmacao": "Beber água gelada não causa gripe", "nucleo": "Beber água gelada causa gripe", "polaridade": "nega", "consulta": "beber água gelada causa gripe"}}]
TEXTO: "Prefeitura do Recife abriu 30 mil vagas de emprego em 2025"
[{{"afirmacao": "A Prefeitura do Recife abriu 30 mil vagas de emprego em 2025", "nucleo": "A Prefeitura do Recife abriu 30 mil vagas de emprego em 2025", "polaridade": "afirma", "consulta": "Prefeitura Recife 30 mil vagas emprego 2025"}}]

TEXTO: \"\"\"
{texto}
\"\"\""""


_LISTA = TypeAdapter(List[_ItemLLM])


def _validar_itens(dados):
    """Aceita lista, {"afirmacoes": [...]} ou um item solto; exige português."""
    if isinstance(dados, dict):
        listas = [v for v in dados.values() if isinstance(v, list)]
        dados = listas[0] if ("afirmacao" not in dados and listas) else [dados]
    itens = _LISTA.validate_python(dados)
    ruins = [it.afirmacao for it in itens if _parece_ingles(it.afirmacao) or _parece_ingles(it.nucleo)]
    if ruins:
        raise ValueError(f"afirmações fora do português: {ruins[:2]}. Escreva em português, como no texto")
    return itens


def _via_llm(texto: str, max_n: int) -> tuple[List[Afirmacao], str]:
    res = llm.chat_json(
        [{"role": "user", "content": _INSTRUCAO.format(n=max_n, texto=texto[:4000])}],
        None, finalidade="afirmacoes", max_tokens=2500, validar=_validar_itens)
    if not res.ok:
        raise RuntimeError(res.erro or "LLM indisponível")
    saida: List[Afirmacao] = []
    moldura = _moldura_negacao(texto)
    for i, it in enumerate(res.dados[:max_n]):
        af = re.sub(r"\s+", " ", it.afirmacao).strip()
        if len(af) < 5:
            continue
        nucleo = re.sub(r"\s+", " ", it.nucleo or af).strip()
        pol = it.polaridade
        # Moldura explícita de negação no texto do usuário é determinística: vence o LLM
        # (o LLM local já removeu "É falso que" e passou a checar o próprio boato).
        if moldura and i == 0:
            pol, nucleo = "nega", moldura
            if not _NEGACAO.search(af):  # o LLM tirou a negação: a afirmação é a do usuário
                af = re.sub(r"[.!?]+$", "", texto.strip())
        elif pol == "nega" and not _NEGACAO.search(texto):
            # Iteração 1: "Vacina da covid altera o DNA humano" veio com polaridade "nega" -> sem
            # nenhuma negação no texto do usuário, a polaridade é "afirma" (senão a direção inverte).
            telemetria.fallback("afirmacoes.polaridade", "LLM marcou 'nega' sem negação no texto: afirma")
            pol, nucleo = "afirma", af
        elif pol == "nega" and _sem_acento(nucleo).lower().strip(" .") == _sem_acento(af).lower().strip(" ."):
            # núcleo não veio na forma afirmativa: deriva; se não der, julga o texto do usuário como está
            derivado = _moldura_negacao(af) or _tirar_nao(af)
            telemetria.fallback("afirmacoes.polaridade", "núcleo igual à afirmação negada: "
                                + ("derivado por regra" if derivado else "julgada como está (afirma)"))
            pol, nucleo = ("nega", derivado) if derivado else ("afirma", af)
        if pol == "afirma":
            nucleo = nucleo if nucleo and not _parece_ingles(nucleo) else af
        consulta = (it.consulta or "").strip() or palavras_chave(nucleo)
        if len(consulta.split()) > 10:
            consulta = palavras_chave(consulta, 8)
        # A consulta é o TEMA: sem palavras de veredito (senão a busca vira "falso vacina…")
        consulta = re.sub(r"(?i)\b(falso|falsa|verdade|verdadeiro|boato|fake|mentira|checagem)\b", " ", consulta)
        consulta = re.sub(r"\s+", " ", consulta).strip() or palavras_chave(nucleo)
        inventadas = _termos_inventados(consulta, f"{texto} {nucleo}")
        if inventadas:
            # Iteração 1: "Café cura câncer" -> "cafe cura cancer onco terapia" levou a busca a Google Books.
            telemetria.fallback("afirmacoes.consulta", f"consulta do LLM com termos fora do texto {inventadas}: "
                                "palavras-chave do núcleo", consulta_llm=consulta)
            consulta = palavras_chave(nucleo)
        saida.append(Afirmacao(texto=af[:500], indice=len(saida), nucleo=nucleo[:500],
                               polaridade=pol, consulta=consulta[:200]))
    return saida, f"{res.motor}:{res.modelo}"


# ----------------------------------------------------------------------------- fallback
def _fallback(texto: str, max_n: int) -> List[Afirmacao]:
    """Determinístico: moldura de negação/pergunta + sentenças; consulta por palavras-chave."""
    t = normalizar_entrada(texto)
    if len(re.sub(r"[^\wÀ-ÿ]", "", t)) < 8 or len(palavras_chave(t).split()) < 2:
        return []  # só saudação/apelo: sem afirmação factual
    m = _PERGUNTA.match(t)
    if m:
        t = _cap(m.group(1))
    partes = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", t) if p and len(p.strip()) > 25]
    partes = [re.sub(r"[.!?]+$", "", p).strip() for p in partes] or [re.sub(r"[.!?]+$", "", t).strip()]
    saida: List[Afirmacao] = []
    for p in partes[:max_n]:
        nucleo, pol = p, "afirma"
        x = _moldura_negacao(p)
        if x:
            nucleo, pol = x, "nega"
        elif _tirar_nao(p):
            nucleo, pol = _tirar_nao(p), "nega"
        saida.append(Afirmacao(texto=p[:500], indice=len(saida), nucleo=nucleo[:500], polaridade=pol,
                               consulta=palavras_chave(nucleo)))
    return saida


def extrair_afirmacoes(texto: str, max_n: int = 0, usar_llm: bool = True) -> tuple[List[Afirmacao], str]:
    """Retorna (afirmacoes, motor). Motor honesto: `<provedor>:<modelo>` ou `fallback-regex`."""
    limite = max_n or config.MAX_AFIRMACOES
    limpo = normalizar_entrada(texto) or (texto or "").strip()
    if usar_llm:
        try:
            got, motor = _via_llm(limpo, limite)
            if got:
                return got, motor
            fb = _fallback(texto, limite)
            if fb:
                telemetria.fallback("afirmacoes", f"LLM ({motor}) devolveu vazio para entrada válida: regex")
            return fb, "fallback-regex"
        except Exception as e:
            telemetria.fallback("afirmacoes", f"{type(e).__name__}: {str(e)[:200]}")
    return _fallback(texto, limite), "fallback-regex"
