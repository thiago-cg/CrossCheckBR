"""T5 (B1b): BERTimbau/mock mede a CREDIBILIDADE da página (D1).

Só páginas com corpo lido recebem `p["bert"]`; `analisar_pagina` divide em
blocos de 192 tokens (máx 4) e tira a média. Mock, sem rede, sem torch.
"""
import asyncio

import pytest

from factcheck_mvp import telemetria
from factcheck_mvp import pipeline as pl
from factcheck_mvp.aprofundar import CorpoLido
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.modelo_fake import BERT_MAX_BLOCOS, BERT_MAX_LENGTH, MockDetector
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import Afirmacao


@pytest.fixture
def amb(monkeypatch):
    ev = []
    orig = telemetria.evento
    monkeypatch.setattr(telemetria, "evento",
                        lambda tipo, **d: (ev.append((tipo, d)), orig(tipo, **d))[1])
    return {"eventos": ev}


class _SerpInativa:
    ativo = False
    ultimo_motivo = "sem-chave"


def _pipe():
    return Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(),
                    serpapi=_SerpInativa(), detector=MockDetector())


def test_ler_so_paginas_lidas_recebem_bert(amb, monkeypatch):
    """Só páginas com corpo_lido ganham `bert` + evento `bert_pagina`; só-título não."""
    afs = [Afirmacao(texto="Ibuprofeno cura dengue", nucleo="ibuprofeno cura dengue",
                     consulta="ibuprofeno dengue")]
    lida = "https://lida.test/noticia/1"
    so_titulo = "https://sotitulo.test/noticia/2"
    pecas = [{"url": lida, "titulo": "Lida", "snippet": "s", "afs": {0}},
             {"url": so_titulo, "titulo": "Só título", "snippet": "s", "afs": {0}}]

    async def fake_aprofundar(cands, catalogo, **kw):
        out = {}
        for d in cands:
            if d["url"] == lida:
                corpo = "Texto apurado com vários parágrafos sobre o caso. " * 20
                out[d["url"]] = CorpoLido(url=d["url"], final_url=d["url"],
                                          trecho_corpo=corpo[:3000], corpo_lido=True,
                                          texto_completo=corpo, metodo="fake")
            else:
                out[d["url"]] = CorpoLido(url=d["url"], erro="HTTP 404")
        return out

    monkeypatch.setattr(pl, "aprofundar", fake_aprofundar)
    n_lidas, n_alvo = asyncio.run(_pipe()._ler(afs, pecas, pl._nada))
    assert (n_lidas, n_alvo) == (1, 2)

    por_url = {p["url"]: p for p in pecas}
    assert por_url[lida].get("corpo_lido") is True
    bert = por_url[lida].get("bert")
    assert bert is not None and {"prob_fake", "modelo"} <= set(bert)
    assert 0.0 <= bert["prob_fake"] <= 1.0
    assert bert["modelo"] == MockDetector.nome
    assert "bert" not in por_url[so_titulo]

    evs = [d for t, d in amb["eventos"] if t == "bert_pagina"]
    assert [d["url"] for d in evs] == [lida]
    assert evs[0]["prob_fake"] == bert["prob_fake"]


def test_analisar_pagina_media_de_blocos_192(monkeypatch):
    """500 tokens → 3 blocos (≤192 cada); prob_fake = média dos blocos."""
    assert (BERT_MAX_LENGTH, BERT_MAX_BLOCOS) == (192, 4)
    det = MockDetector()
    chamadas = []
    probs = [0.2, 0.4, 0.6]

    def fake_analisar(texto):
        chamadas.append(texto)
        return {"prob_fake": probs[len(chamadas) - 1], "modelo": det.nome, "mock": True}

    monkeypatch.setattr(det, "analisar", fake_analisar)
    corpo = " ".join(f"palavra{i}" for i in range(500))
    r = det.analisar_pagina("", corpo)
    assert len(chamadas) == 3, len(chamadas)
    assert all(len(c.split()) <= 192 for c in chamadas), [len(c.split()) for c in chamadas]
    assert r["prob_fake"] == round(sum(probs) / len(probs), 3)
    assert r["modelo"] == det.nome and r["mock"] is True


def test_analisar_pagina_teto_4_blocos():
    """1000 tokens → capa em 4 blocos (o resto é ignorado)."""
    det = MockDetector()
    n_chamadas = [0]
    orig = MockDetector.analisar

    def contadora(self, texto):
        n_chamadas[0] += 1
        assert len(texto.split()) <= 192
        return orig(self, texto)

    det.analisar = contadora.__get__(det, MockDetector)
    corpo = " ".join(f"palavra{i}" for i in range(1000))
    r = det.analisar_pagina("Título", corpo)
    assert n_chamadas[0] == 4, n_chamadas
    assert 0.0 <= r["prob_fake"] <= 1.0
