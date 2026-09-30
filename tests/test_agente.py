"""Agente de descoberta: onda 1 determinística, estado por requisição e crítico PÓS-JUIZ.

Sem rede: SerpAPI, LLM (afirmações, juiz, reformulação) e deep crawl são FALSOS. Os testes
de pipeline passam pelo caminho real: onda 1 → dedupe → seleção → crawl → juiz (prompt real,
citação verificada) → crítico → onda extra reescrita pelo LLM → juiz → decidir.
"""
import asyncio
import json
import re
import threading
import time

import pytest

from factcheck_mvp import agente, config, llm, telemetria
from factcheck_mvp import pipeline as pl
from factcheck_mvp import serpapi_layer as camada
from factcheck_mvp.aprofundar import CorpoLido
from factcheck_mvp.catalogo import Catalogo
from factcheck_mvp.indice import Indice
from factcheck_mvp.modelo_fake import MockDetector
from factcheck_mvp.pipeline import Pipeline
from factcheck_mvp.schemas import Afirmacao, EntradaConsulta


# ----------------------------------------------------------------------------- fakes
def _org(url, titulo, snippet="snip"):
    return {"link": url, "title": titulo, "snippet": snippet, "displayed_link": url, "position": 1}


class FakeSerp:
    """SerpAPI falsa (duck-typed): `rota(q) -> [organic]`; conta chamadas; thread-safe."""
    ativo = True
    ultimo_motivo = "ok"
    uso_hoje = 0

    def __init__(self, rota, atraso=0.0):
        self.rota = rota
        self.atraso = atraso
        self.chamadas = []
        self._t = threading.Lock()

    def buscar(self, params):
        with self._t:
            self.chamadas.append(params["q"])
        if self.atraso:
            time.sleep(self.atraso(params["q"]) if callable(self.atraso) else self.atraso)
        r = self.rota(params["q"])
        return None if r is None else {"organic_results": r}

    def engine_de(self, q):
        return "google"

    def result_key(self, q):
        return "organic_results"


def _alvo(i, texto, consulta=""):
    return agente.Alvo(indice=i, texto=texto, consulta=consulta or agente.consulta_padrao(texto))


def _rodar_onda1(alvos, cli, **kw):
    est = agente.EstadoBusca.novo(**kw)
    asyncio.run(agente.onda1(est, cli, alvos))
    return est


# ----------------------------------------------------------------------------- onda 1 / estado
def test_agencias_do_catalogo_incluem_lupa_nova_e_excluem_valorinveste():
    sites = [a["site"] for a in camada.agencias_checagem()]
    assert "agencialupa.org" in sites and "lupa.uol.com.br" in sites
    assert not any("valorinveste" in s or "canalsaude" in s for s in sites)
    assert "g1.globo.com/fato-ou-fake" in sites  # seção de portal amplo entra com caminho


def test_plano_onda1_consulta_depois_agencias_e_max_32_palavras():
    longa = " ".join(f"termo{i}" for i in range(40))
    alvos = [_alvo(0, "Ibuprofeno cura dengue", "ibuprofeno cura dengue"), _alvo(1, longa, longa)]
    plano = agente.plano_onda1(alvos)
    motivos = [m.split(" ")[0] for _, _, m in plano]
    assert motivos == ["consulta", "consulta", "agencias", "agencias"]  # round-robin: (a) de todas antes
    assert plano[0][0]["q"] == "ibuprofeno cura dengue"
    assert "site:agencialupa.org" in plano[2][0]["q"] and "valorinveste" not in plano[2][0]["q"]
    for q, _, _ in plano:
        assert len(q["q"].split()) <= 32, q["q"]


def test_consulta_fallback_usa_termos_de_conteudo():
    al = agente.alvos_de([Afirmacao(texto="O ibuprofeno cura a dengue em 3 dias", consulta="")])[0]
    assert al.consulta and "ibuprofeno" in al.consulta.lower() and " a " not in f" {al.consulta} "
    al2 = agente.alvos_de([Afirmacao(texto="x y", consulta="vacina dna")])[0]
    assert al2.consulta == "vacina dna"


def test_dedupe_acumula_afirmacoes():
    comum = _org("https://www.aosfatos.org/noticias/x/", "Checagem comum")
    cli = FakeSerp(lambda q: [comum, _org(f"https://ex.com/{abs(hash(q))}", "outra")])
    est = _rodar_onda1([_alvo(0, "vacina altera dna"), _alvo(1, "vacina causa autismo")], cli)
    pecas = est.pecas()
    doc = [d for d in pecas if "aosfatos" in d["url"]]
    assert len(doc) == 1 and doc[0]["_afirmacoes"] == {0, 1}


def test_mesma_query_para_duas_afirmacoes_busca_uma_vez():
    cli = FakeSerp(lambda q: [_org("https://ex.com/a", "A")])
    est = _rodar_onda1([_alvo(0, "x", "vacina dna"), _alvo(1, "y", "vacina dna")], cli)
    assert len(cli.chamadas) == 2  # (a) e (b) uma vez cada, atribuídas às duas afirmações
    assert est.pecas()[0]["_afirmacoes"] == {0, 1}


def test_corte_por_afirmacao_preserva_round_robin(monkeypatch):
    monkeypatch.setattr(config, "AGENTE_MAX_POR_AFIRMACAO", 3)

    def rota(q):
        if "site:" in q:
            return []
        if "muitos" in q:
            return [_org(f"https://muitos.com/{i}", f"m{i}") for i in range(8)]
        return [_org(f"https://poucos.com/{i}", f"p{i}") for i in range(2)]
    est = _rodar_onda1([_alvo(0, "muitos resultados", "muitos resultados"),
                        _alvo(1, "poucos resultados", "poucos resultados")], FakeSerp(rota))
    urls = [d["url"] for d in est.pecas()]
    assert urls == ["https://muitos.com/0", "https://poucos.com/0", "https://muitos.com/1",
                    "https://poucos.com/1", "https://muitos.com/2"]


def test_teto_por_requisicao_reserva_onda_extra():
    cli = FakeSerp(lambda q: [])
    est = _rodar_onda1([_alvo(0, "a um", "a um"), _alvo(1, "b dois", "b dois")], cli, teto=4, max_extras=1)
    assert cli.chamadas == ["a um", "b dois"]  # só (a): 2 buscas reservadas p/ ondas extras
    assert est.buscas == 2 and est.restante() == 2
    assert any("reserva" in d for d in est.decisoes)


def test_timeout_preserva_parcial(monkeypatch):
    eventos = []
    monkeypatch.setattr(telemetria, "fallback", lambda onde, motivo, **kw: eventos.append((onde, motivo)))

    def rota(q):
        return [_org("https://lento.com/x", "lento")] if "site:" in q else [_org("https://rapido.com/x", "rápido")]
    cli = FakeSerp(rota, atraso=lambda q: 1.0 if "site:" in q else 0.0)
    est = _rodar_onda1([_alvo(0, "vacina dna", "vacina dna")], cli, timeout_s=0.3)
    assert [d["url"] for d in est.pecas()] == ["https://rapido.com/x"]
    assert est.parar_motivo == "timeout"
    assert any(o == "agente" and "parcial preservado" in m for o, m in eventos)


def test_cap_diario_interrompe():
    class Cap(FakeSerp):
        def buscar(self, params):
            self.chamadas.append(params["q"])
            self.ultimo_motivo = "cap"
            return None
    est = _rodar_onda1([_alvo(0, "vacina dna", "vacina dna")], Cap(lambda q: []))
    assert est.parar_motivo == "cap" and est.pecas() == []
    assert agente.criticar(est, agente.resumir_julgamento(0, []))["decisao"] == "parar"


def test_sem_cliente_nao_busca():
    est = _rodar_onda1([_alvo(0, "x", "x")], None)
    assert est.buscas == 0 and any("sem SerpAPI" in d for d in est.decisoes)


def test_cliente_real_conta_por_requisicao_e_cache(monkeypatch):
    """buscar_ex devolve o motivo DESTA chamada; o estado conta suas buscas, não o uso do cliente."""
    import httpx as _hx

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"organic_results": [{"link": "https://x.com/a", "title": "T"}]}
    monkeypatch.setattr(_hx, "get", lambda *a, **k: R())
    cli = camada.SerpAPIClient(api_key="k")
    cli._uso_n = 50  # outras requisições já gastaram
    est = _rodar_onda1([_alvo(0, "vacina dna", "vacina dna")], cli, teto=9)
    assert est.buscas == 2 and est.buscas_cache == 0
    est2 = _rodar_onda1([_alvo(0, "vacina dna", "vacina dna")], cli, teto=9)
    assert est2.buscas == 2 and est2.buscas_cache == 2  # cache do cliente: não gastou


# ----------------------------------------------------------------------------- crítico / reformulação
def _res(**n):
    base = {"classe": None, "cluster": None, "veredito": None, "titulo": "t", "dominio": "d"}
    return [dict(base, **x) for x in n.get("itens", [])]


def test_critico_regras(monkeypatch):
    ev = []
    monkeypatch.setattr(telemetria, "evento", lambda tipo, **d: ev.append((tipo, d)))
    est = agente.EstadoBusca.novo(teto=9, max_extras=1)
    R = agente.resumir_julgamento
    # veredito de checagem aplicável -> parar
    d = agente.criticar(est, R(0, _res(itens=[{"classe": "RELATA_SEM_ENDOSSO", "veredito": "FALSO", "cluster": "c1"}])))
    assert d["decisao"] == "parar" and "veredito" in d["motivo"]
    # 2 clusters com postura -> parar
    d = agente.criticar(est, R(0, _res(itens=[{"classe": "REFUTA", "cluster": "c1"},
                                              {"classe": "SUSTENTA", "cluster": "c2"}])))
    assert d["decisao"] == "parar" and d["contagens"]["clusters_postura"] == 2
    # 2 peças no MESMO cluster = 1 voto -> nova onda
    d = agente.criticar(est, R(0, _res(itens=[{"classe": "REFUTA", "cluster": "c1"},
                                              {"classe": "REFUTA", "cluster": "c1"}])))
    assert d["decisao"] == "nova_onda" and "1 cluster" in d["motivo"]
    # veredito de página que o juiz disse NAO_TRATA não conta
    d = agente.criticar(est, R(0, _res(itens=[{"classe": "NAO_TRATA", "veredito": "FALSO", "cluster": "c1"}])))
    assert d["decisao"] == "nova_onda" and "fora do tema" in d["motivo"]
    # juiz indisponível -> parar; extras esgotadas -> parar
    assert agente.criticar(est, R(0, []), juiz_disponivel=False)["decisao"] == "parar"
    est.extras[0] = 1
    assert "máximo" in agente.criticar(est, R(0, []))["motivo"]
    assert [t for t, _ in ev] == ["agente"] * 6
    assert {"afirmacao", "decisao", "motivo", "contagens"} <= set(ev[0][1])


def test_reformular_llm_recebe_contexto_e_rejeita_repeticao(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    prompts = []

    def fake(messages, max_tokens, timeout_s, finalidade):
        assert finalidade == "reformular"
        prompts.append(messages[-1]["content"])
        if len(prompts) == 1:
            return json.dumps({"consulta": "Ibuprofeno cura dengue"}), "fake"  # repete: vira retry
        return json.dumps({"consulta": "ibuprofeno dengue site:x.com hemorragia \"anvisa\""}), "fake"
    monkeypatch.setattr(llm, "_local", fake)
    al = _alvo(0, "Ibuprofeno cura dengue em 3 dias", "ibuprofeno cura dengue")
    q, motor = agente.reformular(al, ["ibuprofeno cura dengue"], [("Lula foi preso? É falso", "boatos.org")])
    assert q == "ibuprofeno dengue hemorragia anvisa" and motor.startswith("llm-reformular")
    p = prompts[0]
    assert "Ibuprofeno cura dengue em 3 dias" in p and "- ibuprofeno cura dengue" in p
    assert "Lula foi preso? É falso — boatos.org" in p and '{"consulta": "..."}' in p
    assert "repete" in prompts[1] or len(prompts) == 2


def test_reformular_fallback_marcado(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    fb = []
    monkeypatch.setattr(telemetria, "fallback", lambda onde, motivo, **kw: fb.append(onde))

    def quebra(*a, **k):
        raise llm.ErroLLM("sem modelo")
    monkeypatch.setattr(llm, "_local", quebra)
    al = _alvo(0, "Ibuprofeno cura dengue", "ibuprofeno cura dengue")
    q, motor = agente.reformular(al, ["ibuprofeno cura dengue"], [])
    assert motor == agente.MOTOR_REFORMULAR_FALLBACK and q and q != "ibuprofeno cura dengue"
    assert "agente.reformular" in fb
    assert len(q.split()) <= 32


# ----------------------------------------------------------------------------- pipeline (integração)
REGRAS_IBU = [(r"[^.]*(ibuprofeno[^.]*(é falso|não cura)|(é falso|não cura)[^.]*ibuprofeno)[^.]*", "REFUTA"),
              (r"[^.]*ibuprofeno[^.]*comprov[^.]*", "SUSTENTA")]


def _llm_falso(regras, consulta_nova="ibuprofeno dengue risco hemorragia checagem", chamadas=None):
    def fake(messages, max_tokens, timeout_s, finalidade):
        conteudo = messages[-1]["content"]
        if chamadas is not None:
            chamadas.append(finalidade)
        if finalidade == "afirmacoes":
            texto = conteudo.split('"""')[1].strip()
            return json.dumps([{"afirmacao": texto, "nucleo": texto, "polaridade": "afirma",
                                "consulta": texto.lower()}]), "fake"
        if finalidade == "padroes":
            return "NENHUM", "fake"
        if finalidade == "reformular":
            return json.dumps({"consulta": consulta_nova}), "fake"
        itens = [json.loads(l) for l in conteudo.split("ITENS:\n", 1)[1].splitlines() if l.strip()]
        out = []
        for it in itens:
            texto = f"{it['titulo']}\n{it['trecho']}"
            classe, cit = "NAO_TRATA", ""
            for rx, c in regras:
                m = re.search(rx, texto, re.I)
                if m:
                    classe, cit = c, m.group(0).strip()
                    break
            out.append({"i": it["i"], "classe": classe, "citacao": cit})
        return json.dumps({"itens": out}), "fake"
    return fake


@pytest.fixture
def amb(monkeypatch):
    monkeypatch.setenv("INDICE_CHECAGENS", "0")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "SERP_ESTRATEGIA", "agente")
    monkeypatch.setattr(config, "SERP_ENGINE", "google")
    monkeypatch.setattr(config, "DISCOVERY_MAX_SITES", 0)
    monkeypatch.setattr(config, "AGENTE_MAX_BUSCAS", 9)
    monkeypatch.setattr(config, "AGENTE_MAX_ONDAS_EXTRAS", 1)
    monkeypatch.setattr(llm, "_local", _llm_falso(REGRAS_IBU))
    corpos = {}

    async def fake_aprofundar(cands, catalogo, **kw):
        out = {}
        for d in cands:
            c = corpos.get(d["url"])
            out[d["url"]] = (CorpoLido(url=d["url"], erro="HTTP 403") if c is None else
                             CorpoLido(url=d["url"], final_url=d["url"], trecho_corpo=c[:3000], corpo_lido=True,
                                       texto_completo=c, metodo="fake"))
        return out
    monkeypatch.setattr(pl, "aprofundar", fake_aprofundar)
    ev = []
    orig = telemetria.evento
    monkeypatch.setattr(telemetria, "evento", lambda tipo, **d: (ev.append((tipo, d)), orig(tipo, **d))[1])
    return {"corpos": corpos, "eventos": ev}


def _pag(amb, url, titulo, frase):
    dom = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    contexto = " ".join(f"Apuração própria de {dom}, parágrafo {k}, com detalhes do caso." for k in range(12))
    amb["corpos"][url] = f"{titulo}. {contexto} {frase} Mais contexto de {dom} sem relação direta."
    return _org(url, titulo, frase[:120])


def _rodar(texto, serp, pipe=None):
    pipe = pipe or Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(), serpapi=serp,
                            detector=MockDetector())
    return pipe.executar(EntradaConsulta(tipo="titulo", conteudo=texto))


def _etapa(rel, nome):
    return next((e for e in rel.etapas if e.nome == nome), None)


def test_critico_nao_para_com_checagem_fora_do_tema_e_onda_extra_acha_evidencia(amb):
    """Sonda S1: o site: devolve um boatos.org sobre OUTRO assunto. O antigo crítico parava por
    ser host de checagem; agora o juiz diz NAO_TRATA e o crítico pede a onda extra."""
    fora = _pag(amb, "https://www.boatos.org/politica/lula-foi-preso.html", "Lula foi preso? É falso",
                "É falso que Lula foi preso na semana passada, diz a PF.")
    bolo = _pag(amb, "https://exame.com/casa/receita-bolo-cenoura/", "Receita de bolo de cenoura",
                "O bolo de cenoura leva três ovos e cobertura de chocolate.")
    lupa = _pag(amb, "https://www.agencialupa.org/verificacao/2026/09/ibuprofeno-dengue/",
                "Ibuprofeno não cura dengue", "É falso que o ibuprofeno cura dengue; o remédio aumenta o risco.")
    g1 = _pag(amb, "https://g1.globo.com/saude/noticia/2026/09/ibuprofeno-dengue.ghtml",
              "Médicos alertam sobre dengue", "Especialistas explicam que o ibuprofeno não cura dengue e é contraindicado.")
    nova = "ibuprofeno dengue risco hemorragia checagem"

    def rota(q):
        if q == nova:
            return [lupa, g1]
        return [fora] if "site:" in q else [bolo]
    serp = FakeSerp(rota)
    rel = asyncio.run(_rodar("Ibuprofeno cura dengue em 3 dias", serp))
    assert len(serp.chamadas) == 3 and serp.chamadas[-1] == nova  # 2 da onda 1 + 1 reescrita pelo LLM
    crit = [d for t, d in amb["eventos"] if t == "agente"]
    assert crit[0]["decisao"] == "nova_onda" and crit[0]["contagens"]["NAO_TRATA"] == 2
    assert crit[-1]["decisao"] == "parar"  # depois da onda extra: 2 clusters refutam
    fontes = {f.url: f.postura for f in rel.fontes}
    assert fontes["https://www.boatos.org/politica/lula-foi-preso.html"] == "NAO_TRATA"
    assert fontes["https://www.agencialupa.org/verificacao/2026/09/ibuprofeno-dengue/"] == "REFUTA"
    assert rel.propensao == "alta", rel.justificativa
    assert _etapa(rel, "agente-onda-2") is not None and _etapa(rel, "agente-critico") is not None
    ondas = [d for t, d in amb["eventos"] if t == "etapa" and d.get("nome") == "agente-onda"]
    assert [d["onda"] for d in ondas] == [1, 1, 2] and all("query" in d and "n_novos" in d for d in ondas)
    assert ondas[-1]["n_novos"] == 2


def test_onda_extra_quando_zero_resultados(amb):
    lupa = _pag(amb, "https://lupa.uol.com.br/jornalismo/2026/09/ibuprofeno/", "Ibuprofeno e dengue",
                "É falso que o ibuprofeno cura dengue, dizem infectologistas.")
    nova = "ibuprofeno dengue risco hemorragia checagem"
    serp = FakeSerp(lambda q: [lupa] if q == nova else [])
    rel = asyncio.run(_rodar("Ibuprofeno cura dengue", serp))
    crit = [d for t, d in amb["eventos"] if t == "agente"]
    assert crit[0]["decisao"] == "nova_onda" and "0 fontes julgadas" in crit[0]["motivo"]
    assert len(serp.chamadas) == 3
    assert any(f.postura == "REFUTA" for f in rel.fontes)


def test_sem_onda_extra_com_dois_clusters_com_postura(amb, monkeypatch):
    a = _pag(amb, "https://www.aosfatos.org/noticias/ibuprofeno-dengue/", "Ibuprofeno e dengue",
             "É falso que o ibuprofeno cura dengue, segundo a Anvisa.")
    b = _pag(amb, "https://www.bbc.com/portuguese/articles/ibu123", "O que se sabe sobre dengue",
             "Médicos reforçam que o ibuprofeno não cura dengue.")
    chamadas = []
    monkeypatch.setattr(llm, "_local", _llm_falso(REGRAS_IBU, chamadas=chamadas))
    serp = FakeSerp(lambda q: [b] if "site:" in q else [a])
    rel = asyncio.run(_rodar("Ibuprofeno cura dengue", serp))
    assert len(serp.chamadas) == 2  # só a onda 1
    crit = [d for t, d in amb["eventos"] if t == "agente"]
    assert len(crit) == 1 and crit[0]["decisao"] == "parar" and crit[0]["contagens"]["clusters_postura"] == 2
    assert "reformular" not in chamadas
    assert _etapa(rel, "agente-onda-2") is None and rel.propensao == "alta"


def test_url_da_onda_extra_ja_vista_ganha_a_afirmacao(amb):
    """Dedupe acumula também entre ondas: a URL já julgada p/ a af0 é julgada de novo só se
    vier para outra afirmação; para a mesma afirmação não entra de novo no juiz."""
    fora = _pag(amb, "https://exame.com/casa/receita-bolo-cenoura/", "Receita de bolo",
                "O bolo leva três ovos.")
    nova = "ibuprofeno dengue risco hemorragia checagem"
    serp = FakeSerp(lambda q: [fora])
    rel = asyncio.run(_rodar("Ibuprofeno cura dengue", serp))
    assert len(serp.chamadas) == 3 and serp.chamadas[-1] == nova
    julg = [d for t, d in amb["eventos"] if t == "fonte" and d.get("estagio") == "juiz"]
    assert len(julg) == 1  # a URL repetida da onda extra não foi julgada duas vezes
    assert rel.propensao == "indeterminada"


def test_estado_nao_vaza_entre_execucoes_concorrentes(amb, monkeypatch):
    """Sonda S6: duas checagens concorrentes no MESMO cliente. Cada uma conta só as próprias
    buscas (teto 2 cada) e só vê as próprias fontes."""
    monkeypatch.setattr(config, "AGENTE_MAX_BUSCAS", 2)
    monkeypatch.setattr(config, "AGENTE_MAX_ONDAS_EXTRAS", 0)

    def rota(q):
        chave = "cafe" if "café" in q.lower() else "vacina"
        return [_org(f"https://{chave}.com/{'b' if 'site:' in q else 'a'}/materia-1", f"{chave} matéria")]
    serp = FakeSerp(rota, atraso=0.05)
    pipe = Pipeline(Catalogo.carregar(), Indice.de_checagens([]), Indice(), serpapi=serp, detector=MockDetector())

    async def ambos():
        return await asyncio.gather(_rodar("Café cura câncer", serp, pipe), _rodar("Vacina altera o DNA", serp, pipe))
    r1, r2 = asyncio.run(ambos())
    assert len(serp.chamadas) == 4
    for rel, chave in ((r1, "cafe"), (r2, "vacina")):
        det = _etapa(rel, "descoberta-agente").detalhe
        assert det.startswith("Agente onda 1: 2/2 busca(s)"), det
        assert rel.fontes and all(chave in f.url for f in rel.fontes)
