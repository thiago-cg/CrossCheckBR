"""T8 (B5/B6): trava VAGO só com sujeito genérico + independência curada.

B5: "Ibuprofeno piora o quadro de dengue" tem sujeito específico (fármaco/doença)
e é checável — não pode cair na trava VAGO. Sujeito genérico (economia, país,
governo...) com comparativo sem indicador continua vago.

B6: assinatura de agência não casa com crédito de foto ("Foto: Folhapress" não é
republicação Folhapress); subdomínio de participação do leitor
(comentarios1.folha.uol.com.br) não herda a curadoria da Folha.
"""
import asyncio

from factcheck_mvp import corroboracao as co
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.modelo_fake import MockDetector
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import EntradaConsulta
from factcheck_mvp.serpapi_layer import SerpAPIClient


def _pipe():
    return Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=SerpAPIClient(api_key=""), detector=MockDetector())


def _limitacoes(texto):
    rel = asyncio.run(_pipe().executar(
        EntradaConsulta(tipo="titulo", conteudo=texto), usar_llm=False))
    return rel.limitacoes


def _eh_vago(texto):
    return any("vaga" in lim.lower() for lim in _limitacoes(texto))


# ------------------------------------------------------------------ B5: VAGO
def test_ibuprofeno_piora_quadro_dengue_nao_e_vago():
    """Caso real: sujeito específico (fármaco + doença) é checável."""
    assert not _eh_vago("Ibuprofeno piora o quadro de dengue")


def test_sujeito_especifico_com_comparativo_nao_e_vago():
    assert not _eh_vago("A vacina melhorou a proteção contra a dengue")
    assert not _eh_vago("O paracetamol piora o quadro de dengue")


def test_sujeito_generico_com_comparativo_continua_vago():
    assert _eh_vago("A economia do Brasil só piorou")
    assert _eh_vago("O país piorou muito nos últimos anos")
    assert _eh_vago("O governo melhorou a situação do país")
    assert _eh_vago("A vida só piorou")


# ------------------------------------------------- B6: foto não é assinatura
def test_credito_de_foto_folhapress_nao_e_assinatura():
    corpo = ("Reportagem própria sobre a campanha de vacinação. " * 10
             + "\nFoto: Folhapress")
    assert co.agencia_assinada(corpo) is None


def test_credito_de_foto_variacoes_nao_assinam():
    base = "Texto próprio da redação sobre o tema do dia. " * 10
    for credito in ["Foto Folhapress", "FOTO: FOLHAPRESS",
                    "Crédito da foto: Folhapress",
                    "Imagem: Folhapress",
                    "Foto:\nFolhapress",
                    "Foto: Estadão Conteúdo"]:
        assert co.agencia_assinada(base + "\n" + credito) is None, credito


def test_assinatura_real_continua_valendo():
    corpo = ("SÃO PAULO - Balanço da campanha de vacinação. " * 10
             + "\n(Estadão Conteúdo)")
    assert co.agencia_assinada(corpo) == "estadao-conteudo"
    assert co.agencia_assinada(
        "Texto qualquer da matéria.\nCom informações da Agência Brasil") == "agencia-brasil"
    # parágrafo de sindicalização sem marca de foto continua assinando
    assert co.agencia_assinada(
        "Texto da matéria distribuída nacionalmente. " * 10 + "\n(Folhapress)") == "folhapress"


# ------------------------------------------- B6: curada exige host declarado
def test_comentarios_folha_nao_e_curada():
    cat = Catalogo.carregar()
    p = cat.por_url("https://comentarios1.folha.uol.com.br/comentarios/2026/09/x")
    assert p is None
    assert not cat.eh_curado(p)


def test_host_exato_continua_curado_e_editorial_preservado():
    cat = Catalogo.carregar()
    folha = cat.por_url("https://www.folha.uol.com.br/poder/2026/09/x.shtml")
    assert folha is not None and folha["id"] == "folha" and cat.eh_curado(folha)
    # subdomínio editorial (sem marca UGC) continua roteando
    estadao = cat.por_url("https://esportes.estadao.com.br/x")
    assert estadao is not None and estadao["id"] == "estadao"


def test_forum_e_comunidade_tambem_nao_herdam_curadoria():
    cat = Catalogo.carregar()
    c = Catalogo([
        {"id": "jornal", "nome": "Jornal", "tipo": "geral",
         "homepage": "https://www.jornal.com.br/"},
    ])
    assert c.por_url("https://www.jornal.com.br/politica/x")["id"] == "jornal"
    assert c.por_url("https://comentarios.jornal.com.br/x") is None
    assert c.por_url("https://forum.jornal.com.br/x") is None
    assert c.por_url("https://comunidade.jornal.com.br/x") is None
