import json,sys,math,re,collections
sys.path.insert(0,'.')
from eval.run import carregar_casos, filtrar, esperado_de, eh_grave
S=sys.argv[1]
def wilson(k,n,z=1.96):
    if n==0: return (float('nan'),)*2
    p=k/n; d=1+z*z/n; c=p+z*z/(2*n); h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return ((c-h)/d,(c+h)/d)
ROT=('falso','enganoso','verdadeiro','sem_evidencia')
def metr(casos,pred):
    n=len(casos); ok=par=gr=ind=0; gr_fn=gr_fp=0; por=collections.defaultdict(lambda:[0,0]); cob=0; ok_cob=0
    for c in casos:
        p=pred(c); esp,ac=esperado_de(c)
        o=p in esp; ok+=o; par+=(o or p in ac); g=eh_grave(c['rotulo'],p); gr+=g
        gr_fn+= g and c['rotulo'] in ('falso','enganoso'); gr_fp+= g and c['rotulo']=='verdadeiro'
        ind+= p=='indeterminada'; por[c['rotulo']][0]+=o; por[c['rotulo']][1]+=1
        if p in ('alta','baixa'):
            cob+=1; ok_cob+= o
    bal=sum(por[r][0]/por[r][1] for r in por)/len(por)
    nf=sum(1 for c in casos if c['rotulo'] in('falso','enganoso')); nv=sum(1 for c in casos if c['rotulo']=='verdadeiro')
    return dict(n=n,acerto=ok,acerto_parcial=par,grave=gr,grave_fn=f"{gr_fn}/{nf}",grave_fp=f"{gr_fp}/{nv}",indet=ind,bal=round(bal,3),
                por={r:f"{por[r][0]}/{por[r][1]}" for r in ROT if r in por},cob=cob,prec_decisiva=(f"{ok_cob}/{cob}" if cob else '-'))
NEG=re.compile(r'^(é falso que|não é verdade que)|\bnão\b',re.I)
preds={'sempre alta':lambda c:'alta','sempre media':lambda c:'media','sempre baixa':lambda c:'baixa','sempre indeterminada':lambda c:'indeterminada',
 'heur. negação (não→baixa, senão alta)':lambda c:'baixa' if NEG.search(c['entrada']['conteudo']) else 'alta'}
todos=carregar_casos()
out={}
for split in ('dev','holdout'):
    cs=filtrar(todos,split)
    print('==',split,len(cs),collections.Counter(c['rotulo'] for c in cs))
    for k,f in preds.items():
        m=metr(cs,f); out[(split,k)]=m; print(f"{k:40}",m)
# iter2 reconstruida e replay
rec={x['id']:x['niv'] for x in json.load(open(S+'/iter2_reconstr.json'))}
cs=filtrar(todos,'dev')
m=metr(cs,lambda c:rec[c['id']]); print(f"{'Iteração 2 (reconstruída)':40}",m)
import glob
d=sorted(glob.glob(S+'/resultados/*'))[0]
rp={json.loads(l)['id']:json.loads(l)['nivel'] for l in open(d+'/casos.jsonl')}
m2=metr(cs,lambda c:rp[c['id']]); print(f"{'Iteração 2 (replay hoje)':40}",m2)
# ICs
print('Wilson Iter2 acerto 20/70',[round(x,3) for x in wilson(20,70)])
print('Wilson Iter2 grave 3/70',[round(x,3) for x in wilson(3,70)])
print('Wilson Iter2 indet 27/70',[round(x,3) for x in wilson(27,70)])
print('Wilson Iter2 verdadeiro 4/8',[round(x,3) for x in wilson(4,8)])
print('Wilson Iter2 falso alta 10/49',[round(x,3) for x in wilson(10,49)])
print('Wilson sempre-alta acerto dev',out[('dev','sempre alta')]['acerto'],[round(x,3) for x in wilson(out[('dev','sempre alta')]['acerto'],70)])
for k,n in ((4,12),(0,12),(5,12),(3,12)):
    print(f'Wilson {k}/{n}',[round(x,3) for x in wilson(k,n)])
# acerto balanceado iter2 CI via bootstrap estratificado
import random
random.seed(0)
def bal(sample):
    por=collections.defaultdict(lambda:[0,0])
    for c in sample:
        esp,_=esperado_de(c); por[c['rotulo']][0]+=rec[c['id']] in esp; por[c['rotulo']][1]+=1
    return sum(a/b for a,b in por.values())/len(por)
by=collections.defaultdict(list)
for c in cs: by[c['rotulo']].append(c)
bs=[]
for _ in range(5000):
    s=[random.choice(v) for v in by.values() for _ in v]
    bs.append(bal(s))
bs.sort(); print('bal acc iter2',round(bal(cs),3),'boot95',round(bs[125],3),round(bs[4875],3))
