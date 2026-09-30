import json,re,sys,copy
sys.path.insert(0,"/Users/aluno1/Documents/Default Project")
from factcheck_mvp import decisao
D=json.load(open('docs/review/review2/sondas/casos_extraidos.json'))
def b(x): return x is True or x=='True'
def evid(o):
    det=next((e['detalhe'] for e in o['etapas'] if e['nome']=='afirmacoes'),'')
    afs=[]
    for m in re.finditer(r"\[(afirma|nega)\] (['\"])(.*?)\2 \(núcleo (['\"])(.*?)\4",det):
        afs.append(decisao.AfirmacaoDecisao(texto=m.group(3),nucleo=m.group(5),polaridade=m.group(1)))
    rel={f['url']:f for f in o['fontes'].get('relatorio',[])}
    itens=[]
    seen={}
    for f in o['fontes'].get('juiz',[]):
        cl=f.get('classe')
        motor='llm-juiz:openrouter' if cl else 'fallback-sem-juiz'
        clu=(rel.get(f['url']) or {}).get('cluster') or f.get('cluster') or f['url']
        it=decisao.ItemEvidencia(url=f['url'],afirmacao=int(f.get('afirmacao') or 0),cluster=clu,classe=cl,motor=motor,
            citacao_verificada=(False if f.get('decisao')=='rebaixada' else True),curada=b(f.get('curada')),corpo_lido=b(f.get('corpo_lido')),
            veredito=(f.get('veredito') if f.get('veredito') not in (None,'None') else None),origem_veredito='pagina',veiculo='')
        seen[(f['url'],it.afirmacao)]=it   # último julgamento vale (onda2 não re-julga pares)
    itens=list(seen.values())
    tr=(o['decisao'] or {}).get('travas') or {}
    ev=decisao.Evidencias(afirmacoes=afs,itens=itens,vago=tr.get('vago',False),opiniao=tr.get('opiniao',False),rumor=tr.get('rumor',False),
        juiz_disponivel=tr.get('juiz_disponivel',True),n_lidas=0,n_consultadas=0)
    return ev
if __name__=='__main__':
    ok=0;bad=[]
    for cid,o in D.items():
        if not o['decisao']: continue
        ev=evid(o); dec=decisao.decidir(ev)
        L0=o['decisao']['travas']['L']; n0=o['decisao']['nivel']
        if abs(dec.log_odds-L0)<1e-3 and dec.nivel==n0: ok+=1
        else: bad.append((cid,n0,L0,dec.nivel,dec.log_odds,len(ev.afirmacoes)))
    print('reproduz',ok,'de',sum(1 for o in D.values() if o['decisao'])); print(bad)
