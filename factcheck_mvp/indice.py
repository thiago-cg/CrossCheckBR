"""Índice BM25 de vereditos + notícias (rank_bm25, sem dependências pesadas).

Duas APIs convivem aqui:

* **Legada** — `Indice.adicionar/buscar` sobre `ArtigoNoticia` (BM25Plus, piso
  absoluto no pipeline). Mantida enquanto o pipeline não migra.
* **Checagens** — `Indice.buscar_checagens(afirmacao, k)` sobre
  data/checagens.jsonl (gerado por `python3 -m factcheck_mvp.ingestor`):
  BM25 Okapi, tokenização PT (sem acento, stopwords, radical RSLP), campo
  `afirmacao_checada` com boost e **limiar relativo** (ver `_LIMIAR`).

O índice de checagens é um ATALHO OPCIONAL (o núcleo é a busca aberta):
`INDICE_CHECAGENS=0` no ambiente desliga — `habilitado()` volta False e
`buscar_checagens` devolve [] sem tocar no disco.
"""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

log = logging.getLogger("factcheck.indice")

try:
    from rank_bm25 import BM25Okapi, BM25Plus

    BM25_OK = True
except Exception:  # pragma: no cover
    log.warning("rank_bm25 ausente: buscas retornam vazio.")
    BM25Plus = BM25Okapi = None  # type: ignore
    BM25_OK = False

BASE_DIR = Path(__file__).resolve().parent
ARQ_CHECAGENS = BASE_DIR / "data" / "checagens.jsonl"

_STOP = frozenset(
    "a o os as um uma de do da dos das em no na nos nas por para com sem que se e ou mas como mais foi eram será são é esta este esta isto isso esse essa aquele aquela muito tão já ainda também pode podem tem têm há foi vão ser ter".split()
)


def tokenizar(texto: str) -> List[str]:
    """Tokenização LEGADA (usada por `buscar`). Não mexer: testes antigos dependem dela."""
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    toks = re.findall(r"[a-z0-9]{3,}", texto.lower())
    return [t for t in toks if t not in _STOP]


# ============================================================================ flag
def habilitado() -> bool:
    """Atalho do índice de checagens ligado? Lê `INDICE_CHECAGENS` a cada chamada
    ("1" padrão; "0"/"false"/"off"/"nao" desligam)."""
    return os.getenv("INDICE_CHECAGENS", "1").strip().lower() not in ("0", "false", "off", "nao", "não", "no")


# ============================================================================ tokenização PT
def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


# Stopwords PT embutidas (sem acento). Unidas às do NLTK quando o corpus existe.
_STOP_PT_BASE = frozenset(_sem_acento(w) for w in """
a ao aos aquela aquelas aquele aqueles aquilo as ate com como da das de dela delas dele deles depois do dos
e ela elas ele eles em entre era eram essa essas esse esses esta estas este estes eu foi fomos for foram
fosse fossem fui ha isso isto ja la lhe lhes mais mas me mesmo meu meus minha minhas muito na nas nem
no nos nossa nossas nosso nossos num numa o os ou para pela pelas pelo pelos por qual quando que quem
se sem ser seu seus so sua suas tambem te tem tinha tu tua tuas um uma voce voces vos sao estao esta
seria teria sera serao vai vao ter tambem ainda pode podem deve devem apos sobre ate cada outro outra
outros outras todo toda todos todas nao sim qual quais onde porque pois entao assim aqui ali tudo nada
""".split())

# Palavras de "moldura" (como a pessoa conta a história, não o conteúdo) — tiradas da
# consulta E dos documentos, senão "é falso que…"/"vi no zap que…" viram match.
_MOLDURA = frozenset(_sem_acento(w) for w in """
falso falsa falsos falsas fake fakes fato fatos verdade verdadeiro verdadeira boato boatos enganoso enganosa
checagem checagens checado circula circulam circulando vi viu zap zapzap whatsapp grupo grupos mensagem
mensagens post posts postagem postagens publicacao publicacoes rede redes sociais alega alegam alegacao diz
dizem dizendo disseram afirma afirmam afirmando sabia gente pessoal compartilhe compartilhar urgente ta to
pra pro ne eh recebi mandaram mandou manda falaram falando noticia noticias veja confira real mesmo ai
""".split())


@lru_cache(maxsize=1)
def _stopwords() -> frozenset:
    extras: Iterable[str] = ()
    try:
        from nltk.corpus import stopwords as _sw

        extras = (_sem_acento(w) for w in _sw.words("portuguese"))
    except Exception:  # corpus ausente: lista embutida basta
        pass
    return frozenset(_STOP_PT_BASE) | frozenset(extras) | _MOLDURA


def _baixar_nltk(recurso: str) -> bool:
    """Tenta baixar um recurso do NLTK uma vez (com CA do certifi: o Python do
    python.org no macOS não acha a cadeia do sistema). Desligável por
    FACTCHECK_NLTK_DOWNLOAD=0."""
    if os.getenv("FACTCHECK_NLTK_DOWNLOAD", "1") == "0":
        return False
    try:
        import nltk

        try:
            import certifi

            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        except Exception:
            pass
        return bool(nltk.download(recurso, quiet=True))
    except Exception:
        return False


@lru_cache(maxsize=1)
def _stemmer():
    """RSLPStemmer (NLTK, recurso 'rslp'); None = sem radicalização (fallback)."""
    try:
        import nltk
        from nltk.stem import RSLPStemmer
    except Exception:
        return None
    try:
        nltk.data.find("stemmers/rslp")
    except LookupError:
        if not _baixar_nltk("rslp"):
            log.warning("RSLP indisponível: índice de checagens sem stemming.")
            try:
                from . import telemetria

                telemetria.fallback("indice.rslp", "recurso NLTK 'rslp' ausente: sem radicalização")
            except Exception:
                pass
            return None
    try:
        return RSLPStemmer()
    except Exception:
        return None


@lru_cache(maxsize=100_000)
def _radical(palavra: str) -> str:
    st = _stemmer()
    if st is None:
        return _sem_acento(palavra)
    try:
        return _sem_acento(st.stem(palavra)) or _sem_acento(palavra)
    except Exception:
        return _sem_acento(palavra)


def radicalizacao_ativa() -> bool:
    return _stemmer() is not None


def tokenizar_pt(texto: str, radical: bool = True) -> List[str]:
    """Minúsculas, sem acento, sem stopword/moldura, radical RSLP (sem acento).
    Números e termos com dígito (5g, h1n1) com ≥2 caracteres ficam; palavras com <3 letras saem."""
    stop = _stopwords()
    saida = []
    for w in re.findall(r"[^\W_]+", (texto or "").lower()):
        base = _sem_acento(w)
        if not base or base in stop:
            continue
        if base.isdigit():
            if len(base) >= 2:
                saida.append(base)
            continue
        if any(c.isdigit() for c in base):  # "5g", "4g", "h1n1": sem eles "5G causa câncer" vira "causa câncer"
            if len(base) >= 2:
                saida.append(base)
            continue
        if len(base) < 3:
            continue
        saida.append(_radical(w) if radical else base)
    return saida


# ============================================================================ limiar
# Limiar RELATIVO (o absoluto não serve: BM25 fora do tema no corpus real marca
# 8–22). Um registro volta só se passar TODOS:
#   1. score >= max(piso_abs, fator_mediana × mediana dos scores > 0 da consulta
#      sem o 1º, fracao_top1 × score do 1º). A mediana dos docs que casam ALGUM
#      termo mede o "ruído de 1 palavra em comum" daquela consulta.
#   2. >= min_termos termos distintos da consulta (ou todos, se a consulta tiver
#      menos) aparecem na afirmação/título do registro.
#   3. score_rel = cobertura da consulta ponderada por IDF, medida SÓ na
#      afirmação/título (não no trecho) >= cobertura_min.
# O critério 3 é o que segura os negativos difíceis: nomes próprios viram 2
# termos ("flavio", "bolsonar") e casam com qualquer checagem da mesma pessoa;
# exigir que ~60% do peso IDF da consulta esteja na afirmação corta isso.
# Mini-benchmark (scripts/bench_indice_checagens.py, 28/09/2026, 334 checagens;
# 18 paráfrases no_indice + 80 negativos = 59 fora_do_indice + 21 fora do tema):
#   sem limiar (score>0) ........ recall@1 0,94  recall@3 0,94  FP 98,8%
#   cobertura_min 0,35 .......... recall@1 0,94  recall@3 0,94  FP 42,5%
#   cobertura_min 0,50 .......... recall@1 0,94  recall@3 0,94  FP 15,0%
#   ESCOLHIDO (0,60, 3 termos) .. recall@1 0,83  recall@3 0,83  FP  2,5%
# Preferimos precisão: um atalho errado entra como "caminho feliz" (peso alto);
# um atalho perdido só devolve a consulta para a busca aberta.
#
# 07/10/2026 — índice com o histórico da Lupa e do Boatos.org (21.579 checagens): com 65×
# mais textos "quase iguais", o FP subiu para 13,2% (r@3 0,86, 22 positivos). Critério 4,
# "números batem": se a consulta tem números, ≥1 deles precisa estar na afirmação/título
# ("Janja gastou R$ 7 bilhões" não casa com "R$ 62 milhões"; "salário de 3 mil" não casa
# com "acabar com o salário mínimo"). Resultado: FP 7,9%, r@1 0,77 e r@3 0,86 iguais.
_LIMIAR = {
    "piso_abs": 4.0,
    "fator_mediana": 2.0,
    "fracao_top1": 0.5,
    "min_termos": 3,
    "cobertura_min": 0.6,
    "exigir_numeros": True,
}
# Só números soltos ("7 bilhões", "15%", "R$ 3 mil"); colados a letras/hífen não contam
# ("CR7", "5G", "Covid-19", "H1N1" são nomes, não quantidades).
_RE_NUMERO = re.compile(r"(?<![\w-])\d+(?![^\W\d_])")
_BOOST_AFIRMACAO = 3  # repetição de tokens = boost de campo "pobre" (sem BM25F)
_BOOST_TITULO = 2
_MAX_TOKENS_TRECHO = 80


def checagem_como_doc(reg: Dict[str, Any]) -> Dict[str, Any]:
    """Registro de checagens.jsonl -> formato de doc LEGADO do pipeline (`h["doc"]`)."""
    return {
        "url": reg.get("url", ""),
        "titulo": reg.get("titulo") or reg.get("afirmacao_checada") or "",
        "corpo_texto": reg.get("trecho") or "",
        "fonte": {"id": reg.get("agencia"), "nome": reg.get("agencia_nome") or reg.get("agencia") or ""},
        "tipo_conteudo": "checagem",
        "veredito": reg.get("veredito"),
        "selo_original": reg.get("selo_original"),
        "data_publicacao": reg.get("data_pub"),
        "afirmacao_checada": reg.get("afirmacao_checada"),
        "origem_veredito": reg.get("origem"),
    }


class Indice:
    """Corpus de ArtigosNoticia (legado) + checagens (novo), consultáveis por texto. RF04/RF05."""

    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []
        self._bm25 = None
        self._matriz: List[List[str]] = []
        # --- checagens (novo)
        self.checagens: List[Dict[str, Any]] = []
        self._checagens_carregadas = False  # False -> buscar_checagens carrega o padrão sob demanda
        self._bm25_chk = None
        self._campos_chk: List[set] = []   # tokens de afirmacao+titulo (p/ sobreposição)
        self._todos_chk: List[set] = []    # tokens de todos os campos (p/ cobertura)

    # ------------------------------------------------------------------ legado
    def adicionar(self, doc: Dict[str, Any]) -> None:
        self.docs.append(doc)
        self._bm25 = None  # invalida; reconstrói sob demanda

    def _garantir(self) -> None:
        if self._bm25 is not None or not self.docs:
            return
        self._matriz = [tokenizar(f"{d.get('titulo','')} {d.get('corpo_texto','') or ''}") for d in self.docs]
        if BM25_OK:
            # BM25Plus (não Okapi): evita IDF zerado em corpus pequenos —
            # no Okapi, termo em metade dos docs zera o score.
            self._bm25 = BM25Plus(self._matriz)

    def __len__(self) -> int:
        return len(self.docs)

    def buscar(self, consulta: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Retorna [{doc, score}] ordenado. Score BM25 bruto >= 0."""
        self._garantir()
        if not self.docs or not BM25_OK or self._bm25 is None:
            return []
        toks = tokenizar(consulta)
        if not toks:
            return []
        scores = self._bm25.get_scores(toks)
        ordem = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        saida = []
        for i in ordem[:top_k]:
            if scores[i] <= 0:
                continue
            saida.append({"doc": self.docs[i], "score": float(scores[i])})
        return saida

    @classmethod
    def de_artigos(cls, artigos: List[Dict[str, Any]]) -> "Indice":
        idx = cls()
        for a in artigos:
            idx.adicionar(a)
        return idx

    # ------------------------------------------------------------------ checagens
    @classmethod
    def de_checagens(cls, fonte: Union[None, str, Path, List[Dict[str, Any]]] = None) -> "Indice":
        """Índice só com checagens: `fonte` = caminho do .jsonl (padrão data/checagens.jsonl)
        ou lista de registros."""
        idx = cls()
        idx.carregar_checagens(fonte)
        return idx

    def carregar_checagens(self, fonte: Union[None, str, Path, List[Dict[str, Any]]] = None) -> int:
        """Carrega (substitui) as checagens. Arquivo ausente/ilegível -> 0 registros, sem erro."""
        if isinstance(fonte, list):
            regs = fonte
        else:
            caminho = Path(fonte) if fonte else ARQ_CHECAGENS
            regs = []
            try:
                for linha in caminho.read_text(encoding="utf-8").splitlines():
                    linha = linha.strip()
                    if not linha:
                        continue
                    try:
                        regs.append(json.loads(linha))
                    except ValueError:
                        log.warning("linha inválida em %s", caminho)
            except OSError as e:
                log.warning("checagens indisponíveis (%s): %s", caminho, e)
        self.checagens = [r for r in regs if isinstance(r, dict) and (r.get("afirmacao_checada") or r.get("titulo"))]
        self._checagens_carregadas = True
        self._bm25_chk = None
        return len(self.checagens)

    def adicionar_checagem(self, reg: Dict[str, Any]) -> None:
        self._checagens_carregadas = True
        self.checagens.append(reg)
        self._bm25_chk = None

    def _garantir_checagens(self) -> None:
        if not self._checagens_carregadas:
            self.carregar_checagens()
        if self._bm25_chk is not None or not self.checagens or not BM25_OK:
            return
        matriz = []
        self._campos_chk, self._todos_chk = [], []
        for r in self.checagens:
            af = tokenizar_pt(r.get("afirmacao_checada") or "")
            extras = " ".join(r.get("afirmacoes_extras") or [])
            tit = tokenizar_pt(r.get("titulo") or "")
            tre = tokenizar_pt(f"{extras} {r.get('trecho') or ''}")[:_MAX_TOKENS_TRECHO]
            matriz.append(af * _BOOST_AFIRMACAO + tit * _BOOST_TITULO + tre)
            self._campos_chk.append(set(af) | set(tit) | set(tokenizar_pt(extras)))
            self._todos_chk.append(set(af) | set(tit) | set(tre))
        # Okapi (não Plus): o Plus soma delta·idf a TODO doc e desmonta o limiar relativo.
        self._bm25_chk = BM25Okapi(matriz)

    def buscar_checagens(self, afirmacao: str, k: int = 5,
                         limiar: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
        """Checagens sobre a afirmação, acima do limiar relativo (ver `_LIMIAR`).

        Retorna até k registros (campos do checagens.jsonl + `score` BM25 bruto +
        `score_rel` = cobertura da consulta ponderada por IDF, em [0, 1] +
        `termos_casados`). Lista vazia = nenhuma checagem confiável (inclusive com
        o atalho desligado por INDICE_CHECAGENS=0)."""
        if not habilitado():
            return []
        self._garantir_checagens()
        if not self.checagens or self._bm25_chk is None:
            return []
        lim = {**_LIMIAR, **(limiar or {})}
        q = list(dict.fromkeys(tokenizar_pt(afirmacao)))
        if not q:
            return []
        nums_q = set(_RE_NUMERO.findall(afirmacao or "")) if lim.get("exigir_numeros") else set()
        scores = self._bm25_chk.get_scores(q)
        positivos = sorted((s for s in scores if s > 0), reverse=True)
        if not positivos:
            return []
        top1 = positivos[0]
        # ruído = mediana dos que casam algo, SEM o 1º (senão, com poucos docs
        # casando, a própria checagem certa puxa a mediana para cima)
        mediana = statistics.median(positivos[1:]) if len(positivos) > 1 else 0.0
        corte = max(lim["piso_abs"], lim["fator_mediana"] * mediana, lim["fracao_top1"] * top1)
        idf = self._bm25_chk.idf
        peso_q = {t: max(idf.get(t, 0.0), 0.0) for t in q}
        soma_q = sum(peso_q.values()) or 1.0
        saida = []
        for i in sorted(range(len(scores)), key=lambda j: scores[j], reverse=True):
            s = float(scores[i])
            if s < corte:
                break
            casados = [t for t in q if t in self._campos_chk[i]]
            if len(casados) < min(lim["min_termos"], len(q)):
                continue
            cobertura = sum(peso_q[t] for t in q if t in self._campos_chk[i]) / soma_q
            if cobertura < lim["cobertura_min"]:
                continue
            if nums_q:
                reg = self.checagens[i]
                if not nums_q & set(_RE_NUMERO.findall(f"{reg.get('afirmacao_checada') or ''} "
                                                       f"{reg.get('titulo') or ''}")):
                    continue
            saida.append({**self.checagens[i], "score": round(s, 4), "score_rel": round(cobertura, 4),
                          "termos_casados": casados})
            if len(saida) >= k:
                break
        return saida
