import json,re,copy,math,collections,sys
sys.path.insert(0,"/Users/aluno1/Documents/Default Project")
from factcheck_mvp import decisao
from reconstruir import D, evid
AGRE=re.compile(r"boatos\.org|e-farsas\.com|aosfatos\.org|agencialupa\.org|lupa\.uol\.com\.br|piaui\.folha\.uol\.com\.br/lupa|g1\.globo\.com/fato-ou-fake|estadao\.com\.br/estadao-verifica|projetocomprova\.com\.br|noticias\.uol\.com\.br/(confere|comprova)|checamos\.afp\.com|agenciatatu|reuters\.com/fact-check|apublica\.org/checagem|fatooufake",re.I)
SOC=re.compile(r"//(www\.|m\.|web\.)?(instagram\.com|facebook\.com|fb\.watch|tiktok\.com|threads\.(com|net)|x\.com|twitter\.com|youtube\.com|youtu\.be|reddit\.com|kwai\.com|t\.co|linkedin\.com)/",re.I)
casos={}
for f in ["/Users/aluno1/Documents/Default Project/eval/casos.jsonl","/Users/aluno1/Documents/Default Project/eval/casos_claimreview.jsonl"]:
    for l in open(f):
        l=l.strip()
        if l and not l.startswith('#'):
            c=json.loads(l); casos.setdefault(c['id'],c)
ACEIT={'falso':['media'],'enganoso':[],'verdadeiro':[],'sem_evidencia':[]}
def aval(cid,niv):
    c=casos[cid]; rot=c['rotulo']
    esp=c.get('esperado') or {'falso':['alta'],'enganoso':['alta','media'],'verdadeiro':['baixa'],'sem_evidencia':['indeterminada']}[rot]
    ac=c.get('aceitavel') or ACEIT[rot]
    grave=(rot in('falso','enganoso') and niv=='baixa') or (rot=='verdadeiro' and niv=='alta')
    return niv in esp, (niv in esp or niv in ac), grave
def rodar(regra):
    res={}
    for cid,o in D.items():
        if not o['decisao']: res[cid]=None; continue
        ev=evid(o)
        tau_old=decisao.TAU
        try:
            ev=regra(ev) or ev
            dec=decisao.decidir(ev)
        finally:
            decisao.TAU=tau_old
        res[cid]=dec.nivel
    return res
# ---- regras: transformam itens; peso extra via truque: duplicar contribuição -> usar veredito sintético
def base(ev): return ev
def agencia_refuta_selo(ev):
    # agência curada/checadora com postura REFUTA -> veredito FALSO sintético (w=1.5) no mesmo item
    for it in ev.itens:
        if AGRE.search(it.url) and it.classe=='REFUTA' and not it.veredito:
            it.veredito='FALSO'; it.curada=True
    return ev
def agencia_curada(ev):
    for it in ev.itens:
        if AGRE.search(it.url): it.curada=True
    return ev
def social_sem_sustenta(ev):
    for it in ev.itens:
        if SOC.search(it.url) and it.classe=='SUSTENTA': it.classe='RELATA_SEM_ENDOSSO'
    return ev
def social_zero(ev):
    for it in ev.itens:
        if SOC.search(it.url) and it.classe in('SUSTENTA','REFUTA'): it.classe='RELATA_SEM_ENDOSSO'
    return ev
def social_um_cluster(ev):
    for it in ev.itens:
        if SOC.search(it.url): it.cluster='__social__'
    return ev
def selo_so_agencia(ev):
    for it in ev.itens:
        if it.veredito and not AGRE.search(it.url): it.veredito=None
    return ev
def tau_ln2(ev):
    decisao.TAU=math.log(2); return ev
def comp(*fs):
    def g(ev):
        for f in fs: ev=f(ev) or ev
        return ev
    return g
REGRAS={
 'R0 baseline':base,
 'R1 agência REFUTA = selo (w1.5)':agencia_refuta_selo,
 'R1b agência sempre curada (w1.0)':agencia_curada,
 'R2 social SUSTENTA não vota':social_sem_sustenta,
 'R3 social não vota':social_zero,
 'R3b social = 1 cluster só':social_um_cluster,
 'R8 selo de página só de agência':selo_so_agencia,
 'R6 tau=ln2':tau_ln2,
 'R1+R2':comp(agencia_refuta_selo,social_sem_sustenta),
 'R1+R8':comp(agencia_refuta_selo,selo_so_agencia),
 'R1+R2+R8':comp(agencia_refuta_selo,social_sem_sustenta,selo_so_agencia),
 'R1+R3+R8':comp(agencia_refuta_selo,social_zero,selo_so_agencia),
 'R1+R3b+R8':comp(agencia_refuta_selo,social_um_cluster,selo_so_agencia),
 'R2+R8':comp(social_sem_sustenta,selo_so_agencia),
 'R1b+R2+R8':comp(agencia_curada,social_sem_sustenta,selo_so_agencia),
 'R6+R2+R8':comp(tau_ln2,social_sem_sustenta,selo_so_agencia),
 'R1+R2+R8+R6':comp(agencia_refuta_selo,social_sem_sustenta,selo_so_agencia,tau_ln2),
}
if __name__=='__main__':
    b=rodar(base)
    for nome,f in REGRAS.items():
        r=rodar(f)
        A=P=G=I=0; mud=[]
        for cid,n in r.items():
            a,p,g=aval(cid,n); A+=a;P+=p;G+=g; I+=(n=='indeterminada')
            if n!=b[cid]:
                a0,_,g0=aval(cid,b[cid]); mud.append(f"{cid}:{b[cid]}->{n}{'(+)' if a and not a0 else '(-)' if a0 and not a else ''}{'(GRAVE)' if g and not g0 else ''}")
        print(f"{nome:34} acerto={A:2}/70 acerto+parcial={P:2} graves={G} indet={I} mudancas={len(mud)}")
        if nome!='R0 baseline': print("     ",'; '.join(mud))
