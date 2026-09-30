"""Prova de conceito do 'eval de decisão': reconstrói Evidencias do resultado.json gravado e reexecuta decidir()."""
import json,sys,collections
sys.path.insert(0,'.')
from factcheck_mvp.decisao import decidir, Evidencias, ItemEvidencia, AfirmacaoDecisao
from eval.run import carregar_casos, esperado_de, eh_grave
S=sys.argv[1]
casos={c['id']:c for c in carregar_casos()}
rec=json.load(open(S+'/iter2_reconstr.json'))
def B(x): return x in (True,'True','true')
def evid(R):
    rel=R['relatorio']; d=rel.get('decisao') or {}
    afs=[AfirmacaoDecisao(texto=a['afirmacao'],nucleo=a.get('nucleo',''),polaridade=a.get('polaridade','afirma')) for a in d.get('por_afirmacao',[])]
    idx={a.texto:i for i,a in enumerate(afs)}
    itens=[]
    for f in rel.get('fontes') or []:
        cl=f.get('postura')
        itens.append(ItemEvidencia(url=f['url'],afirmacao=idx.get(f.get('afirmacao'),0),cluster=f.get('cluster') or '',
            classe=cl if cl in ('SUSTENTA','REFUTA','RELATA_SEM_ENDOSSO','NAO_TRATA') else None, motor=f.get('motor_juiz') or '',
            citacao_verificada=(None if f.get('citacao_verificada') is None else B(f.get('citacao_verificada'))),
            curada=B(f.get('curada')), corpo_lido=B(f.get('corpo_lido')), veredito=f.get('veredito_normalizado'),
            origem_veredito='pagina' if f.get('veredito_normalizado') else None, veiculo=f.get('portal_id') or ''))
    t=d.get('travas',{}); c=d.get('contagem',{})
    return Evidencias(afirmacoes=afs,itens=itens,vago=t.get('vago',False),opiniao=t.get('opiniao',False),rumor=t.get('rumor',False),
                      juiz_disponivel=t.get('juiz_disponivel',True),n_lidas=c.get('lidas',0),n_consultadas=c.get('consultadas',0))
iguais=0; n=0; dif=[]
for x in rec:
    R=json.load(open(f"runs/{x['run']}/resultado.json"))
    if not R.get('relatorio') or not (R['relatorio'].get('decisao')): continue
    n+=1
    d=decidir(evid(R))
    if d.nivel==R['relatorio']['propensao']: iguais+=1
    else: dif.append((x['id'],R['relatorio']['propensao'],d.nivel,round(d.log_odds,2),R['relatorio']['decisao'].get('log_odds')))
print(f'decidir() offline reproduz {iguais}/{n} níveis gravados'); print(dif[:10])
