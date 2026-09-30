from simular import *
from simular2 import agencia_trunfo, social_sus_peso_baixo
import re
VAGO_NOVO=re.compile(r"^\s*(a|o)?\s*(economia|pa[ií]s|brasil|governo|vida|situa[cç][aã]o)\b.*\b(piorou|melhorou)\b",re.I)
def vago_fix(ev):
    # simula: VAGO só para comparativo sem objeto mensurável (sujeito genérico: economia/país/governo)
    return ev
def rodar_v(regra):
    res={}
    for cid,o in D.items():
        if not o['decisao']: res[cid]=None; continue
        ev=evid(o)
        if ev.vago and not VAGO_NOVO.search(o['entrada'] or ''): ev.vago=False
        ev=regra(ev) or ev
        res[cid]=decisao.decidir(ev).nivel
    return res
b=rodar(base)
for nome,f in {'vago_fix só':base,'vago_fix+R1+R11+R2b+R8':comp(agencia_refuta_selo,agencia_trunfo,social_sus_peso_baixo,selo_so_agencia),
               'vago_fix+R1+R11+R2+R8':comp(agencia_refuta_selo,agencia_trunfo,social_sem_sustenta,selo_so_agencia)}.items():
    r=rodar_v(f); A=P=G=I=0; mud=[]
    for cid,n in r.items():
        a,p,g=aval(cid,n); A+=a;P+=p;G+=g; I+=(n=='indeterminada')
        if n!=b[cid]:
            a0,_,g0=aval(cid,b[cid]); mud.append(f"{cid}:{b[cid]}->{n}{'(+)' if a and not a0 else '(-)' if a0 and not a else ''}")
    print(f"{nome:28} acerto={A}/70 +parcial={P} graves={G} indet={I} | "+'; '.join(mud))
    gr=[c for c,n in r.items() if aval(c,n)[2]]; print('   graves restantes:',gr)
