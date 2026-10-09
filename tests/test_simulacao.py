"""Simulação de usuário: só as partes puras (sem rede, sem API). O script em si não roda aqui."""
from eval import simulacao as sim


def _frases():
    fs = []
    for cat, n in (("checadas_falsas", 30), ("verdadeiras", 6), ("fora_da_base", 12), ("antigas_como_atuais", 5)):
        rot = {"verdadeiras": "verdadeiro", "antigas_como_atuais": "enganoso"}.get(cat, "falso")
        fs += [{"id": f"{cat}:{i}", "categoria": cat, "texto": f"{cat} frase {i}", "rotulo": rot,
                "origem": "teste", "url_checagem": None} for i in range(n)]
    return fs


def test_cotas_respeitam_composicao():
    assert sim.cotas(50) == {"checadas_falsas": 20, "verdadeiras": 15, "fora_da_base": 10, "antigas_como_atuais": 5}
    assert sum(sim.cotas(17).values()) == 17


def test_sorteio_reprodutivel_e_sem_completar_categoria():
    a1, f1 = sim.sortear(_frases(), 50, seed=42)
    a2, _ = sim.sortear(_frases(), 50, seed=42)
    a3, _ = sim.sortear(_frases(), 50, seed=7)
    assert [f["id"] for f in a1] == [f["id"] for f in a2]           # mesma seed = mesma rodada
    assert [f["id"] for f in a1] != [f["id"] for f in a3]
    assert f1 == {"verdadeiras": 9}                                   # 6 de 15: falta registrada
    assert sum(1 for f in a1 if f["categoria"] == "checadas_falsas") == 20  # não completou com falsas


def _reg(rotulo, nivel, prob, **kw):
    return {"frase": {"id": "x", "rotulo": rotulo, "categoria": kw.pop("cat", "checadas_falsas"), "texto": "t"},
            "erro": None, "nivel": nivel, "prob": prob, "fontes": [], **kw}


def test_classificar():
    assert sim.classificar(_reg("falso", "alta", 0.86)) == "acerto"
    assert sim.classificar(_reg("falso", "baixa", 0.10)) == "boato_nao_detectado"
    assert sim.classificar(_reg("verdadeiro", "alta", 0.80)) == "falso_alarme"
    assert sim.classificar(_reg("verdadeiro", "baixa", 0.20)) == "acerto"
    assert sim.classificar(_reg("falso", "media", 0.60)) == "inconclusivo"
    assert sim.classificar(_reg("falso", "indeterminada", 0.50)) == "evidencia_insuficiente"
    assert sim.classificar({"frase": {"rotulo": "falso"}, "erro": "HTTP 504"}) == "erro"


def test_extrair_sem_fase_e_marca_nao_implementado():
    rel = {"propensao": "alta", "header": "🔴 Alta propensão de ser fake news", "why_1linha": "w",
           "justificativa": "j", "limitacoes": [],
           "decisao": {"prob": 0.86, "votos": [{"urls": ["https://a.test/1"]}], "vereditos_aplicados": []},
           "fontes": [{"url": "https://a.test/1", "portal_nome": "Lupa", "postura": "REFUTA", "veredito": "FALSO",
                       "corpo_lido": False}]}
    e = sim.extrair(rel)
    assert e["onde_encontrado"] == sim.NAO_IMPLEMENTADO and e["alerta_data"] == sim.NAO_IMPLEMENTADO
    assert e["selos"] == [{"agencia": "Lupa", "selo": "FALSO"}]
    assert e["fontes"][0]["votou"] and not e["fontes"][0]["lida_integralmente"]


def test_verificacoes_de_regras():
    r = _reg("falso", "alta", 0.9, header="🔴 Alta propensão de ser fake news", why="",
             justificativa="Selo da Lupa: FALSO. Também considerado: VERDADEIRO (Boatos.org).",
             serpapi=3, vereditos_aplicados=[{"url": "https://lupa.test/1"}],
             fontes=[{"url": "https://x.test/2", "votou": True, "lida_integralmente": False}])
    r["resultado"] = sim.classificar(r)
    neutro, selo, titulo, web = sim.verificar_regras([r], {"lupa.test/1"})  # ordem fixa das 4 regras
    assert neutro["ok"]                                          # sem "é falso"/"é verdade"
    assert not selo["ok"] and "VERDADEIRO" in selo["violacoes"][0]["detalhe"]
    assert "FALSO" not in selo["violacoes"][0]["detalhe"]        # "Selo da Lupa: FALSO" é aceito
    assert not titulo["ok"]                                      # fonte só de título votou
    assert not web["ok"]                                         # web com selo aplicável da base


def test_neutralidade_reprova_e_falso_na_conclusao():
    r = _reg("falso", "alta", 0.9, header="h", why="", justificativa="Isso é falso, segundo nós.", serpapi=0,
             vereditos_aplicados=[])
    r["resultado"] = sim.classificar(r)
    neutro = sim.verificar_regras([r], set())[0]
    assert not neutro["ok"] and "é falso" in neutro["violacoes"][0]["detalhe"]


def test_relatorio_html_e_indicadores():
    regs = [_reg("falso", "alta", 0.9, cat="checadas_falsas", header="h", why="w", justificativa="j", serpapi=2,
                 tempo_s=20.0, vereditos_aplicados=[]),
            _reg("verdadeiro", "alta", 0.8, cat="verdadeiras", header="h", why="w", justificativa="j", serpapi=3,
                 tempo_s=40.0, vereditos_aplicados=[]),
            {"frase": {"id": "y", "rotulo": "falso", "categoria": "fora_da_base", "texto": "t"}, "erro": "HTTP 504",
             "tempo_s": 180.0}]
    for i, r in enumerate(regs):
        r["frase"]["id"] = f"c{i}"
    d = sim.montar(regs, {"inicio": "agora", "seed": 1, "n": 3, "url": "u", "frases": "f", "faltas": {}}, set())
    g = d["geral"]
    assert (g["total"], g["sucessos"], g["erros"], g["falsos_alarmes"], g["serpapi_total"]) == (3, 2, 1, 1, 5)
    assert g["taxa_acerto"] == 50.0 and g["tempo_medio_s"] == 30.0
    h = sim.html_relatorio(d)
    assert "Casos para revisar (2)" in h and "falso alarme" in h and "id='c2'" in h


def _gerador():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import gerar_frases_simulacao as g
    return g


def test_descricao_do_boato_e_frase_inteira():
    g = _gerador()
    ok = {"titulo": "Simpsons terá episódio sobre manifestações no Brasil #boato",
          "trecho": "Boato – Matt Groening estaria preparando um episódio sobre os protestos no Brasil para a "
                    "próxima temporada da série Os Simpsons. No pico dos protestos… Continue a ler »"}
    assert g.descricao_boato(ok) == ("Matt Groening estaria preparando um episódio sobre os protestos no Brasil "
                                     "para a próxima temporada da série Os Simpsons.")
    emendada = {"titulo": "Boato no Facebook fala de contas e mural",
                "trecho": "Boato – Funcionários do Facebook passarão a dar informações de contas que não postarem "
                          "no mural Nem bem o boato sobre o pagamento… Continue a ler »"}
    assert g.descricao_boato(emendada) is None          # sem ponto entre boato e artigo: descartada
    sem_relacao = {"titulo": "Vacina causa autismo #boato",
                   "trecho": "Boato – A prefeitura anunciou um novo calendário de feiras livres para o bairro."}
    assert g.descricao_boato(sem_relacao) is None        # não fala do boato do título


def test_moldura_determinista_sem_palavras_de_tempo():
    g = _gerador()
    assert g.emoldurar("Café cura câncer", "k1") == g.emoldurar("Café cura câncer", "k1")
    for m in g.MOLDURAS:
        assert not any(p in m.lower() for p in ("hoje", "agora", "ontem"))
    msg, i = g.emoldurar("É verdade que o Pix vai ser taxado?", "k2")
    assert i in g.MOLDURAS_PERGUNTA and msg.count("?") == 1  # pergunta não ganha pergunta extra
