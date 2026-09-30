import os, sys, json, pathlib, re, logging
S=os.environ["S"]
os.environ["IO_MODO"]="replay"; os.environ["TELEMETRIA_DIR"]=S+"/r3runs"; os.environ["SERPAPI_USO_ARQ"]=S+"/r3_serpapi_uso.json"
sys.path.insert(0, "/Users/aluno1/Documents/Default Project")
logging.disable(logging.CRITICAL)
from eval import run
from factcheck_mvp import decisao
casos = run.filtrar(run.carregar_casos(), "dev")
SOC = re.compile(r"(instagram\.com|facebook\.com|tiktok\.com|//(www\.)?x\.com|twitter\.com|youtube\.com|youtu\.be|kwai|threads\.net|reddit\.com)", re.I)
orig_contrib = decisao._contribuicoes
def sem_social(it, s, dec):
    if SOC.search(it.url): return []
    return orig_contrib(it, s, dec)
variantes = {
  "V1_relata_sem_selo": lambda: setattr(decisao, "CLASSES_TRATA", ("SUSTENTA", "REFUTA")),
  "V2_V1+social_nao_vota": lambda: setattr(decisao, "_contribuicoes", sem_social),
}
base = json.load(open(S+"/casos_replay.json")); b = {r["id"]: r for r in base}
for nome, aplicar in variantes.items():
    aplicar()
    out = run.rodar(casos, modo="replay", nome=nome, split="dev", dir_resultados=pathlib.Path(S+"/r3res"), progresso=lambda s: None)
    m = out["metricas"]
    print(f"{nome}: acerto={m['acerto']} grave={m['n_erro_grave']} indet={m['taxa_indeterminada']} niveis={m['niveis']} miss={m['http_miss_total']}")
    for r in out["casos"]:
        if r["nivel"] != b[r["id"]]["nivel"]:
            print(f"   {r['id']:<34} {r['rotulo']:<13} {b[r['id']]['nivel']} -> {r['nivel']}")
