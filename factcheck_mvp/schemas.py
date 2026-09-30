"""Contratos do MVP (Pydantic v2). Valem para Telegram, API e futura web (RNF05)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

TipoEntrada = Literal["texto", "titulo", "link"]
Propensao = Literal["baixa", "media", "alta", "indeterminada"]
StatusEtapa = Literal["ok", "parcial", "falha", "pulada"]
Polaridade = Literal["afirma", "nega"]
# Postura de uma fonte frente ao NÚCLEO da afirmação (juiz_llm)
Postura = Literal["SUSTENTA", "REFUTA", "RELATA_SEM_ENDOSSO", "NAO_TRATA"]


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EntradaConsulta(BaseModel):
    """RF02/RF03: só texto, título ou link. Nada de mídia."""

    tipo: TipoEntrada
    conteudo: str = Field(..., min_length=3, max_length=20000)
    idioma: str = "pt-BR"


class Afirmacao(BaseModel):
    """`texto` preserva idioma e POLARIDADE do usuário ("A vacina não altera o DNA");
    `nucleo` é a alegação na forma afirmativa ("A vacina altera o DNA"), que é o que
    o juiz e os selos avaliam; `polaridade="nega"` inverte a direção na decisão."""

    texto: str
    indice: int = 0
    nucleo: str = ""
    polaridade: Polaridade = "afirma"
    consulta: str = ""  # 5-8 palavras-chave (PT) para a busca

    def alvo(self) -> str:
        return (self.nucleo or self.texto).strip()


class FonteEvidencia(BaseModel):
    """RF07/RNF03: toda evidência carrega sua fonte rastreável."""

    url: str
    titulo: str = ""
    portal_id: Optional[str] = None
    portal_nome: str = ""
    tipo_fonte: Literal["veredito", "corroboracao", "descoberta"] = "descoberta"
    veredito: Optional[str] = None
    selo_original: Optional[str] = None
    confianca: Optional[float] = None
    # Deep crawl (check #2): prova de corpo lido, não só manchete
    trecho_corpo: Optional[str] = None
    corpo_lido: bool = False
    # Clareza lado-a-lado (check #1): data + quote + tipo p/ tabela
    data_pub: Optional[str] = None
    quote: Optional[str] = None
    tipo_conteudo: Optional[str] = None  # noticia|checagem|opiniao|satira
    # LLM-juiz: resumo da peça frente à afirmação + termômetro (-100..+100)
    resumo_juiz: Optional[str] = None
    score_juiz: Optional[int] = None
    # Relevância DERIVADA da postura do juiz: SUSTENTA/REFUTA/RELATA -> True,
    # NAO_TRATA -> False, não julgado (sem LLM/teto) -> None. False nunca é "útil".
    relevante: Optional[bool] = None
    # Juiz de 4 classes (fase 2): postura frente ao núcleo + citação verificada
    postura: Optional[Postura] = None
    citacao: Optional[str] = None
    citacao_verificada: Optional[bool] = None
    motor_juiz: Optional[str] = None  # llm-juiz:<motor> | fallback-*
    veredito_normalizado: Optional[str] = None  # selos.VEREDITOS
    cluster: Optional[str] = None  # grupo de independência (1 voto por cluster)
    curada: Optional[bool] = None  # fonte do catálogo curado (por URL/aliases)
    afirmacao: Optional[str] = None  # afirmação (texto do usuário) a que a fonte foi julgada


class SinalAnalise(BaseModel):
    """Um motor, um resultado. O agregador nunca vê texto livre, só sinais."""

    motor: str = Field(..., description="llm-juiz | laya | modelo-fake | llm-padroes | corroboracao | veredito-existente")
    rotulo: str
    valor: str
    confianca: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    evidencias: List[str] = Field(default_factory=list)


class EtapaRecibo(BaseModel):
    """RF11/RNF01: cada etapa do pipeline deixa recibo auditável."""

    nome: str
    status: StatusEtapa
    detalhe: str = ""
    fontes: List[str] = Field(default_factory=list)


class RelatorioChecagem(BaseModel):
    """Envelope final. RF12/RNF02: propensão em escala, nunca binário."""

    propensao: Propensao
    justificativa: str
    sinais: List[SinalAnalise] = Field(default_factory=list)
    fontes: List[FonteEvidencia] = Field(default_factory=list)
    etapas: List[EtapaRecibo] = Field(default_factory=list)
    limitacoes: List[str] = Field(default_factory=list)
    perguntas_guia: List[str] = Field(default_factory=list)
    consulta: EntradaConsulta
    gerado_em: str = Field(default_factory=agora_iso)
    versao: str = "mvp-0.2.0"
    # Clareza em segundos (check #1): header + why sempre preenchidos pelo pipeline
    header: str = ""
    why_1linha: str = ""
    # Decisão completa (decisao.Decisao serializada): votos por cluster, vereditos,
    # log-odds, faixas. O nível, o header e a justificativa saem deste objeto.
    decisao: Optional[Dict[str, Any]] = None
