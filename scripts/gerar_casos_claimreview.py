"""Gera eval/casos_claimreview.jsonl a partir das checagens rotuladas pelas agências.

Uso: python3 scripts/gerar_casos_claimreview.py

* `fora_do_indice` (MÉTRICA PRINCIPAL, mede a busca aberta): checagens recentes
  (≈ últimos 30 dias) EXCLUÍDAS de data/checagens.jsonl — junto com as checagens
  da mesma afirmação feitas por outras agências (cluster) — e listadas em
  data/checagens_holdout_excluidas.json (o ingestor nunca as reinsere).
* `no_indice` (métrica secundária, mede o atalho do índice): a checagem continua
  no índice; o caso mede "o sistema acha e usa a checagem certa" (vazamento
  intencional, documentado na tag e na nota).

A entrada é a afirmação REESCRITA como um usuário escreveria (à mão, abaixo),
nunca o título literal. Rótulo = veredito da agência (FALSO→falso, ENGANOSO→
enganoso, VERDADEIRO→verdadeiro, SEM_EVIDENCIA→sem_evidencia). Quando duas
agências divergem (ex.: FALSO × ENGANOSO), fica o rótulo mais fraco.
Split determinístico: sha1(id) % 10 < 7 → dev, senão holdout.
Idempotente: relê as excluídas antes de resolver.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from factcheck_mvp import ingestor as ing  # noqa: E402

ARQ_CASOS = RAIZ / "eval" / "casos_claimreview.jsonl"

ROTULO = {"FALSO": "falso", "ENGANOSO": "enganoso", "VERDADEIRO": "verdadeiro", "SEM_EVIDENCIA": "sem_evidencia"}
ESPERADO = {"falso": ["alta"], "enganoso": ["alta", "media"], "verdadeiro": ["baixa"],
            "sem_evidencia": ["indeterminada", "media"]}
FORCA = {"falso": 3, "enganoso": 2, "sem_evidencia": 1, "verdadeiro": 0}

# (id, [(agencia, trecho da afirmacao_checada ou do título), …] = cluster, entrada do usuário)
# O 1º item do cluster é a checagem principal (vai na nota). Demais: mesma afirmação em outras agências.
FORA_DO_INDICE = [
    ("renner_renan_substituto_rick", [("boatos", "Renner tenha anunciado Renan")],
     "Vi que o Renner já anunciou o Renan como substituto do Rick na dupla"),
    ("rick_caixao_de_ouro", [("boatos", "caixão de ouro")],
     "Rick foi enterrado num caixão de ouro que custou 1 milhão de reais?"),
    ("janja_nem_deus_tira_eleicao", [("boatos", "nem Deus tira essa eleição")],
     "a Janja falou num comício no Piauí que nem Deus tira essa eleição do Lula"),
    ("romario_torcida_flamengo_flavio", [("boatos", "Romário tenha dito")],
     "Romário disse que a torcida do Flamengo inteira vai votar no Flávio Bolsonaro"),
    ("flamengo_protesto_lula_bets", [("boatos", "Torcida do Flamengo tenha feito protesto")],
     "vi no zap que a torcida do Flamengo fez protesto contra o Lula por causa das bets"),
    ("arcebispo_aparecida_voto_lula", [("estadao-verifica", "Arcebispo Orlando Brandes")],
     "O arcebispo de Aparecida pediu pros fiéis votarem no Lula durante a missa"),
    ("radar_amazonia_narcoterroristas", [("estadao-verifica", "Radar destruído por narco")],
     "narcoterroristas destruíram um radar na Amazônia com ataque de drone"),
    ("hytalo_santos_morreu", [("boatos", "Hytalo Santos tenha morrido")],
     "morreu o Hytalo Santos, acabou de sair a notícia"),
    ("daniela_lima_lula_desiste", [("boatos", "Daniela Lima revelou")],
     "A jornalista Daniela Lima revelou que o Lula vai desistir da candidatura"),
    ("delegacao_lula_expulsa_onu", [("boatos", "Delegação de Lula tenha sido convidada")],
     "a delegação do Lula foi expulsa da ONU no meio do discurso do Netanyahu"),
    ("lula_nao_acredita_em_deus", [("boatos", "Lula disse que não acredita em Deus")],
     "Lula disse num discurso no Rio que não acredita em Deus"),
    ("avioes_inflaveis_passagem_barata", [("boatos", "aviões infláveis")],
     "o governo liberou avião inflável com passagem 70% mais barata"),
    ("evangelicos_fogo_nossa_senhora", [("boatos", "fogo em imagem de Nossa Senhora")],
     "bolsonaristas evangélicos tacaram fogo numa imagem de Nossa Senhora Aparecida"),
    ("nikolas_virgem_maria", [("boatos", "Nikolas Ferreira disse que Virgem Maria")],
     "Nikolas Ferreira falou que a Virgem Maria foi estuprada pelo Espírito Santo"),
    ("lula_janja_7_bi_viagens", [("lupa", "Lula gastou R$ 7,35 bilhões")],
     "Lula e Janja já gastaram mais de 7 bilhões em viagens, é verdade?"),
    ("voto_prova_de_vida_inss", [("fato-ou-fake", "prova de vida automática"),
                                 ("boatos", "como prova de vida para aposentados")],
     "Quem votar na eleição de 2026 já faz a prova de vida do INSS automaticamente"),
    ("lula_acabar_bets_que_criou", [("estadao-verifica", "acabar com as bets que ele mesmo criou")],
     "o Lula quer acabar com as bets, mas foi ele mesmo que criou elas"),
    ("miojo_lagartixa", [("boatos", "miojo feito com lagartixas")],
     "tem vídeo mostrando fábrica de miojo feito com lagartixa e calango"),
    ("flavio_tirar_padroeira", [("boatos", "retirar Nossa Senhora Aparecida como Padroeira"),
                                ("aos-fatos", "Boatos sobre Nossa Senhora Aparecida e Flávio"),
                                ("estadao-verifica", "rebaixar")],
     "Flávio Bolsonaro vai tirar Nossa Senhora Aparecida de padroeira do Brasil"),
    ("sacani_idosos_decidem_eleicao", [("boatos", "Sérgio Sacani")],
     "o Sérgio Sacani disse que são os idosos que vão decidir a eleição de 2026"),
    ("vorcaro_pagou_dino", [("lupa", "Pagamentos de Vorcaro a Flávio Dino")],
     "o Vorcaro pagou o ministro Flávio Dino, o nome dele apareceu nos documentos apreendidos"),
    ("video_lula_ministros_stf_atual", [("estadao-verifica", "Vídeo que mostra Lula com ministros do STF")],
     "vídeo mostra o Lula reunido com os ministros do STF agora, no meio da crise da Corte"),
    ("lula_pobre_morrer_cortando_cana", [("estadao-verifica", "pobre tinha que morrer cortando cana"),
                                         ("aos-fatos", "morrer cortando cana")],
     "Lula falou que pobre tem que morrer cortando cana"),
    ("flavio_camiseta_nordestinos", [("fato-ou-fake", "camisa com ofensas a nordestinos"),
                                     ("lupa", "camiseta com frase contra nordestinos"),
                                     ("boatos", "camiseta com frase contra nordestinos")],
     "foto do Flávio Bolsonaro usando camiseta xingando os nordestinos"),
    ("fachin_dedo_moraes", [("aos-fatos", "Edson Fachin apontando o dedo")],
     "foto mostra o Fachin apontando o dedo na cara do Moraes numa discussão"),
    ("kit_chuca_sus", [("aos-fatos", "kit chuca no SUS"), ("boatos", "Kit Chuca")],
     "o governo começou a distribuir kit chuca no SUS"),
    ("moraes_aviao_vorcaro_verdadeira", [("comprova", "Não é Moraes no avião de Vorcaro")],
     "a imagem do Moraes descendo do avião ligado ao Vorcaro é verdadeira mesmo"),
    ("ives_gandra_voto_lula", [("comprova", "Ives Gandra")],
     "o jurista Ives Gandra escreveu que votar no Lula não é ser de esquerda nem de direita"),
    ("cr7_compra_remo", [("boatos", "Clube do Remo"), ("boatos", "compra do Santa Cruz")],
     "o Cristiano Ronaldo anunciou que comprou o Clube do Remo"),
    ("cantor_daniel_pai_nosso_lula", [("boatos", "Cantor Daniel tenha criticado Lula")],
     "o cantor Daniel criticou o Lula com uma frase sobre o Pai Nosso"),
    ("michelle_100_milhoes_vorcaro", [("boatos", "Michelle Bolsonaro tenha pedido R$ 100 milhões")],
     "Michelle Bolsonaro pediu 100 milhões pro Vorcaro por mensagem no WhatsApp"),
    ("flavio_impeachment_dino", [("lupa", "Flávio Bolsonaro protocolou pedido de impeachment de Dino"),
                                 ("aos-fatos", "Flávio Bolsonaro protocola impeachment de Flávio Dino")],
     "Flávio Bolsonaro protocolou pedido de impeachment do ministro Flávio Dino"),
    ("lula_garis_nao_da_orgulho", [("fato-ou-fake", "Lula menosprezou garis"),
                                   ("lupa", "Fala de Lula sobre garis"),
                                   ("comprova", "ser lixeiro")],
     "Lula disse que ser gari não é profissão que dá orgulho"),
    ("pf_dark_horse_sem_corrupcao", [("estadao-verifica", "não houve corrupção no filme Dark"),
                                     ("aos-fatos", "Dark Horse na tentativa de tumultuar")],
     "a PF concluiu que não teve corrupção nenhuma no filme Dark Horse"),
    ("comicio_lula_porto_alegre_fracasso", [("estadao-verifica", "Comício de Lula em Porto Alegre")],
     "o comício do Lula em Porto Alegre foi um fracasso, praça vazia"),
    ("medico_robo_fugindo", [("e-farsas", "Médico-robô")],
     "vídeo de um médico robô fugindo do hospital"),
    ("macete_urna_bolsonaro", [("comprova", "macete nesse ano pra votar em JAIR")],
     "tem um macete na urna esse ano pra votar no Bolsonaro"),
    ("chico_cesar_proibiu_direita", [("boatos", "Chico César tenha proibido")],
     "Chico César proibiu gente de direita de ouvir e cantar as músicas dele"),
    ("jn_soltura_vorcaro", [("boatos", "Jornal Nacional que anuncia soltura de Daniel Vorcaro")],
     "o Jornal Nacional anunciou que o Vorcaro foi solto"),
    ("bolsa_familia_15_aposentadoria_39", [("boatos", "reajuste do Bolsa Família de 15%"),
                                           ("lupa", "Aposentadorias tiveram alta de até 24,5%")],
     "Bolsa Família teve reajuste de 15% e a aposentadoria só 3,9%"),
    ("audio_renan_santos_vorcaro", [("lupa", "Áudio em que Renan Santos fala com Vorcaro"),
                                    ("comprova", "Renan Santos teria enviado para Vorcaro")],
     "vazou um áudio do Renan Santos falando com o Vorcaro"),
    ("dino_moraes_monitorou_mendonca", [("lupa", "Dino revelou que Moraes ordenou")],
     "Dino revelou que o Moraes mandou monitorar o André Mendonça escondido"),
    ("lula_salario_minimo_3_mil", [("estadao-verifica", "Lula planeje elevar salário mínimo"),
                                   ("comprova", "Lula irá aumentar o salário mínimo")],
     "Lula vai aumentar o salário mínimo pra 3 mil reais se ganhar a eleição"),
    ("flavio_salario_minimo_3_mil", [("fato-ou-fake", "Flávio Bolsonaro prometendo salário mínimo"),
                                     ("lupa", "vídeo de Flávio Bolsonaro prometendo salário mínimo"),
                                     ("comprova", "Flávio Bolsonaro irá aumentar o salário mínimo")],
     "tem um vídeo do Flávio Bolsonaro prometendo salário mínimo de 3 mil"),
    ("gilmar_reuniao_secreta_vorcaro", [("aos-fatos", "Reunião secreta")],
     "Gilmar Mendes teve uma reunião secreta com o Daniel Vorcaro"),
    ("el_nino_catastrofe_setembro", [("comprova", "El Niño causará em setembro")],
     "um pastor avisou que o El Niño vai causar catástrofe no Sul e seca gigante agora em setembro"),
    ("rebeca_abravanel_pato_separaram", [("boatos", "Rebeca Abravanel e Alexandre Pato")],
     "Rebeca Abravanel e o Alexandre Pato se separaram depois de oito anos"),
    ("pf_compra_votos_lula_para", [("boatos", "comprando votos para Lula no Pará"),
                                   ("estadao-verifica", "compra de voto circula")],
     "a PF prendeu gente comprando voto pro Lula no Pará"),
    ("ibge_trafico_no_pib", [("e-farsas", "tráfico de drogas no cálculo do PIB")],
     "o IBGE vai colocar o tráfico de drogas no cálculo do PIB"),
    ("sandra_bullock_nicole_kidman", [("boatos", "Sandra Bullock e Nicole Kidman")],
     "Sandra Bullock e Nicole Kidman assumiram que estão namorando"),
    ("virginia_vini_gravidez", [("fato-ou-fake", "Virginia Fonseca e Vini Jr.")],
     "Virginia e o Vini Jr postaram fotos anunciando gravidez"),
    ("xi_jinping_avc", [("boatos", "Xi Jinping sofreu um AVC")],
     "Xi Jinping teve um AVC na Índia e está internado na China"),
    ("moto_placa_dianteira", [("boatos", "placa dianteira")],
     "a partir de 1º de outubro moto vai ter que usar placa na frente também"),
    ("filha_de_lula_presa", [("lupa", "Filha de Lula foi presa")],
     "a filha do Lula foi presa pela Polícia Federal"),
    ("flavio_fugiu_eua", [("comprova", "Flávio Bolsonaro fugiu para os EUA")],
     "o Flávio Bolsonaro fugiu pros Estados Unidos porque está sendo investigado pela PF"),
    ("hackers_invadiram_urnas_teste", [("boatos", "35 hackers"),
                                       ("fato-ou-fake", "Urna eletrônica foi invadida por hackers"),
                                       ("lupa", "Hackers não invadiram urnas")],
     "35 hackers conseguiram invadir as urnas eletrônicas no teste do TSE"),
    ("oreo_petroleo", [("lupa", "Biscoito Oreo")],
     "biscoito Oreo tem petróleo na receita"),
    ("codigo_fonte_urnas_moraes", [("fato-ou-fake", "Código-fonte de urnas eletrônicas foi alterado")],
     "alteraram o código-fonte das urnas e o original está com o Alexandre de Moraes"),
    ("vaca_trem_india_levanta", [("fato-ou-fake", "Vaca se levanta")],
     "vídeo de uma vaca que é atingida por um trem na Índia e levanta depois"),
]

NO_INDICE = [
    ("estribo_caminhao_lixo", [("fato-ou-fake", "estribos em caminhões")],
     "o governo proibiu os garis de andar no estribo do caminhão de lixo"),
    ("trump_capturar_lula", [("lupa", "Trump declarou em vídeo que vai capturar Lula")],
     "Trump falou num vídeo que vai capturar o Lula"),
    ("whatsapp_cobrar_mensagens", [("lupa", "WhatsApp cobrará usuários")],
     "o WhatsApp vai começar a cobrar de todo mundo por mensagem enviada"),
    ("ivermectina_70_covid", [("lupa", "ivermectina tenha 70%")],
     "ivermectina tem 70% de eficácia contra covid"),
    ("toyota_corolla_brasil", [("fato-ou-fake", "Toyota deixou de fabricar Corolla")],
     "a Toyota parou de fabricar o Corolla aqui no Brasil"),
    ("china_acai_amapa", [("lupa", "safra de açaí do Amapá")],
     "a China comprou todo o açaí do Amapá até 2031"),
    ("lula_nordestino_analfabeto", [("fato-ou-fake", "povo nordestino é analfabeto")],
     "Lula disse que ganhou do Bolsonaro em 2022 porque o nordestino é analfabeto"),
    ("fauci_cadeira_eletrica", [("lupa", "Fauci poderá ser condenado")],
     "o Fauci vai pra cadeira elétrica por causa do que fez na pandemia"),
    ("stf_idade_minima_aposentadoria", [("fato-ou-fake", "STF derrubou idade mínima")],
     "o STF acabou com a idade mínima pra se aposentar"),
    ("cucurella_taca_sacola", [("e-farsas", "Cucurella levou a Copa"), ("fato-ou-fake", "Cucurella carregou")],
     "o Cucurella levou a taça da Copa numa sacola de plástico"),
    ("semaforos_derreteram_europa", [("lupa", "Semáforos foram derretidos"), ("fato-ou-fake", "Semáforos derreteram")],
     "semáforos derreteram com a onda de calor na Europa"),
    ("vulcao_havai_lava_300m", [("fato-ou-fake", "fonte de lava de 300 metros")],
     "tem vídeo de uma fonte de lava de 300 metros num vulcão no Havaí, é real"),
    ("banho_de_fogo_russia", [("fato-ou-fake", "banho com fogo")],
     "homem toma banho de fogo numa fonte de água natural na Rússia"),
    ("zeca_pagodinho_apoio_flavio", [("lupa", "Zeca Pagodinho declarou apoio")],
     "Zeca Pagodinho declarou apoio ao Flávio Bolsonaro"),
    ("internacao_psiquiatrica_anula_dividas", [("lupa", "Internação psiquiátrica anula dívidas"),
                                              ("fato-ou-fake", "Internação em hospital psiquiátrico anula")],
     "quem se interna em hospital psiquiátrico tem as dívidas anuladas"),
    ("chips_taiwan_fraude_urnas", [("fato-ou-fake", "Chips importados de Taiwan")],
     "usaram chips importados de Taiwan pra fraudar as urnas"),
    ("eua_prender_lula_iminente", [("lupa", "Prisão de Lula pelos EUA")],
     "os Estados Unidos vão prender o Lula a qualquer momento"),
    ("ipva_cadeira_de_rodas", [("e-farsas", "IPVA de cadeira de rodas")],
     "o governo federal vai cobrar IPVA de cadeira de rodas"),
]


def _split(cid: str) -> str:
    return "dev" if int(hashlib.sha1(cid.encode()).hexdigest(), 16) % 10 < 7 else "holdout"


def _resolver(regs, agencia, trecho):
    t = trecho.casefold()
    achados = [r for r in regs if r.get("agencia") == agencia and
               (t in (r.get("afirmacao_checada") or "").casefold() or t in (r.get("titulo") or "").casefold())]
    com_v = [r for r in achados if r.get("veredito")]
    achados = com_v or achados
    if len(achados) != 1:
        raise SystemExit(f"'{agencia}: {trecho}' casou {len(achados)} registros: "
                         f"{[r.get('titulo') for r in achados][:5]}")
    return achados[0]


def _caso(cid, cluster, entrada, tag):
    rot = min((ROTULO[r["veredito"]] for r in cluster if r.get("veredito") in ROTULO), key=FORCA.get)
    p = cluster[0]
    vereditos = sorted({f"{r['agencia']}:{r['veredito']}" for r in cluster})
    nota = (f"{p['url']} | {p['agencia']} selo '{p.get('selo_original')}' ({p.get('origem')}) sobre "
            f"'{p.get('afirmacao_checada')}'")
    if len(cluster) > 1:
        nota += f" | cluster: {vereditos}"
    if tag == "no_indice":
        nota += (" | NO ÍNDICE: a própria checagem está em data/checagens.jsonl — mede se o atalho acha e usa "
                 "a checagem certa (vazamento intencional).")
    else:
        nota += " | FORA DO ÍNDICE: checagem(ões) excluída(s) de checagens.jsonl — mede a busca aberta."
    return {"id": f"cr_{cid}", "entrada": {"tipo": "texto", "conteudo": entrada}, "rotulo": rot,
            "esperado": ESPERADO[rot], "tags": ["claimreview", p["agencia"], tag],
            "split": _split(f"cr_{cid}"), "origem": "claimreview", "nota": nota,
            "urls_checagem": [r["url"] for r in cluster], "data_checagem": (p.get("data_pub") or "")[:10]}


def main() -> int:
    ativos = ing.carregar_jsonl(ing.ARQ_CHECAGENS)
    excluidas_antes = []
    if ing.ARQ_EXCLUIDAS.exists():
        excluidas_antes = json.loads(ing.ARQ_EXCLUIDAS.read_text(encoding="utf-8")).get("checagens", [])
    todos = ing.mesclar(ativos, [{k: v for k, v in r.items() if k != "caso_id"} for r in excluidas_antes])

    casos, excluir = [], {}
    for tag, lista in (("fora_do_indice", FORA_DO_INDICE), ("no_indice", NO_INDICE)):
        for cid, sels, entrada in lista:
            cluster = [_resolver(todos, ag, tr) for ag, tr in sels]
            casos.append(_caso(cid, cluster, entrada, tag))
            if tag == "fora_do_indice":
                for r in cluster:
                    excluir[ing.chave_url(r["url"])] = {**r, "caso_id": f"cr_{cid}"}

    ids = [c["id"] for c in casos]
    assert len(ids) == len(set(ids)), "id duplicado"
    no_idx_urls = {ing.chave_url(u) for c in casos if "no_indice" in c["tags"] for u in c["urls_checagem"]}
    assert not (no_idx_urls & set(excluir)), "checagem no_indice também excluída"

    ing.ARQ_EXCLUIDAS.write_text(json.dumps({
        "nota": ("Checagens EXCLUÍDAS de propósito de data/checagens.jsonl para os casos `fora_do_indice` "
                 "de eval/casos_claimreview.jsonl (medem a busca aberta sem o atalho do índice). O ingestor "
                 "lê este arquivo e nunca as reinsere. Gerado por scripts/gerar_casos_claimreview.py."),
        "gerado_em": date.today().isoformat(),
        "checagens": sorted(excluir.values(), key=lambda r: (r["caso_id"], r["url"])),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    restantes = ing.mesclar(todos, [], excluidas=set(excluir))
    ing.gravar_jsonl(ing.ARQ_CHECAGENS, restantes)

    with ARQ_CASOS.open("w", encoding="utf-8") as f:
        f.write("# Gerado por scripts/gerar_casos_claimreview.py — rótulos humanos das agências (ClaimReview/"
                "selo/manchete). fora_do_indice = métrica principal; no_indice = atalho (vazamento intencional).\n")
        for c in casos:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    from collections import Counter
    print(f"casos: {len(casos)} | fora_do_indice: {sum('fora_do_indice' in c['tags'] for c in casos)} | "
          f"no_indice: {sum('no_indice' in c['tags'] for c in casos)}")
    print("rótulos:", dict(Counter(c["rotulo"] for c in casos)))
    print("split:", dict(Counter(c["split"] for c in casos)))
    print("rótulo×tag:", dict(Counter((c["rotulo"], c["tags"][-1]) for c in casos)))
    print(f"excluídas: {len(excluir)} | checagens.jsonl: {len(restantes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
