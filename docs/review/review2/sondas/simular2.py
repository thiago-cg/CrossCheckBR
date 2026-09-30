from simular import *
def agencia_trunfo(ev):
    # se há agência REFUTA na afirmação, SUSTENTA de não-agência não vota (checagem > repetição do boato)
    ref={it.afirmacao for it in ev.itens if AGRE.search(it.url) and it.classe=='REFUTA'}
    for it in ev.itens:
        if it.afirmacao in ref and it.classe=='SUSTENTA' and not AGRE.search(it.url): it.classe='RELATA_SEM_ENDOSSO'
    return ev
def social_sus_peso_baixo(ev):
    # social SUSTENTA vira cluster único '__social__' (no máx 1 voto de 0.42) — REFUTA mantém
    for it in ev.itens:
        if SOC.search(it.url) and it.classe=='SUSTENTA': it.cluster='__social_sus__'
    return ev
R={'R11 agência trunfo':agencia_trunfo,
   'R11+R8':comp(agencia_trunfo,selo_so_agencia),
   'R1+R11+R8':comp(agencia_refuta_selo,agencia_trunfo,selo_so_agencia),
   'R2b social SUS 1 cluster':social_sus_peso_baixo,
   'R2b+R8':comp(social_sus_peso_baixo,selo_so_agencia),
   'R1+R2b+R8':comp(agencia_refuta_selo,social_sus_peso_baixo,selo_so_agencia),
   'R1+R11+R2b+R8':comp(agencia_refuta_selo,agencia_trunfo,social_sus_peso_baixo,selo_so_agencia),
   'R1+R11+R2+R8':comp(agencia_refuta_selo,agencia_trunfo,social_sem_sustenta,selo_so_agencia),
}
b=rodar(base)
for nome,f in R.items():
    r=rodar(f); A=P=G=I=0; mud=[]
    for cid,n in r.items():
        a,p,g=aval(cid,n); A+=a;P+=p;G+=g; I+=(n=='indeterminada')
        if n!=b[cid]:
            a0,_,g0=aval(cid,b[cid]); mud.append(f"{cid}:{b[cid]}->{n}{'(+)' if a and not a0 else '(-)' if a0 and not a else ''}{'(GRAVE)' if g and not g0 else ''}")
    print(f"{nome:28} acerto={A:2}/70 +parcial={P:2} graves={G} indet={I} | "+'; '.join(mud))
