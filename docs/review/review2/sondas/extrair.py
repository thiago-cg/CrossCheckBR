import json, os, sys, glob, collections, re
from urllib.parse import urlsplit, parse_qsl
ROOT="/Users/aluno1/Documents/Default Project"
S="docs/review/review2/sondas"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from factcheck_mvp import replay
val=json.load(open(f"{S}/validos.json"))
casos={}
for f in ["eval/casos.jsonl","eval/casos_claimreview.jsonl"]:
    for l in open(f):
        l=l.strip()
        if not l or l.startswith("#"): continue
        c=json.loads(l); casos.setdefault(c["id"],c)
# index serpapi cassettes by canonical url
cas={}
for fn in glob.glob("eval/cassettes/*.json"):
    try: d=json.load(open(fn))
    except Exception: continue
    u=d["req"]["url"]
    cas[u]=fn
def serp_result(url):
    k=replay.url_canonica(url)
    fn=cas.get(k)
    if not fn: return None
    d=json.load(open(fn)); r=d["resp"]
    try: j=json.loads(r.get("texto") or "{}")
    except Exception: return {"status":r.get("status"),"erro":"nao-json"}
    org=[{"pos":i,"url":x.get("link"),"titulo":x.get("title"),"snippet":(x.get("snippet") or "")[:200]} for i,x in enumerate(j.get("organic_results") or [])]
    top=[{"url":x.get("link"),"titulo":x.get("title")} for x in (j.get("top_stories") or [])]
    return {"status":r.get("status"),"q":dict(parse_qsl(urlsplit(k).query)).get("q"),"organic":org,"top":top}
out={}
for cid,v in val.items():
    rid=v["run"]; c=casos.get(cid,{})
    ev=[json.loads(l) for l in open(f"runs/{rid}/trace.jsonl")]
    res=json.load(open(f"runs/{rid}/resultado.json"))
    o={"id":cid,"run":rid,"rot":v["rot"],"esp":v["esp"],"nivel":v["nivel"],"entrada":c.get("entrada",{}).get("conteudo"),
       "nota":c.get("nota"),"urls_checagem":c.get("urls_checagem") or re.findall(r"https?://\S+",c.get("nota") or "")[:1],
       "dur_ms":res.get("dur_ms"),"erro":res.get("erro"),"etapas":[],"queries":[],"serp":[],"fontes":collections.defaultdict(list),
       "llm":[],"fallbacks":[],"agente":[],"decisao":None,"http_pages":[]}
    for e in ev:
        t,dd=e["tipo"],e["dados"]
        if t=="etapa":
            o["etapas"].append({"t":e["t_rel_ms"],**{k:dd.get(k) for k in ("nome","status","detalhe","onda","afirmacao","query","motivo","n_resultados","n_novos","serpapi")}})
        elif t=="http":
            u=dd.get("url","")
            if "serpapi.com" in u:
                sr=serp_result(u); o["serp"].append({"t":e["t_rel_ms"],"status":dd.get("status"),"cache":dd.get("cache"),"lat":dd.get("latencia_ms"),"res":sr})
            else:
                o["http_pages"].append({"url":u,"status":dd.get("status"),"lat":dd.get("latencia_ms"),"bytes":dd.get("bytes"),"erro":dd.get("erro"),"cache":dd.get("cache")})
        elif t=="fonte":
            o["fontes"][dd.get("estagio")].append(dd)
        elif t=="llm":
            o["llm"].append({"t":e["t_rel_ms"],"fin":dd.get("finalidade"),"motor":dd.get("motor"),"modelo":dd.get("modelo"),"lat":dd.get("latencia_ms"),"cache":dd.get("cache"),"erro":dd.get("erro"),"saida":dd.get("saida"),"n_chars":dd.get("n_chars_prompt")})
        elif t=="fallback":
            o["fallbacks"].append(dd)
        elif t=="agente":
            o["agente"].append(dd)
        elif t=="decisao":
            o["decisao"]=dd
        elif t in("span_inicio","span_fim"):
            o.setdefault("spans",[]).append({"t":e["t_rel_ms"],"tipo":t,**{k:dd.get(k) for k in ("nome","dur_ms","ok")}})
    o["fontes"]=dict(o["fontes"])
    out[cid]=o
json.dump(out,open(f"{S}/casos_extraidos.json","w"),ensure_ascii=False,indent=1,default=str)
print(len(out), "cassettes idx", len(cas))
