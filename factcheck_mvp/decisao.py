"""Núcleo de decisão: UMA função pura `decidir(evidencias) -> Decisao`.

Este módulo é o juiz-agregador determinístico sobre os outputs do avaliador 1:1
(`avaliador.avaliar`, manchete+corpo, 1 chamada por par peça×afirmação): o pipeline
monta cada `ItemEvidencia` com `classe = julg posicao`, `corpo_lido` da peça
(com `F_SO_TITULO` quando só título/snippet) e `citacao_verificada` do avaliador;
`decidir` agrega em 1 voto por cluster. Não chama LLM nem faz I/O.

O nível é propensão a a entrada ser desinformação, só a partir de VERACIDADE:
(a) postura das fontes julgadas (juiz de 4 classes, citação verificada), um voto
por CLUSTER independente; (b) vereditos tipados de checagem (ClaimReview da página
ou índice), direção por `selos.direcao` e só quando o juiz disse que a página trata
da afirmação. Estilo (BERTimbau/mock, padrões) NÃO entra.

Fórmula (log-odds, prior neutro L0 = 0), por afirmação a:

    s_a       = +1 se o usuário afirma o núcleo, -1 se nega (polaridade)
    contrib   = s_a * d * w
      postura : d = +1 REFUTA, -1 SUSTENTA (RELATA/NAO_TRATA não votam)
                w = W_POSTURA * f_fonte * f_corpo
      veredito: d = selos.direcao(veredito) (FALSO +1 … VERDADEIRO -1; SATIRA 0 = não vota)
                w = W_VEREDITO * f_fonte
      f_fonte = confiabilidade da fonte (confiabilidade.FATOR_POSTURA / FATOR_VEREDITO):
                curada 1,0 · institucional / muito acessada (Tranco) 0,6 (selo 0,8) ·
                rede social / plataforma 0,45 (selo 0,55) · site pouco acessado 0,3 (selo 0,4)
      página com postura e veredito: vale a contribuição de maior |.| se concordam;
      se discordam, 0 (conflito registrado).
    voto do cluster = sinal(Σ contrib dos itens) * max(|contrib| dos itens que concordam)
    L_a       = Σ_clusters voto  (um cluster = um voto: republicação não soma)
    L         = L_a da afirmação com maior L positivo se ele passa da faixa média;
                senão o L_a de maior |L_a|
    p         = σ(L)  (probabilidade de desinformação, só informativa)

Faixas SIMÉTRICAS em torno de 0 (τ = ln 3 ≈ 1,10, ou seja p ≥ 0,75 / p ≤ 0,25):
    L ≥ +τ → alta · L ≤ −τ → baixa · |L| < τ → media
    indeterminada: nenhum cluster com voto ≠ 0 (0 fontes SUSTENTA/REFUTA e nenhum
    veredito aplicável), ou afirmação vaga, ou opinião/sátira sem veredito.
Trava de confiabilidade: sem ao menos 1 voto de fonte confiável (curada, institucional ou muito
acessada) no mesmo sentido, |L_a| fica abaixo de τ — redes sociais e sites pouco acessados votam,
mas sozinhos não cravam alta/baixa.
Consequências: 1 fonte curada sozinha (w=1,0) → média (não satura); 2 clusters
concordes → alta/baixa; 1 selo de checagem aplicável (1,5) → alta/baixa; fontes em
conflito → média. Sinais `fallback-*` (sem juiz) são ignorados. Ausência de
evidência não é evidência: nada empurra o nível sem uma fonte com postura.

A justificativa, o header e o why são gerados DO MESMO objeto `Decisao`.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from . import confiabilidade, selos

# ------------------------------------------------------------------ parâmetros (únicos)
TAU = math.log(3)            # faixa média: |L| < ln 3  <=>  0,25 < p < 0,75
W_POSTURA = 1.0              # postura com citação verificada, fonte curada, corpo lido
W_VEREDITO = 1.5             # selo de checagem tipado, aplicável à afirmação
# Fonte fora do catálogo: o fator vem do nível de confiabilidade (confiabilidade.py).
F_NAO_CURADA = confiabilidade.FATOR_POSTURA[confiabilidade.ALTO_TRAFEGO]            # 0,6 (como antes)
F_VEREDITO_NAO_CURADA = confiabilidade.FATOR_VEREDITO[confiabilidade.ALTO_TRAFEGO]  # 0,8 (como antes)
F_SO_TITULO = 0.7            # postura julgada só com título/snippet (corpo não lido)

CLASSES_VOTO = ("SUSTENTA", "REFUTA")
CLASSES_TRATA = ("SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO")


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

    def nivel_confiabilidade(self) -> str:
        return self.confiabilidade or confiabilidade.classificar(self.url, self.curada)


@dataclass
class AfirmacaoDecisao:
    texto: str
    nucleo: str = ""
    polaridade: str = "afirma"


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
    contagem: Dict[str, int] = field(default_factory=dict)
    travas: Dict[str, bool] = field(default_factory=dict)
    parametros: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # -------------------------------------------------------------- texto (mesmo objeto)
    def n_clusters(self, sinal: int) -> int:
        return len({(v.afirmacao, v.cluster) for v in self.votos if v.valor * sinal > 0})

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
            if self.vereditos_aplicados:
                selos_txt = ", ".join(f"{v['veredito']} ({v['veiculo'] or v['url']})"
                                      for v in self.vereditos_aplicados[:3])
                partes[-1] += f"; selos de checagem considerados: {selos_txt}"
            partes[-1] += "."
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
        partes.append("Isso não é um veredito: compare as fontes abaixo e tire sua própria conclusão.")
        return " ".join(partes)

    def why_1linha(self) -> str:
        c = self.contagem
        if self.nivel == "indeterminada":
            return (f"{self.motivo}; {c.get('julgadas', 0)} fonte(s) julgada(s), "
                    f"{c.get('fora_do_tema', 0)} fora do tema — compare as fontes abaixo.")
        return (f"{self.n_clusters(+1)} fonte(s) independente(s) contestam e {self.n_clusters(-1)} confirmam "
                f"o que o texto afirma; {c.get('lidas', 0)} página(s) lida(s), "
                f"{c.get('fora_do_tema', 0)} fora do tema.")

    def header(self) -> str:
        from .agregador import EMOJI_PROPENSAO, titulo_propensao
        return f"{EMOJI_PROPENSAO.get(self.nivel, '⚪')} {titulo_propensao(self.nivel)}"

    def resumo_trace(self) -> Dict[str, Any]:
        """Versão compacta para o evento `decisao` (lida no `cli trace`)."""
        return {
            "L": round(self.log_odds, 3), "p": round(self.prob, 3), "motivo": self.motivo,
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


def nivel_de(L: float) -> str:
    if L >= TAU:
        return "alta"
    if L <= -TAU:
        return "baixa"
    return "media"


def _contribuicoes(it: ItemEvidencia, s: float, dec: Decisao) -> List[tuple]:
    """(valor, descrição) de um item; registra vereditos aplicados/ignorados e conflitos."""
    if (it.motor or "").startswith("fallback") or it.classe is None:
        if it.veredito:
            dec.vereditos_ignorados.append({"url": it.url, "veredito": it.veredito,
                                            "motivo": "página não julgada (sem juiz)"})
        return []
    out: List[tuple] = []
    post = None
    if it.classe in CLASSES_VOTO:
        d = 1.0 if it.classe == "REFUTA" else -1.0
        w = (W_POSTURA * confiabilidade.FATOR_POSTURA[it.nivel_confiabilidade()]
             * (1.0 if it.corpo_lido else F_SO_TITULO))
        post = (s * d * w, f"postura {it.classe}")
    ver = None
    if it.veredito:
        if it.classe not in CLASSES_TRATA:
            dec.vereditos_ignorados.append({"url": it.url, "veredito": it.veredito,
                                            "motivo": "juiz: a página não trata desta afirmação"})
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


def decidir(ev: Evidencias) -> Decisao:
    """Função pura: mesma entrada, mesma Decisao. Não faz I/O nem telemetria."""
    dec = Decisao(nivel="indeterminada", log_odds=0.0, prob=0.5, motivo="",
                  travas={"vago": ev.vago, "opiniao": ev.opiniao, "rumor": ev.rumor, "sem_fonte_confiavel": False,
                          "juiz_disponivel": ev.juiz_disponivel},
                  parametros={"tau": round(TAU, 4), "w_postura": W_POSTURA, "w_veredito": W_VEREDITO,
                              "f_nao_curada": F_NAO_CURADA, "f_veredito_nao_curada": F_VEREDITO_NAO_CURADA,
                              "f_so_titulo": F_SO_TITULO,
                              "f_postura_por_nivel": dict(confiabilidade.FATOR_POSTURA),
                              "f_veredito_por_nivel": dict(confiabilidade.FATOR_VEREDITO)})
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
    for a_idx, af in enumerate(ev.afirmacoes):
        s = -1.0 if af.polaridade == "nega" else 1.0
        clusters: Dict[str, List[tuple]] = {}
        for it in ev.itens:
            if it.afirmacao != a_idx:
                continue
            for valor, desc in _contribuicoes(it, s, dec):
                clusters.setdefault(it.cluster or it.url, []).append((valor, desc, it))
        L_a = 0.0
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
            dec.votos.append(voto)
            L_a += voto.valor
        # Fontes pouco confiáveis (rede social, site pouco acessado) votam, mas sozinhas não
        # cravam alta/baixa: sem ao menos 1 voto confiável no mesmo sentido, fica na faixa média.
        if abs(L_a) >= TAU and not any(v.confiavel and v.afirmacao == a_idx and v.valor * L_a > 0
                                       for v in dec.votos):
            L_a = math.copysign(TAU * 0.99, L_a)
            dec.travas["sem_fonte_confiavel"] = True
        por_af[a_idx] = L_a
        dec.por_afirmacao.append({"afirmacao": af.texto, "nucleo": af.nucleo, "polaridade": af.polaridade,
                                  "L": round(L_a, 4), "n_votos": sum(1 for v in dec.votos if v.afirmacao == a_idx)})
    # combinação entre afirmações: a mais "falsa" se passa da faixa média; senão a de maior |L|
    L = 0.0
    if por_af:
        mx = max(por_af.values())
        L = mx if mx >= TAU else max(por_af.values(), key=abs)
    dec.log_odds = round(L, 4)
    dec.prob = round(_sig(L), 4)

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
        elif dec.contagem["sustenta"] + dec.contagem["refuta"] == 0:
            dec.motivo = "nenhuma fonte consultada confirma ou contesta a afirmação (só relatos ou fora do tema)"
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
    return dec
