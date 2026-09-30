import os, sys, json, pathlib, re, logging
S=os.environ["S"]
os.environ["IO_MODO"]="replay"; os.environ["TELEMETRIA_DIR"]=S+"/r3runs"; os.environ["SERPAPI_USO_ARQ"]=S+"/r3_serpapi_uso.json"
sys.path.insert(0, "/Users/aluno1/Documents/Default Project")
logging.disable(logging.CRITICAL)
from eval import run
from factcheck_mvp import decisao
casos = run.filtrar(run.carregar_casos(), "dev")
SOC = re.compile(r"(instagram\.com|facebook\.com|tiktok\.com|//(www\.)?x\.com|twitter\.com|youtube\.com|youtu\.be|kwai|threads\.net|reddit\.com)", re.I)
orig = decisao._contribuicoes
def v3(it, s, dec):
    if SOC.search(it.url) and it.classe == "SUSTENTA": return []
    return orig(it, s, dec)
decisao.CLASSES_TRATA = ("SUSTENTA", "REFUTA"); decisao._contribuicoes = v3
base = {r["id"]: r for r in json.load(open(S+"/casos_replay.json"))}
out = run.rodar(casos, modo="replay", nome="V3", split="dev", dir_resultados=pathlib.Path(S+"/r3res"), progresso=lambda s: None)
m = out["metricas"]
print(f"V3 (V1 + rede social nao SUSTENTA): acerto={m['acerto']} grave={m['n_erro_grave']} indet={m['taxa_indeterminada']} niveis={m['niveis']}")
print("  por rotulo:", {k: (v['acerto'], v['n_erro_grave'], v['niveis']) for k, v in m['por_rotulo'].items()})
for r in out["casos"]:
    if r["nivel"] != base[r["id"]]["nivel"]:
        print(f"   {r['id']:<34} {r['rotulo']:<13} {base[r['id']]['nivel']} -> {r['nivel']}")
