"""Núcleo de decisão: UMA função pura `decidir(evidencias) -> Decisao`.

Este módulo é o juiz-agregador determinístico sobre os outputs do avaliador 1:1
(`avaliador.avaliar`, manchete+corpo, 1 chamada por par peça×afirmação): o pipeline
monta cada `ItemEvidencia` com `classe = julg posicao`, `corpo_lido` da peça
e `citacao_verificada` do avaliador;
`decidir` agrega em 1 voto por cluster. Não chama LLM nem faz I/O.

O nível é propensão a a entrada ser desinformação, só a partir de VERACIDADE:
(a) postura das fontes julgadas (juiz de 4 classes, citação verificada), um voto
por CLUSTER independente, ponderado pela credibilidade da página (T6: o BERTimbau mede
a credibilidade; a direção é sempre do juiz; o mock não entra); (b) vereditos tipados
de checagem do ÍNDICE (E1/aplicabilidade), direção por `selos.direcao` e só quando o
juiz disse que a página trata da afirmação. Selo ClaimReview extraído da própria
página NÃO vota (T6). Padrões de estilo NÃO entram.

Fórmula (log-odds, prior neutro L0 = 0), por afirmação a:

    s_a       = +1 se o usuário afirma o núcleo, -1 se nega (polaridade)
    contrib   = s_a * d * w
      postura : d = +1 REFUTA, -1 SUSTENTA (RELATA/NAO_TRATA não votam)
                w = W_POSTURA * f_fonte * (1 − prob_fake_pagina)  (T6; sem modelo real, fator 1,0)
      veredito: d = selos.direcao(veredito) (FALSO +1 … VERDADEIRO -1; SATIRA 0 = não vota)
                w = W_VEREDITO * f_fonte; só selo do ÍNDICE (origem "pagina" não vota: T6)
      f_fonte = confiabilidade da fonte (confiabilidade.FATOR_POSTURA / FATOR_VEREDITO):
                curada 1,0 · institucional / muito acessada (Tranco) 0,6 (selo 0,8) ·
                rede social / plataforma 0,45 (selo 0,55) · site pouco acessado 0,3 (selo 0,4)
      página com postura e veredito: vale a contribuição de maior |.| se concordam;
      se discordam, 0 (conflito registrado).
      E3: peça lida só pelo título/snippet não vota (nem postura nem selo).
      E4: texto com "hoje/ontem/nesta semana" e fonte publicada `e` dias além da janela →
          r = relevancia_temporal(e, janela) e contrib' = sinal·ln(r·e^|contrib| + 1 − r)
          (mistura: com prob. 1−r a fonte fala de outro episódio e não informa nada).
          Simétrico: nunca eleva nem baixa a propensão por si; os bits descartados
          ficam em `descontos_temporais`.
    voto do cluster = sinal(Σ contrib dos itens) * max(|contrib| dos itens que concordam)
    L_a       = Σ_clusters voto  (um cluster = um voto: republicação não soma)
    p_texto   = 1 − Π_a (1 − σ(L_a))   (conjunção, D2: o texto é desinformação se QUALQUER parte for)
    L         = logit(p_texto)          (0 se nenhuma afirmação tem voto)
    p         = σ(L)  (probabilidade de desinformação, só informativa)

Conjunção (D2): só afirmações COM voto (n_votos > 0) entram no produto. Afirmação sem voto
fica fora (ausência de evidência não é evidência); votos que se anulam (L_a = 0) entram com
p = 0,5. Com uma afirmação, L = L_a. O contrafactual (sem desconto temporal) usa o mesmo
critério sobre os votos brutos. Ver `_combinar_afirmacoes`.
T7 (E4 × D2): com 2+ afirmações com voto, sai do produto a afirmação cujos votos vêm SÓ de
fontes fora da janela (descontadas) e cujo |L_a| < τ ("só evidência de outro período" em
`por_afirmacao[i]`): evidência de outro episódio que não chega a um nível não informa o fato
atual. Também sai a afirmação que a trava so_fontes (abaixo) cortou. O contrafactual (sem
desconto) não muda.
E4, guarda do sinal (decisão da usuária, 09/10): E4 só remove informação de fonte de outro episódio.
O nível pode ficar mais extremo apenas na direção de fontes do período, nunca sustentado só por
fonte descontada. Trava `so_fontes_de_outro_periodo` (só no caminho COM desconto): por afirmação, se
|L_a| ≥ τ e nenhum voto na direção de L_a tem contribuição de item NÃO descontado (r = 1, dentro da
janela), então L_a = copysign(0,99τ, L_a) e `travas["so_fontes_de_outro_periodo"] = True`. Reusa o
rastreio "descontado vs atual" da exclusão T7. Ordem com `sem_fonte_confiavel`: as duas leem o mesmo
L_a bruto e cortam para o mesmo 0,99τ, então a ordem não muda o nível nem as flags, e aplicar as duas
é idempotente (cada flag é registrada quando a própria condição vale).

Faixas SIMÉTRICAS em torno de 0 (τ = ln 3 ≈ 1,10, ou seja p ≥ 0,75 / p ≤ 0,25):
    L ≥ +τ → alta · L ≤ −τ → baixa · |L| < τ → media
    indeterminada: nenhum cluster com voto ≠ 0 (0 fontes SUSTENTA/REFUTA e nenhum
    veredito aplicável), ou afirmação vaga, ou opinião/sátira sem veredito.
Trava de confiabilidade: sem ao menos 1 voto de fonte confiável (curada, institucional ou muito
acessada) no mesmo sentido, |L_a| fica abaixo de τ — redes sociais e sites pouco acessados votam,
mas sozinhos não cravam alta/baixa.
Consequências: 1 fonte curada sozinha (w=1,0) → média (não satura); 2 clusters
concordes → alta/baixa; 1 selo do índice aplicável (1,5) → alta/baixa; fontes em
conflito → média. Sinais `fallback-*` (sem juiz) são ignorados. Ausência de
evidência não é evidência: nada empurra o nível sem uma fonte com postura.
Conjunção: uma parte com L ≥ τ e outra com L ≤ −τ dão alta, e a justificativa cita as duas
partes; a parte verdadeira não apaga a falsa (L ≥ max L_a).

A justificativa, o header e o why são gerados DO MESMO objeto `Decisao`.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from . import aplicabilidade, confiabilidade, selos

# ------------------------------------------------------------------ parâmetros (únicos)
TAU = math.log(3)            # faixa média: |L| < ln 3  <=>  0,25 < p < 0,75
W_POSTURA = 1.0              # postura com citação verificada, fonte curada, corpo lido
W_VEREDITO = 1.5             # selo de checagem tipado, aplicável à afirmação
# Fonte fora do catálogo: o fator vem do nível de confiabilidade (confiabilidade.py).
F_NAO_CURADA = confiabilidade.FATOR_POSTURA[confiabilidade.ALTO_TRAFEGO]            # 0,6 (como antes)
F_VEREDITO_NAO_CURADA = confiabilidade.FATOR_VEREDITO[confiabilidade.ALTO_TRAFEGO]  # 0,8 (como antes)

CLASSES_VOTO = ("SUSTENTA", "REFUTA")
CLASSES_TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")
# Trecho do `motivo` de `decidir` quando o desconto temporal zera a evidência (indeterminada):
# a justificativa e o why já trazem o aviso de data por esse motivo (ver `_motivo_avisa_data`).
_MARCA_OUTRO_EPISODIO = "outro episódio"


@dataclass
class ItemEvidencia:
    """Uma peça julgada frente a UMA afirmação (a mesma URL pode aparecer para 2 afirmações)."""
    url: str
    afirmacao: int = 0
    cluster: str = ""
    classe: Optional[str] = None      # SUSTENTA|REFUTA|RELATA_SEM_ENDOSSO|NAO_TRATA|None
    motor: str = ""                   # llm-juiz:<prov> | fallback-*
    citacao_verificada: Optional[bool] = None
    curada: bool = False
    corpo_lido: bool = False
    veredito: Optional[str] = None    # selos.VEREDITOS
    origem_veredito: Optional[str] = None  # pagina|indice
    veiculo: str = ""
    confiabilidade: Optional[str] = None   # confiabilidade.NIVEIS; None = calcula pela URL
    data_pub: Optional[str] = None         # YYYY-MM-DD da peça (E4: relevância temporal)
    prob_fake_pagina: Optional[float] = None  # T6/B1c: P(fake) da página (BERTimbau); None = sem modelo real
    data_pub_bruta: Optional[str] = None      # E4: data como veio da fonte (só telemetria/auditoria; não decide)
    data_pub_precisao: Optional[str] = None   # E4: dia|ano da data normalizada (telemetria; não decide)

    def nivel_confiabilidade(self) -> str:
        return self.confiabilidade or confiabilidade.classificar(self.url, self.curada)


@dataclass
class AfirmacaoDecisao:
    texto: str
    nucleo: str = ""
    polaridade: str = "afirma"
    janela: Optional[int] = None  # E4: janela temporal da afirmação (janela_da_afirmacao); None = sem marcador
    marco: Optional[tuple[str, int]] = None  # E4 (Task 4b): (data_evento ISO, folga) da data explícita do fato; tem precedência
    # E4 (I-4): True = janela/marco já calculados pelo pipeline (`aplicabilidade.marcas_da_afirmacao`);
    # None então significa "sem marcador de fato", e a decisão NÃO recalcula pelo texto. False = snapshot
    # antigo ou construção sem o campo (testes/snapshots pré-campo): caminho legado.
    calculada: bool = False


@dataclass
class Evidencias:
    afirmacoes: List[AfirmacaoDecisao]
    itens: List[ItemEvidencia] = field(default_factory=list)
    vago: bool = False
    opiniao: bool = False
    rumor: bool = False
    juiz_disponivel: bool = True
    n_lidas: int = 0           # páginas com corpo lido
    n_consultadas: int = 0     # peças únicas após dedupe
    texto_usuario: str = ""    # E4: marcadores "hoje/ontem/nesta semana" do texto
    data_referencia: Optional[str] = None  # E4: "hoje" do texto (YYYY-MM-DD); None = data atual


@dataclass
class Voto:
    afirmacao: int
    cluster: str
    direcao: float             # +1 eleva propensão, -1 reduz (já com polaridade)
    peso: float
    valor: float               # direcao * peso
    urls: List[str]
    classes: List[str]
    vereditos: List[str]
    motivo: str
    confiavel: bool = True     # algum item do cluster é curado, institucional ou muito acessado


@dataclass
class Decisao:
    nivel: str
    log_odds: float
    prob: float
    motivo: str
    votos: List[Voto] = field(default_factory=list)
    por_afirmacao: List[Dict[str, Any]] = field(default_factory=list)
    vereditos_aplicados: List[Dict[str, Any]] = field(default_factory=list)
    vereditos_ignorados: List[Dict[str, Any]] = field(default_factory=list)
    conflitos: List[Dict[str, Any]] = field(default_factory=list)
    nao_analisadas: List[Dict[str, Any]] = field(default_factory=list)       # E3: só título, não vota
    descontos_temporais: List[Dict[str, Any]] = field(default_factory=list)  # E4: evidência de outro episódio
    contagem: Dict[str, int] = field(default_factory=dict)
    travas: Dict[str, bool] = field(default_factory=dict)
    parametros: Dict[str, Any] = field(default_factory=dict)
    log_odds_sem_desconto: float = 0.0  # E4: contrafactual sem desconto temporal
    nivel_sem_desconto: str = "indeterminada"  # E4: faixa do contrafactual

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # -------------------------------------------------------------- texto (mesmo objeto)
    def _afirmacao_fora(self, a_idx: Any) -> bool:
        """T7/E4: afirmação excluída da conjunção ("só evidência de outro período"). Os votos dela ficam
        em `votos` (trace), mas o texto só conta as afirmações que pesaram no resultado."""
        return (isinstance(a_idx, int) and 0 <= a_idx < len(self.por_afirmacao)
                and "fora_da_conjuncao" in self.por_afirmacao[a_idx])

    def n_clusters(self, sinal: int) -> int:
        return len({(v.afirmacao, v.cluster) for v in self.votos
                    if v.valor * sinal > 0 and not self._afirmacao_fora(v.afirmacao)})

    def n_fontes_descontadas(self) -> int:
        """Fontes com desconto por data, contadas por URL: a mesma URL em 2 afirmações é 1 fonte."""
        return len({x.get("url") for x in self.descontos_temporais if x.get("url")})

    def _motivo_avisa_data(self) -> bool:
        """Indeterminada cujo motivo já é o aviso de data (desconto zerou a evidência): o bloco E4
        da justificativa e o sufixo do why não repetem o aviso."""
        return self.nivel == "indeterminada" and _MARCA_OUTRO_EPISODIO in self.motivo

    def limitacao_datas(self) -> str:
        """Limitação neutra sobre datas (o bot mostra as 3 primeiras; o pipeline a põe no início)."""
        n = self.n_fontes_descontadas()
        if n == 1:
            return "Datas: 1 fonte anterior ao período do texto teve o peso reduzido."
        return f"Datas: {n} fontes anteriores ao período do texto tiveram o peso reduzido."

    def _partes_em_conflito(self) -> str:
        """D2: em alta, uma afirmação contestada (L_a ≥ τ) e outra confirmada (L_a ≤ −τ) são
        citadas, cada uma pelo próprio texto. "" quando não se aplica."""
        if self.nivel != "alta":
            return ""
        contestadas = [(a["L"], i) for i, a in enumerate(self.por_afirmacao) if a["L"] >= TAU]
        confirmadas = [(a["L"], i) for i, a in enumerate(self.por_afirmacao) if a["L"] <= -TAU]
        if not contestadas or not confirmadas:
            return ""
        i_c = max(contestadas)[1]
        i_f = min(confirmadas)[1]
        return (f"Fontes independentes contestam {_citar_afirmacao(self.por_afirmacao[i_c], i_c)}; "
                f"já {_citar_afirmacao(self.por_afirmacao[i_f], i_f)} é confirmada por fontes.")

    def justificativa(self) -> str:
        from .agregador import titulo_propensao
        c = self.contagem
        partes = [titulo_propensao(self.nivel) + "."]
        if self.nivel == "indeterminada":
            partes.append(self.motivo[:1].upper() + self.motivo[1:] + ".")
        else:
            up, down = self.n_clusters(+1), self.n_clusters(-1)
            partes.append(f"{up} fonte(s) independente(s) contestam o que o texto afirma e "
                          f"{down} o confirmam")
            selos = [v for v in self.vereditos_aplicados if not self._afirmacao_fora(v.get("afirmacao"))]
            if selos:
                selos_txt = ", ".join(f"{v['veredito']} ({v['veiculo'] or v['url']})" for v in selos[:3])
                partes[-1] += f"; selos de checagem considerados: {selos_txt}"
            partes[-1] += "."
            conflito = self._partes_em_conflito()
            if conflito:
                partes.append(conflito)
            if self.conflitos:
                partes.append(f"{len(self.conflitos)} fonte(s) com selo e texto em sentidos opostos "
                              "foram desconsideradas.")
            if self.travas.get("sem_fonte_confiavel"):
                partes.append("As fontes que tomam posição são redes sociais ou sites pouco acessados, "
                              "então o resultado não passa de propensão média.")
        partes.append(f"Lidas {c.get('lidas', 0)} página(s) de {c.get('consultadas', 0)} consultada(s); "
                      f"{c.get('julgadas', 0)} julgada(s): {c.get('sustenta', 0)} confirmam, "
                      f"{c.get('refuta', 0)} contestam, {c.get('relata', 0)} só relatam, "
                      f"{c.get('fora_do_tema', 0)} fora do tema"
                      + (f" ({c.get('citacao_invalida', 0)} por citação não encontrada no texto)"
                         if c.get("citacao_invalida") else "")
                      + (f"; {c.get('sem_juiz', 0)} sem julgamento" if c.get("sem_juiz") else "") + ".")
        # E4 Task 6: aviso neutro de data (sobre DATAS, nunca veracidade; sem "falso/verdadeiro").
        if self.travas.get("data_incompativel") and self.descontos_temporais and not self._motivo_avisa_data():
            n_dt = self.n_fontes_descontadas()
            if n_dt == 1:
                partes.append("1 fonte foi publicada antes do período que o texto descreve "
                              "(\"hoje\", \"ontem\"…); o peso dela foi reduzido porque pode tratar "
                              "de outro episódio.")
            else:
                partes.append(f"{n_dt} fontes foram publicadas antes do período que o texto descreve "
                              "(\"hoje\", \"ontem\"…); o peso delas foi reduzido porque podem tratar "
                              "de outro episódio.")
            if self.nivel != self.nivel_sem_desconto or self.travas.get("so_fontes_de_outro_periodo"):
                partes.append("Verifique se não é notícia antiga recirculando.")
        partes.append("Isso não é um veredito: compare as fontes abaixo e tire sua própria conclusão.")
        return " ".join(partes)

    def why_1linha(self) -> str:
        c = self.contagem
        if self.nivel == "indeterminada":
            base = (f"{self.motivo}; {c.get('julgadas', 0)} fonte(s) julgada(s), "
                    f"{c.get('fora_do_tema', 0)} fora do tema — compare as fontes abaixo.")
        else:
            base = (f"{self.n_clusters(+1)} fonte(s) independente(s) contestam e {self.n_clusters(-1)} confirmam "
                    f"o que o texto afirma; {c.get('lidas', 0)} página(s) lida(s), "
                    f"{c.get('fora_do_tema', 0)} fora do tema.")
            conflito = self._partes_em_conflito()  # D2: o bot e a API mostram o why primeiro
            if conflito:
                base += " " + conflito
        # E4 Task 6: sufixo curto só quando o desconto mudou o nível, ou a trava so_fontes cortou (neutro).
        if (self.travas.get("data_incompativel")
                and (self.nivel != self.nivel_sem_desconto or self.travas.get("so_fontes_de_outro_periodo"))
                and not self._motivo_avisa_data()):
            base += " Datas anteriores ao período do texto tiveram o peso reduzido."
        return base

    def header(self) -> str:
        from .agregador import EMOJI_PROPENSAO, titulo_propensao
        return f"{EMOJI_PROPENSAO.get(self.nivel, '⚪')} {titulo_propensao(self.nivel)}"

    def resumo_trace(self) -> Dict[str, Any]:
        """Versão compacta para o evento `decisao` (lida no `cli trace`)."""
        return {
            "L": round(self.log_odds, 3), "p": round(self.prob, 3), "motivo": self.motivo,
            "L_sem_desconto": round(self.log_odds_sem_desconto, 3),
            "nivel_sem_desconto": self.nivel_sem_desconto,
            "descontos": [f"{d.get('url', '')[:60]} +{d.get('dias_alem_da_janela')}d "
                          f"r={d.get('r')} −{d.get('bits_descartados')}b"
                          for d in self.descontos_temporais][:6],
            "votos": [f"af{v.afirmacao}:{v.cluster} {v.valor:+.2f} [{','.join(v.classes)}"
                      f"{'|' + ','.join(v.vereditos) if v.vereditos else ''}] {v.motivo}" for v in self.votos],
            "vereditos": [f"{v['veredito']}@{v['url'][:60]}" for v in self.vereditos_aplicados],
            "vereditos_ignorados": [f"{v.get('veredito')}@{v['url'][:60]}: {v['motivo']}"
                                    for v in self.vereditos_ignorados][:6],
            "conflitos": [c["url"][:70] for c in self.conflitos],
            "contagem": self.contagem, **self.travas,
        }


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


_LIMITE_CITACAO = 160


def _citar_afirmacao(entrada: Dict[str, Any], idx: int) -> str:
    """Como a justificativa cita uma afirmação: entre aspas, como o usuário escreveu (com a
    negação, se houver). Se o texto traz expressão proibida (ex.: "é falso que X"), cita-se pelo
    número, para o nosso texto continuar passando em `verificar_neutralidade` (RF12)."""
    from .agregador import EXPRESSOES_PROIBIDAS, _SELO_ATRIBUIDO_RE
    texto = " ".join((entrada.get("afirmacao") or "").split())
    if not texto or any(e in texto.lower() for e in EXPRESSOES_PROIBIDAS) or _SELO_ATRIBUIDO_RE.search(texto):
        return f"a afirmação {idx + 1}"
    if len(texto) > _LIMITE_CITACAO:
        texto = texto[:_LIMITE_CITACAO - 1].rstrip() + "…"
    return f'"{texto}"'


def nivel_de(L: float) -> str:
    if L >= TAU:
        return "alta"
    if L <= -TAU:
        return "baixa"
    return "media"


def relevancia_temporal(excedente: int, janela: int) -> float:
    """r = P(a fonte trata do MESMO episódio | publicada `excedente` dias além da janela).

    `janela` vem do marcador do texto (hoje=2, ontem=3, nesta semana=8 dias);
    `excedente` = 0 quando a fonte está dentro da janela. Deve devolver r em [0, 1],
    com r = 1 para excedente 0 e decrescente depois.
    """
    # Hiperbólica: r = 1/2 quando o atraso iguala a janela; cauda longa, mas ~0 em meses/anos.
    return janela / (janela + max(0, excedente)) if janela > 0 else (1.0 if excedente <= 0 else 0.0)


def _descontar(valor: float, r: float) -> float:
    """Peso de evidência (log-likelihood ratio, em nats) de uma fonte que só com probabilidade
    r trata do mesmo episódio: com prob. 1−r ela fala de outro fato e não informa nada
    (razão de verossimilhança 1). Mistura: LR_ef = r·LR + (1−r). Simétrica em |L| para
    não favorecer direção (contesta e confirma perdem o mesmo tanto) — por isso E4 nunca
    eleva a propensão: só tira informação de fonte de outro episódio."""
    if r >= 1.0 or valor == 0:
        return valor
    return math.copysign(math.log(r * math.exp(abs(valor)) + (1.0 - r)), valor)


def _fator_credibilidade(it: ItemEvidencia) -> float:
    """T6/D1: fator (1 − prob_fake_pagina) da postura, com prob_fake limitada a [0, 1].
    Sem modelo real (None: snapshot antigo, página não lida, mock) o fator é 1,0."""
    if it.prob_fake_pagina is None:
        return 1.0
    return 1.0 - min(1.0, max(0.0, float(it.prob_fake_pagina)))


def _contribuicoes(it: ItemEvidencia, s: float, dec: Decisao) -> List[tuple]:
    """(valor, descrição) de um item; registra vereditos aplicados/ignorados e conflitos."""
    if (it.motor or "").startswith("fallback") or it.classe is None:
        if it.veredito:
            dec.vereditos_ignorados.append({"url": it.url, "veredito": it.veredito,
                                            "motivo": "página não julgada (sem juiz)"})
        return []
    if not it.corpo_lido:
        # E3: só título/snippet não vota (nem postura nem selo): "não analisada integralmente".
        dec.nao_analisadas.append({"url": it.url, "classe": it.classe, "veredito": it.veredito,
                                   "afirmacao": it.afirmacao})
        if it.veredito:
            dec.vereditos_ignorados.append({"url": it.url, "veredito": it.veredito,
                                            "motivo": "página não lida integralmente"})
        return []
    out: List[tuple] = []
    post = None
    if it.classe in CLASSES_VOTO:
        d = 1.0 if it.classe == "REFUTA" else -1.0
        w = W_POSTURA * confiabilidade.FATOR_POSTURA[it.nivel_confiabilidade()] * _fator_credibilidade(it)
        if w > 0:  # prob_fake 1,0: a página não vota na postura
            post = (s * d * w, f"postura {it.classe}")
    ver = None
    if it.veredito:
        if it.classe not in CLASSES_TRATA:
            dec.vereditos_ignorados.append({"url": it.url, "veredito": it.veredito,
                                            "motivo": "juiz: a página não trata desta afirmação"})
        elif it.origem_veredito == "pagina":
            # T6: selo ClaimReview extraído da própria página (template/JSON-LD do portal) não
            # vota; só o do índice. origem_veredito None (ausente) segue votando, como antes.
            dec.vereditos_ignorados.append({"url": it.url, "veredito": it.veredito,
                                            "motivo": "selo extraído da página não vota (só o do índice)"})
        else:
            d = selos.direcao(it.veredito)
            if d == 0:
                dec.vereditos_ignorados.append({"url": it.url, "veredito": it.veredito,
                                                "motivo": "selo sem direção (ex.: sátira)"})
            else:
                w = W_VEREDITO * confiabilidade.FATOR_VEREDITO[it.nivel_confiabilidade()]
                ver = (s * d * w, f"selo {it.veredito} ({it.origem_veredito or '?'})")
    if post and ver:
        if post[0] * ver[0] < 0:
            dec.conflitos.append({"url": it.url, "classe": it.classe, "veredito": it.veredito})
            return []
        melhor = max((post, ver), key=lambda x: abs(x[0]))
        dec.vereditos_aplicados.append({"url": it.url, "veredito": it.veredito, "veiculo": it.veiculo,
                                        "valor": round(ver[0], 3), "afirmacao": it.afirmacao})
        return [melhor]
    if ver:
        dec.vereditos_aplicados.append({"url": it.url, "veredito": it.veredito, "veiculo": it.veiculo,
                                        "valor": round(ver[0], 3), "afirmacao": it.afirmacao})
        out.append(ver)
    if post:
        out.append(post)
    return out


def _agregar(clusters: Dict[str, List[tuple]], a_idx: int, dec: Decisao, registrar: bool,
             atuais: Optional[Dict[str, List[float]]] = None) -> tuple[float, bool]:
    """Agrega os votos de UMA afirmação (um voto por cluster + duas travas: confiabilidade e E4).

    Mesma regra para o caminho com desconto e para o contrafactual sem desconto;
    só o caminho com desconto registra votos/travas em `dec` (o `decidir` segue puro).
    `atuais` (só no caminho com desconto): por cluster, as contribuições de itens NÃO descontados
    (dentro da janela). Sem ele a trava so_fontes_de_outro_periodo não age (contrafactual).
    Devolve (L_a, cortou_so_fontes).
    """
    L_a = 0.0
    novos: List[Voto] = []
    for cid, contribs in clusters.items():
        soma = sum(v for v, _, _ in contribs)
        if soma == 0:
            continue
        sinal = 1.0 if soma > 0 else -1.0
        peso = max(abs(v) for v, _, _ in contribs if v * sinal > 0)
        voto = Voto(afirmacao=a_idx, cluster=cid, direcao=sinal, peso=round(peso, 4),
                    valor=round(sinal * peso, 4),
                    urls=sorted({it.url for _, _, it in contribs}),
                    classes=sorted({it.classe for _, _, it in contribs if it.classe}),
                    vereditos=sorted({it.veredito for _, _, it in contribs if it.veredito}),
                    motivo="; ".join(sorted({d for _, d, _ in contribs})),
                    confiavel=any(it.nivel_confiabilidade() in confiabilidade.CONFIAVEIS
                                  for v, _, it in contribs if v * sinal > 0))
        novos.append(voto)
        L_a += voto.valor
    # Duas travas leem o MESMO L_a bruto e cortam para o MESMO 0,99τ: idempotentes, então a ordem
    # não muda o nível nem as flags; cada flag é registrada quando a própria condição vale.
    # 1) sem_fonte_confiavel: fontes pouco confiáveis (rede social, site pouco acessado) votam, mas
    #    sozinhas não cravam alta/baixa: sem ao menos 1 voto confiável no mesmo sentido, fica na média.
    # 2) so_fontes_de_outro_periodo (E4, só com `atuais`): nenhum voto na direção de L_a tem
    #    contribuição de item NÃO descontado; o extremo é só de fonte de outro período.
    corta_sem = corta_so = False
    if abs(L_a) >= TAU:
        sinal = 1.0 if L_a > 0 else -1.0
        corta_sem = not any(v.confiavel and v.valor * sinal > 0 for v in novos)
        corta_so = atuais is not None and not any(
            v.valor * sinal > 0 and any(x * sinal > 0 for x in atuais.get(v.cluster, ())) for v in novos)
    if corta_sem or corta_so:
        L_a = math.copysign(TAU * 0.99, L_a)
        if registrar:
            if corta_sem:
                dec.travas["sem_fonte_confiavel"] = True
            if corta_so:
                dec.travas["so_fontes_de_outro_periodo"] = True
    if registrar:
        dec.votos.extend(novos)
    return L_a, corta_so


def _clusters_com_voto(clusters: Dict[str, List[tuple]]) -> int:
    """Clusters que votam (soma ≠ 0): o mesmo critério de `_agregar`, sem registrar nada."""
    return sum(1 for contribs in clusters.values() if sum(v for v, _, _ in contribs) != 0)


def _combinar_afirmacoes(valores: List[float]) -> float:
    """Conjunção entre afirmações (D2): p_texto = 1 − Π(1 − σ(L_a)) e L = logit(p_texto).

    `valores` são os L_a só das afirmações COM voto (quem chama filtra: ausência de evidência
    não entra no produto; no caminho com desconto, sai também a só-descontada com |L_a| < τ,
    T7, ver `decidir`). Lista vazia → 0. Uma parte só → o próprio L_a, porque σ e logit se
    cancelam (atalho exato, sem o clamp de p). No ramo geral, 1 − σ(L) = σ(−L) (sem
    cancelamento) e p é limitado em [1e-12, 1 − 1e-12] antes do logit.
    Propriedade: L ≥ max(L_a); duas partes com p≈0,7 cada dão p_texto≈0,91.
    """
    if not valores:
        return 0.0
    if len(valores) == 1:
        return valores[0]
    descrenca = 1.0  # Π(1 − σ(L_a))
    for L_a in valores:
        descrenca *= _sig(-L_a)
    p = min(max(1.0 - descrenca, 1e-12), 1.0 - 1e-12)
    return math.log(p / (1.0 - p))


def _medida_temporal(af: AfirmacaoDecisao, ev: Evidencias, data_pub: Optional[str]):
    """(janela, dias além da janela) da fonte frente ao marcador da AFIRMAÇÃO.

    Com `af.marco` (data explícita do fato, Task 4b) o marco tem precedência sobre a janela.
    Senão usa `af.janela` (montada pelo pipeline via `aplicabilidade.janela_da_afirmacao`): os
    dias desde a publicação até a referência são medidos diretamente e o excedente é
    `max(0, dias − janela)`. Snapshot antigo (janela não calculada, 1 afirmação: testes e
    snapshots pré-campo) usa só o marcador do texto, como antes; com 2+ afirmações e janela
    ausente não mede (o "hoje" de uma frase não desconta a outra). A janela do snapshot antigo
    NÃO olha a própria afirmação: `--sem-e4` zera o texto e o desconto some por construção.
    Afirmação `calculada` (I-4): a medida é a MESMA do gate E1 (`aplicabilidade.medida_da_afirmacao`);
    `None` em janela e marco é "sem marcador de fato" e não cai no texto. Só o caminho legado (não
    calculada) recalcula pelo texto.
    """
    if af.calculada:
        return aplicabilidade.medida_da_afirmacao(af.janela, af.marco, data_pub, ev.data_referencia)
    if af.marco is not None:
        return aplicabilidade.dias_excedentes_do_marco(af.marco, data_pub)
    if af.janela is None:
        if len(ev.afirmacoes) != 1:
            return None
        janela = aplicabilidade.janela_da_afirmacao("", ev.texto_usuario, 1, ev.data_referencia)
    else:
        janela = af.janela
    return aplicabilidade.dias_excedentes_da_janela(janela, data_pub, ev.data_referencia)


def decidir(ev: Evidencias) -> Decisao:
    """Função pura: mesma entrada, mesma Decisao. Não faz I/O nem telemetria."""
    dec = Decisao(nivel="indeterminada", log_odds=0.0, prob=0.5, motivo="",
                  travas={"vago": ev.vago, "opiniao": ev.opiniao, "rumor": ev.rumor, "sem_fonte_confiavel": False,
                          "so_fontes_de_outro_periodo": False, "juiz_disponivel": ev.juiz_disponivel},
                  parametros={"tau": round(TAU, 4), "w_postura": W_POSTURA, "w_veredito": W_VEREDITO,
                              "f_nao_curada": F_NAO_CURADA, "f_veredito_nao_curada": F_VEREDITO_NAO_CURADA,
                              "f_postura_por_nivel": dict(confiabilidade.FATOR_POSTURA),
                              "f_veredito_por_nivel": dict(confiabilidade.FATOR_VEREDITO),
                              "postura": "W_POSTURA·f_fonte·(1−prob_fake_pagina)",
                              "combinacao": ("conjuncao: 1−Π(1−σ(L_a)) sobre afirmações com voto; "
                                             "sem as só-descontadas (E4) com |L_a|<τ, se 2+ com voto")})
    julgados = [i for i in ev.itens if i.classe is not None and not (i.motor or "").startswith("fallback")]
    dec.contagem = {
        "consultadas": ev.n_consultadas, "lidas": ev.n_lidas, "julgadas": len(julgados),
        "sustenta": sum(1 for i in julgados if i.classe == "SUSTENTA"),
        "refuta": sum(1 for i in julgados if i.classe == "REFUTA"),
        "relata": sum(1 for i in julgados if i.classe == "RELATA_SEM_ENDOSSO"),
        "fora_do_tema": sum(1 for i in julgados if i.classe == "NAO_TRATA"),
        "citacao_invalida": sum(1 for i in julgados if i.classe == "NAO_TRATA" and i.citacao_verificada is False),
        "sem_juiz": sum(1 for i in ev.itens if i.classe is None or (i.motor or "").startswith("fallback")),
    }
    # votos por (afirmação, cluster)
    por_af: Dict[int, float] = {}
    por_af_brutos: Dict[int, float] = {}
    n_votos: Dict[int, int] = {}
    n_brutos: Dict[int, int] = {}
    so_descontada: Dict[int, bool] = {}  # T7/E4: todo voto da afirmação vem de fonte fora da janela
    for a_idx, af in enumerate(ev.afirmacoes):
        s = -1.0 if af.polaridade == "nega" else 1.0
        clusters: Dict[str, List[tuple]] = {}
        clusters_brutos: Dict[str, List[tuple]] = {}
        atuais: Dict[str, List[float]] = {}  # E4 (trava so_fontes): contribuições NÃO descontadas, por cluster
        contribuiu_atual = contribuiu_descontado = False  # T7: itens com voto dentro / fora da janela
        for it in ev.itens:
            if it.afirmacao != a_idx:
                continue
            contribs = _contribuicoes(it, s, dec)
            brutos = list(contribs)
            medida = (_medida_temporal(af, ev, it.data_pub) if contribs else None)
            descontado = bool(medida and medida[1] > 0)  # E4: fonte de outro período (excedente > 0)
            if descontado:
                r = relevancia_temporal(medida[1], medida[0])
                top = max(brutos, key=lambda x: abs(x[0]))
                direcao = 1 if top[0] > 0 else -1
                nats_antes = max(abs(v) for v, _ in brutos)
                descontados = [(_descontar(v, r), f"{d} (data: r={r:.2f})") for v, d in brutos]
                nats_depois = max(abs(v) for v, _ in descontados)
                bits = (nats_antes - nats_depois) / math.log(2)
                dec.descontos_temporais.append({
                    "url": it.url, "afirmacao": a_idx, "data_pub": it.data_pub,
                    "data_pub_bruta": it.data_pub_bruta, "data_pub_precisao": it.data_pub_precisao,
                    "janela": medida[0], "dias_alem_da_janela": medida[1], "r": round(r, 3),
                    "direcao": direcao,
                    "nats_antes": round(nats_antes, 4), "nats_depois": round(nats_depois, 4),
                    "bits_descartados": round(bits, 3)})
                contribs = descontados
            if brutos:
                if descontado:
                    contribuiu_descontado = True
                else:
                    contribuiu_atual = True
                    atuais.setdefault(it.cluster or it.url, []).extend(v for v, _ in brutos)
            for valor, desc in contribs:
                clusters.setdefault(it.cluster or it.url, []).append((valor, desc, it))
            for valor, desc in brutos:
                clusters_brutos.setdefault(it.cluster or it.url, []).append((valor, desc, it))
        L_a, cortou_so = _agregar(clusters, a_idx, dec, registrar=True, atuais=atuais)
        L_a_bruto, _ = _agregar(clusters_brutos, a_idx, dec, registrar=False)
        por_af[a_idx] = L_a
        por_af_brutos[a_idx] = L_a_bruto
        n_votos[a_idx] = sum(1 for v in dec.votos if v.afirmacao == a_idx)
        n_brutos[a_idx] = _clusters_com_voto(clusters_brutos)
        # T7/E4: só-descontada = todo voto vem de fora da janela; a trava so_fontes também conta
        # (o L cortado sustentava-se só em fonte descontada na direção dele).
        so_descontada[a_idx] = (contribuiu_descontado and not contribuiu_atual) or cortou_so
        dec.por_afirmacao.append({"afirmacao": af.texto, "nucleo": af.nucleo, "polaridade": af.polaridade,
                                  "L": round(L_a, 4), "n_votos": n_votos[a_idx]})
    # combinação entre afirmações (D2, conjunção): o texto é desinformação se QUALQUER parte for.
    # "Sem voto" (n_votos == 0) fica fora do produto. O contrafactual usa o mesmo critério sobre
    # os votos brutos, isto é, o caso sem o desconto temporal.
    # T7 (E4 × D2): também fica fora a afirmação só com fontes fora da janela (descontadas) cujo
    # |L_a| (já com a trava de confiabilidade) ficou abaixo de τ: evidência de outro episódio que,
    # sozinha, não chega a um nível não informa o fato atual. Só vale com 2+ afirmações com voto;
    # com uma só não há conjunção e o L é o dela, como antes. Acima de τ, a parte continua.
    com_voto = [a for a in por_af if n_votos[a] > 0]
    fora = set()
    if len(com_voto) > 1:  # com uma só não há conjunção: o L é o dela
        fora = {a for a in com_voto if so_descontada[a] and abs(por_af[a]) < TAU}
    for a in fora:
        dec.por_afirmacao[a]["fora_da_conjuncao"] = "só evidência de outro período"
    L = _combinar_afirmacoes([por_af[a] for a in com_voto if a not in fora])
    L_sem = _combinar_afirmacoes([por_af_brutos[a] for a in por_af_brutos if n_brutos[a] > 0])
    dec.log_odds = round(L, 4)
    dec.log_odds_sem_desconto = round(L_sem, 4)
    dec.nivel_sem_desconto = nivel_de(L_sem)
    dec.prob = round(_sig(L), 4)
    dec.parametros["e4"] = {"janelas": aplicabilidade.janelas_efetivas(),
                            "formula": "sinal·ln(r·e^|c| + 1 − r)",
                            "trava": ("so_fontes_de_outro_periodo: |L_a|≥τ sem voto de item não descontado na "
                                      "direção de L_a → 0,99τ e fora da conjunção")}

    # elegibilidade (antes: travas espalhadas no pipeline)
    tem_voto = any(v.valor != 0 for v in dec.votos)
    if ev.vago:
        dec.nivel, dec.motivo = "indeterminada", ("afirmação vaga (comparativo sem indicador nem período): "
                                                   "não é checável como está")
    elif not tem_voto:
        if not ev.juiz_disponivel and ev.itens:
            dec.motivo = "sem julgamento de conteúdo (LLM-juiz indisponível): nenhuma fonte foi avaliada"
        elif not ev.itens:
            dec.motivo = "nenhuma fonte encontrada sobre a afirmação"
        elif dec.nao_analisadas and not any(it.corpo_lido for it in julgados
                                            if it.classe in CLASSES_VOTO):
            dec.motivo = ("evidência insuficiente: as fontes com posição não foram lidas integralmente "
                          "(só título/resumo)")
        elif dec.contagem["sustenta"] + dec.contagem["refuta"] == 0:
            dec.motivo = "nenhuma fonte consultada confirma ou contesta a afirmação (só relatos ou fora do tema)"
        elif L_sem != 0 and dec.descontos_temporais:
            dec.motivo = ("as fontes com posição são de antes do período que o texto descreve "
                          "(\"hoje\", \"ontem\"…) e podem tratar de outro episódio — "
                          "verifique se não é notícia antiga recirculando")
        else:
            dec.motivo = "as fontes com postura se anulam dentro do mesmo grupo ou conflitam com o selo"
        dec.nivel = "indeterminada"
    elif ev.opiniao and not dec.vereditos_aplicados:
        dec.nivel, dec.motivo = "indeterminada", "texto com marcas de opinião/sátira e sem checagem aplicável"
    else:
        dec.nivel = nivel_de(L)
        dec.motivo = {"alta": "fontes independentes contestam o que o texto afirma",
                      "baixa": "fontes independentes confirmam o que o texto afirma",
                      "media": "evidência fraca ou dividida"}[dec.nivel]
        if dec.nivel == "media" and dec.travas.get("sem_fonte_confiavel"):
            dec.motivo = ("as fontes com posição são redes sociais ou sites pouco acessados; "
                          "falta a confirmação de um veículo, órgão público ou site muito acessado")
        elif dec.nivel == "media" and (dec.nivel != dec.nivel_sem_desconto
                                       or dec.travas.get("so_fontes_de_outro_periodo")):
            dec.motivo = ("as fontes encontradas são anteriores ao período que o texto descreve "
                          "(\"hoje\", \"ontem\"…): podem tratar de outro episódio — "
                          "verifique se não é notícia antiga recirculando")
    dec.travas["data_incompativel"] = bool(dec.descontos_temporais)
    return dec
