# Revisão do eval do CrossCheckBR: rótulos, métricas, validade e custo

Escopo: `eval/` (README, run.py, casos.jsonl, casos_claimreview.jsonl, ITERACOES.md, baseline.json),
`scripts/gerar_casos_claimreview.py`, `factcheck_mvp/data/selos.json`, RELATORIO §5, traces em `runs/`
e cassetes em `eval/cassettes/`. Não houve nenhuma chamada paga: dois evals do dev em `--modo replay`,
com saída redirecionada para o scratchpad (`TELEMETRIA_DIR` e `dir_resultados`), mais scripts offline.
Houve uma única leitura web gratuita, da checagem do E-farsas sobre o IBGE. O repositório não foi alterado
(`git status` limpo).

Scripts e dados que sustentam os números, todos em `scratchpad/review2/`: `runs_tab.py/json` (297 runs de
eval tabelados), `iter2_reconstr.json`, `r_eval_baselines.py`, `r_eval_serp.py`, `r_eval_vazamento.py`,
`r_eval_posturas.py`, `r_eval_decisao_offline.py`, `replay_dev.py` e os dois resultados de replay em
`resultados/`.

---

## Resumo

1. **As métricas atuais não separam o sistema de um classificador trivial.** No dev, "sempre alta" acerta
   **58/70 (83%)**; a Iteração 2 acerta 20/70 (29%). "Sempre média" tem **0 erro grave**, e o gate só olha
   `n_erro_grave` total e `acerto`, então um sistema que colapsa em média/indeterminada passa no gate.
   A acurácia balanceada da Iteração 2 é **0,39 (IC95 bootstrap 0,22–0,56)**, abaixo de "sempre alta" (0,50).
   Quando o sistema diz `alta`, a chance de o caso ser falso/enganoso é **12/14 = 86%**, contra uma
   prevalência de 83%: esse nível praticamente não informa nada neste dataset. O sinal útil está em `baixa`
   (4/7 verdadeiros, contra prevalência de 11%) e na AUC ordinal de **0,78 (IC 0,63–0,93)**.
2. **2 dos 3 erros graves da Iteração 2 são artefatos de rótulo ou reescrita, não do sistema.**
   - `cr_jn_soltura_vorcaro`: o texto "o JN anunciou que o Vorcaro foi solto" é literalmente verdadeiro
     (o JN noticiou a soltura em 29/11/2025, e o sistema achou essa matéria). O enganoso da checagem era
     "é antigo e está circulando agora", e a reescrita apagou esse elemento.
   - `cr_ibge_trafico_no_pib`: a reescrita tirou o "governo ordenou". O próprio E-farsas diz que há estudos
     do IBGE previstos para 2028 seguindo diretriz da ONU.
   - O único erro grave real é `cr_chico_cesar_proibiu_direita`: posts que espalham o boato foram julgados
     SUSTENTA. O erro grave real fica em **1/70 (IC95 0,3%–7,7%)**.
3. **"Busca aberta" mede sobretudo se o sistema achou uma checagem, não checagem cruzada.**
   - A URL-gabarito aparece nos resultados da SerpAPI em só **4/58 casos (7%)**. As checagens tinham 0 a 7
     dias no momento da gravação, provavelmente ainda não indexadas.
   - Mesmo assim, **9 dos 12 acertos `alta`** (falso/enganoso) vêm de conteúdo de checagem: outra agência,
     página de listagem da agência, post de agência, blog repetindo a checagem, ou checagem antiga do
     mesmo boato.
   - Pelo menos 2 dos 12 são acertos por razão errada.
   - Consequência: se os cassetes forem regravados daqui a semanas, o recall sobe por indexação e não por
     mérito do sistema.
4. **Tamanho amostral: quase nada é estatisticamente distinguível.**
   - Iteração 0 → 1b (12 casos): erro grave 4→0 tem McNemar exato **p = 0,125**; acerto 4→5 tem 3 contra 4
     discordantes, **p = 1,0**.
   - Com n=70, a diferença mínima detectável em acerto fica em torno de 20 pp. Em pares, é preciso algo como
     12 a 2 casos discordantes a favor para ter p<0,05.
   - Com 8 verdadeiros, cada caso vale 12,5 pp.
5. **A reconstrução da Iteração 2 é válida e reprodutível na prática.**
   - Refiz a reconstrução pelos traces de forma independente e ela bate exatamente com o ITERACOES
     (20 / 3 / 27 e a tabela por rótulo).
   - O replay de hoje reproduz **66/70 níveis** (acerto 19/70, grave 3, indeterminada 28). As 4 diferenças
     estão todas em casos com cache miss de LLM.
   - O próprio replay não é determinístico: duas execuções divergem em 1/70 e o `http_miss` vai de 87 para 94.
     A causa é a composição dos lotes do juiz depender da ordem assíncrona das páginas.
   - `decidir()` reexecutado offline sobre os julgamentos gravados reproduz **69/69 níveis**. O "eval de
     decisão" de custo zero já é viável hoje.

---

## Achados por severidade

### CRÍTICO

**C1. Sem baseline trivial nem métrica robusta ao desbalanço, o acerto e o erro grave enganam.**
- **Evidência:**
  - Tabela da seção "Baselines triviais".
  - O dev tem 49 falso, 9 enganoso, 8 verdadeiro e 4 sem_evidencia, ou seja, 83% são F/E.
  - O gate (`avaliar_gate`) reprova só se `n_erro_grave` sobe ou se `acerto` cai mais de 5 pp.
- **Impacto:**
  - Quase toda mudança que empurre para `alta` sobe o acerto: foi o que aconteceu da Iteração 0 para a 1b,
    que trocou "baixa em tudo" por "média/alta em tudo". O acerto subiu de 4 para 5, mas verdadeiro foi de
    3/4 para 0/4.
  - Quase toda mudança que empurre para `media`/`indeterminada` zera o erro grave.
  - As duas métricas principais premiam atalhos opostos. Nenhuma das duas mede discriminação.
- **Correção:**
  - Relatar sempre, lado a lado com o sistema, as linhas "sempre alta" e "sempre média".
  - Adicionar acurácia balanceada, erro grave por direção, cobertura com precisão por classe e AUC ordinal
    (lista na seção "Métricas propostas").
  - Gate novo, sobre o mesmo conjunto de ids:
    - (i) nenhum erro grave novo em nenhuma direção;
    - (ii) acurácia balanceada sem queda;
    - (iii) cobertura sem queda maior que X;
    - (iv) nenhum ganho aceito sem um mecanismo confirmado em trace.

**C2. Os erros graves de referência estão contaminados por erro de rótulo ou reescrita.**
- **Evidência:**
  - `cr_jn_soltura_vorcaro`: a nota é "boatos selo 'antigo'". O trecho diz que o boato era "Vorcaro foi
    solto em setembro de 2026". A entrada não tem data. O trace (`runs/20260930-09…`) traz SUSTENTA de
    `g1.globo.com/jornal-nacional/noticia/2025/11/29/justica-solta-dono-do-banco-master…`.
  - `cr_ibge_trafico_no_pib`: SUSTENTA de "IBGE prepara inclusão de estimativa do tráfico…"; o E-farsas
    confirma estudos para 2028.
- **Impacto:** a métrica mais importante, com peso maior pela regra, conta 3 quando o real é 1. O ITERACOES
  levanta a hipótese H5 ("possível inversão de polaridade") sobre dois casos que não são erro de polaridade.
- **Correção:**
  - Reescrever as entradas preservando o elemento enganoso ("…anunciou **agora** que o Vorcaro foi solto";
    "o governo mandou o IBGE colocar o tráfico e as bets no PIB"). A outra opção é reetiquetar com
    justificativa na `nota` e registrar em ITERACOES.
  - Fazer uma checagem de fidelidade da reescrita para todo caso (ver A4).

**C3. Validade de constructo: o eval mede "achar a checagem", e esse efeito deriva com o tempo.**
- **Evidência de que a URL-gabarito quase nunca é recuperada:**
  - Olhando as queries dos traces da Iteração 2 contra os cassetes SerpAPI (`r_eval_serp.py`), a
    URL-gabarito (qualquer URL de `urls_checagem`) está nos resultados orgânicos em 4/58 casos:
    `hytalo` (posição 4), `lula_garis` (1), `xi_jinping` (1) e `cucurella` (1).
  - Em todos os 256 cassetes SerpAPI, só 5/77 gabaritos aparecem.
  - As checagens eram de 2026-09-17 a 2026-09-28; as buscas foram gravadas em 2026-09-30.
- **Evidência de que, mesmo assim, a decisão depende de checagens** (`r_eval_posturas`):
  - Nos 12 falso/enganoso que deram `alta`, 9 têm REFUTA vindo de conteúdo de checagem. Exemplos:
    `agencialupa.org/tag/falso/page/109` (listagem com o título da checagem, em `lula_nordestino`),
    `boatos.org/entretenimento/page/2` (em `zeca_pagodinho`), AFP Checamos e uma página de autor do Comprova
    na NSC (em `audio_renan`), blogs e Instagram repetindo "checagem concluiu…" (em `flavio_salario`),
    Lupa 2022 (em `pf_compra_votos`).
  - Nas 18 `media` de falso/enganoso, 10 têm REFUTA de conteúdo de checagem e 4 não têm REFUTA nenhum.
  - As buscas incluem `site:` das agências por projeto (RELATORIO §5).
- **Impacto:**
  - A métrica principal ("busca aberta") é, na prática, um teste de *claim matching* contra a web indexada
    na data da gravação.
  - Não mede o que o produto promete quando não há checagem (boato novo, local, sem agência).
  - Não é estacionária: regravar mais tarde muda o que é achável.
  - `fora_do_indice` só tira a checagem do índice local; o Google continua com ela.
- **Correção:** ver a seção "Proposta de splits". Em resumo:
  - (a) registrar a data do snapshot da SERP na `meta` e nunca comparar rodadas com snapshots diferentes;
  - (b) criar um perfil `sem_checagem` que filtra, em tempo de replay, domínios de agências e republicações;
  - (c) criar casos sem checagem publicada;
  - (d) medir e relatar `gabarito_na_serp` e "evidência decisiva é checagem" por caso.

### ALTO

**A1. O n é pequeno demais para as conclusões que o ITERACOES tira.**
- Ver a seção "Intervalos de confiança". A frase "Erro grave 4/12 → 0/12" é compatível com acaso
  (p = 0,125). O que sustenta essa melhora é o mecanismo visto nos traces ("cobertura ampla" removida), não
  o número.
- **Correção:**
  - Escrever as conclusões como "o mecanismo X foi eliminado (trace Y)", não como "erro grave caiu 33 pp".
  - Relatar pares discordantes e o p de McNemar em toda comparação.
  - Crescer os verdadeiros para ao menos 30 no dev (seção "Casos que faltam").

**A2. O harness calcula "parcial" de forma inconsistente com o README.**
- **Evidência:**
  - `esperado_de()` retorna `aceitavel=[]` quando o caso define `esperado`. Os casos claimreview definem
    `esperado` explicitamente, então **falso→média não conta como parcial nos 58 casos claimreview**, mas
    conta nos manuais.
  - Enganoso→média conta como **acerto pleno** no claimreview (`["alta","media"]`), mas como parcial no
    README e em `MAPA_ESPERADO`.
  - Resultado: o harness dá `acerto_com_parcial` = **21/70**, enquanto o ITERACOES cita "≈39/70 (56%)
    contando média", número calculado à mão e que não sai do `eval.run`.
- **Correção:** uma única tabela de mapeamento (em `run.py`), sem `esperado` explícito nos casos claimreview
  a menos que haja razão escrita. Relatar `media` como categoria própria, sem misturar com acerto.

**A3. `sem_evidencia` mistura dois conceitos.**
- **Evidência:**
  - `economia_piorou` é vaga e não falsificável.
  - `cr_vorcaro_pagou_dino` ("Não há registros"), `cr_xi_jinping_avc` ("Não há qualquer confirmação") e
    `cr_eua_prender_lula_iminente` ("Não há indícios") são boatos ou acusações sem prova que circulam como
    fato.
  - Na saída "propensão a desinformação", `alta` é defensável para esse segundo tipo. Mesmo assim, o esperado
    `[indeterminada, media]` conta como erro a Iteração 2 dar `alta` a xi (achando a própria checagem) e a
    eua (achando o desmentido do FBI de 2020).
- **Correção:** separar `nao_verificavel` (esperado indeterminada) de `sem_evidencia_boato` (esperado
  `[media, alta]`, aceitável indeterminada, sem erro grave).

**A4. A procedência dos rótulos não foi revisada.**
- **Evidência:**
  - 63/77 rótulos vêm de `rss-titulo`, isto é, do veredito tirado da manchete pela tabela `selos.json`,
    que está com `"revisao_humana": "pendente"`. Só 14 vêm de ClaimReview estruturado.
  - `cr_moraes_aviao_vorcaro_verdadeira` tem metadado contraditório: `claimReviewed` "Não é Moraes no avião
    de Vorcaro" com selo "Comprovado". O rótulo final está certo por coincidência, porque a entrada seguiu a
    manchete.
  - As entradas foram escritas por quem sabia o rótulo; não há segundo anotador.
  - 35% dos casos são do boatos.org.
- **Correção:**
  - Revisão humana de `selos.json`.
  - Campo `reescrita_revisada_por` e verificação de fidelidade: a entrada precisa conter o elemento que a
    checagem julgou.
  - Segundo anotador em 20 casos, com kappa relatado.
  - Cota máxima por agência.

**A5. O replay não é determinístico e os cassetes são frágeis.**
- **Evidência:**
  - Duas execuções idênticas de replay divergem em `cr_lula_pobre_morrer_cortando_cana` (média numa,
    indeterminada na outra). O 2º lote do juiz teve `prompt_sha` diferente (`3d4bd3f5e766` contra
    `66a184cab9eb`), o que deu `ReplayMiss`, depois fallback para o LLM local e outro miss.
  - 6/70 casos têm miss de LLM. Níveis que batem com a reconstrução:

    | situação no replay | casos | mesmo nível da reconstrução |
    |---|---|---|
    | sem nenhum miss | 36/70 | 36/36 |
    | só miss de página ou de SerpAPI | 28/70 | 28/28 |
    | com miss de LLM | 6/70 | 2/6 |

  - Os misses de SerpAPI e de página são requisições que também falharam no record; o caminho é o mesmo.
  - `eval/cassettes/` tem 694 MB e está no `.gitignore`: o eval existe em uma única máquina.
- **Correção:**
  - Ordenar canonicamente as fontes (por URL) antes de montar os lotes do juiz.
  - Relatar `llm_miss` separado de `http_miss`; caso com `llm_miss > 0` vira "não reproduzido" e sai do delta.
  - Fazer backup versionado de um snapshot mínimo: só SerpAPI e LLM, sem o HTML das páginas, se o tamanho
    for problema.

### MÉDIO

**M1. A reconstrução da Iteração 2 é válida, com ressalvas.**
- Todos os 70 runs escolhidos são da rodada `iter2b` (09:50–10:07).
- 11/70 tiveram buscas recusadas: `serp_fail > 0`, ou seja, "parcial" e não "busca ok".
- `cr_oreo_petroleo` estourou o timeout de 600 s (sem nível).
- 2 casos misturaram juiz luna e deepseek (`toyota`, `stf_idade`).
- Correção: rotular como "Iteração 2 (reconstruída; 11 parciais; 1 timeout)".

**M2. O gate só vê 12 dos 70 casos.**
- `eval/baseline.json` é a Iteração 0 (12 casos); os 58 claimreview nunca entram no delta.
- `--gate-pp 5` com n=12 é menor que um caso (8,3 pp).

**M3. Acertos pela razão errada contam como acerto.**
- `cr_lula_nao_acredita_em_deus` → alta com o juiz invertido: os posts do Facebook que *afirmam* "Lula
  confessa que não acredita em Deus" foram julgados REFUTA; a matéria do Valor sobre a fé de Lula foi
  julgada SUSTENTA.
- `cr_lula_garis_nao_da_orgulho` → alta com 5 SUSTENTA sobre "ser gari dá orgulho", que não têm relação
  com o que Lula disse.
- Correção: auditoria de evidência em cada acerto decisivo (a evidência decisiva trata do tema e tem a
  postura certa?), relatada como `acerto_com_evidencia_valida`.

**M4. Composição do dataset.**
- 14/77 casos (12 no dev) dependem de mídia ("foto/vídeo/áudio é real"), inclusive 3 dos 4 verdadeiros
  claimreview do dev.
- 9 são perecíveis ("vai…", "a partir de 1º de outubro", "acabou de sair").
- 2 são perguntas.
- Nenhuma entrada é do tipo `link`.
- Temas repetidos entre dev e holdout (urnas, salário mínimo de 3 mil).
- 25/77 (32%, IC 23–44%) têm ao menos uma ressalva de constructo.

### BAIXO

- O ITERACOES repete as entradas 1a e 1b.
- `iter1-local` deu 7/12 e `iter1-local-12` deu 5/12 nos mesmos casos, com configurações diferentes
  (`LLM_LOCAL_RACIOCINIO`, sha). Só a segunda foi registrada; a diferença mostra a sensibilidade e deveria
  estar anotada.
- `satira_feriado` mudou de indeterminada para média entre essas duas rodadas.

---

## Pergunta 1: validade dos rótulos e do mapeamento

**Sobre o mapeamento:**
- **falso→[alta]:** correto.
- **enganoso→[alta, media]:** aceitável, mas precisa ser igual em README, harness e casos (A2).
- **verdadeiro→[baixa]:** correto.
- **sem_evidencia→[indeterminada, media]:** errado para boatos sem prova (A3).
- **"media"** funciona como um "não sei" morno: é acerto em enganoso e sem_evidencia, parcial (às vezes) em
  falso e nunca é grave. Deveria ser tratada como abstenção nas métricas de cobertura.

**Revisão dos casos.** Li os 77 casos claimreview, comparando a entrada com a `afirmacao_checada`, o título
e a nota. Em 13 li também o `trecho` da checagem (em `checagens_holdout_excluidas.json`), e no caso do
IBGE a página original. Não abri as outras 76 URLs; por isso esta é uma estimativa de limite inferior.

| categoria | casos | efeito |
|---|---|---|
| Reescrita muda o valor de verdade ou tira o elemento enganoso | `jn_soltura_vorcaro` (dev), `ibge_trafico_no_pib` (dev), `ives_gandra_voto_lula` (holdout: citação truncada, sobra só a metade inócua) | rótulo errado ou contestável para o texto como escrito; 2 deles são erros graves da Iteração 2 |
| Política de esperado discutível (sem_evidencia de boato) | `vorcaro_pagou_dino`, `xi_jinping_avc`, `eua_prender_lula_iminente` | `alta` conta como erro |
| Metadado contraditório (rótulo certo por sorte) | `moraes_aviao_vorcaro_verdadeira` | risco de processo |
| Dependem de mídia | miojo, camiseta, fachin, moraes_aviao, médico-robô, vídeo_lula_stf, áudio_renan, flávio_salário, jn_soltura, vaca, vulcão, banho_de_fogo, cucurella, semáforos (14) | só dá para acertar achando a checagem |
| Perecíveis ou previsão | hytalo, moto_placa, el_niño, eua_prender, lula_salário, flávio_padroeira, whatsapp_cobrar, ibge, fauci (9) | o rótulo vale só na `data_checagem` |
| Pergunta, não afirmação | rick_caixão ("?"), lula_janja ("é verdade?") | baixo |

**Taxas:**
- Rótulo ou esperado errado ou contestável: **6/77 = 7,8% (IC95 Wilson 3,6%–16%)**. No dev são 5/58.
- Ao menos uma ressalva de validade de constructo: **25/77 = 32% (IC 23%–44%)**.
- O impacto se concentra onde mais importa: 2/3 dos erros graves e 2/4 dos erros em sem_evidencia.

## Pergunta 2: baselines triviais (dev, n=70, harness real: `esperado_de` e `eh_grave`)

| preditor | acerto | acerto+parcial (harness) | erro grave (F/E→baixa \| V→alta) | indeterminada | acurácia balanceada | por rótulo F / E / V / SE |
|---|---|---|---|---|---|---|
| **sempre alta** | **58/70 (83%)** [72–90%] | 58 | 8 (0/58 \| **8/8**) | 0 | 0,50 | 49/49, 9/9, 0/8, 0/4 |
| sempre média | 12/70 (17%) | 19 | **0** | 0 | 0,44 | 0/49, 9/9, 0/8, 3/4 |
| sempre baixa | 8/70 (11%) | 8 | 58 (58/58 \| 0/8) | 0 | 0,25 | 0, 0, 8/8, 0 |
| sempre indeterminada | 5/70 (7%) | 5 | **0** | 70 | 0,26 | 1/49 (sátira), 0, 0, 4/4 |
| heurística "não"→baixa, senão alta | 57/70 (81%) | 57 | 9 (3/58 \| 6/8) | 0 | 0,53 | 47/49, 8/9, 2/8, 0/4 |
| **Iteração 2 (reconstruída)** | 20/70 (29%) [19–40%] | 21 | 3 (3/58 \| 0/8) | 27 | **0,39** [0,22–0,56] | 11/49, 3/9, 4/8, 2/4 |
| Iteração 2 (replay de hoje) | 19/70 (27%) | 20 | 3 (3/58 \| 0/8) | 28 | 0,38 | 10/49, 3/9, 4/8, 2/4 |

No holdout (n=25), "sempre alta" acerta 21/25 (84%) com 3/3 graves em verdadeiro.

**Leitura:**
- **Acerto:** perde para "sempre alta" por 38 pp.
- **Erro grave:** perde para "sempre média" (3 contra 0).
- **Acurácia balanceada:** perde para "sempre alta" (0,39 contra 0,50) e para a heurística de negação (0,53).
- **Onde o sistema tem valor mensurável:**
  - não comete FP grave em verdadeiro (0/8, IC 0–32%);
  - `baixa` tem precisão 4/7 contra prevalência de 11% (LR+ ≈ 10);
  - AUC ordinal (baixa < média/indet < alta, F/E contra V) = **0,78 [0,63–0,93]**.
- **`alta` não informa:** P(F/E | alta) = 12/14 = 86% [60–96%] contra prevalência de 83%.
- **Conclusão:** com as métricas atuais o sistema não se distingue de um trivial. A AUC e a precisão de
  `baixa` são as únicas evidências de discriminação, e ambas se apoiam em 8 verdadeiros.

### Métricas propostas (todas calculáveis a partir de `casos.jsonl` sem rodar nada)

1. **Acurácia balanceada** (média do recall por rótulo), com IC por bootstrap estratificado.
2. **Erro grave por direção**, com contagem e denominador:
   `FNR_grave = F/E→baixa / n(F/E)` e `FPR_grave = V→alta / n(V)`. Ao menos um dos dois deve ser gate
   separado.
3. **Cobertura** = fração de casos em `alta` ou `baixa` (média e indeterminada = abstenção), com
   **precisão seletiva por classe** P(F/E | alta) e P(V | baixa), **LR+** contra a prevalência e a curva
   cobertura × precisão variando o limiar τ de `decidir()` (sai grátis do eval de decisão).
4. **AUC ordinal** F/E contra V, que não depende da prevalência.
5. **Linhas de trivial** ("sempre alta", "sempre média") impressas em todo relatório, com o "lift" do
   sistema.
6. **Validade da evidência:**
   - `gabarito_na_serp` (a URL de `urls_checagem` apareceu na busca);
   - `evidencia_decisiva_e_checagem` (o voto de maior peso veio de domínio de agência ou de texto de
     checagem);
   - `acerto_com_evidencia_valida` (auditoria de M3). Métricas estratificadas por essas marcas.
7. **Custo por caso:** buscas SerpAPI live, chamadas LLM (hoje ≈ 6,6/caso), latência p50/p95 e custo por
   acerto balanceado.
8. **Reprodutibilidade:** fração de casos com `llm_miss` e com `serp_miss` em replay; em record, taxa de
   concordância teste-reteste em 10 casos.

## Pergunta 3: tamanho amostral e intervalos de confiança (Wilson 95%)

| quantidade | valor | IC95 |
|---|---|---|
| Iteração 2: acerto | 20/70 = 28,6% | 19,3%–40,1% |
| Iteração 2: erro grave (como registrado) | 3/70 = 4,3% | 1,5%–11,9% |
| Iteração 2: erro grave real (sem os 2 artefatos) | 1/70 = 1,4% | 0,3%–7,7% |
| Iteração 2: indeterminada | 27/70 = 38,6% | 28,0%–50,3% |
| Iteração 2: verdadeiro→baixa | 4/8 = 50% | 21,5%–78,5% |
| Iteração 2: falso→alta | 10/49 = 20% | 11,5%–33,6% |
| Iteração 2: acurácia balanceada | 0,39 | 0,22–0,56 (bootstrap) |
| Iteração 0: erro grave / acerto (12) | 4/12 = 33% / 4/12 | 13,8%–60,9% / idem |
| 1b: erro grave / acerto (12) | 0/12 / 5/12 = 42% | 0–24,3% / 19,3%–68,0% |

**Iteração 0 contra 1b (os mesmos 12 casos, teste pareado):**
- Erro grave: 4 casos saíram de grave e nenhum entrou. McNemar exato **p = 0,125**.
- Acerto: 3 casos só a Iteração 0 acerta (os três verdadeiros com "cobertura ampla") e 4 só a 1b acerta.
  **p = 1,0**.
- Acerto+parcial: 3 contra 7 discordantes, p = 0,34.
- Os intervalos de confiança se sobrepõem em todas as métricas.

**O que é distinguível com n=70:**
- Proporções independentes em torno de 0,3 precisam de uma diferença de ~22 pp (α = 0,05, poder 0,8).
- Pareado (o caso do eval), depende dos discordantes: com 10 discordantes é preciso 9 a 1; com 14, 12 a 2;
  com 20, 15 a 5.
- Erro grave de 3 para 0 dá p = 0,25.
- No subgrupo verdadeiro (8 casos), só 6 a 0 ou 8 a 0 atingem p < 0,05.

**O que se pode afirmar honestamente:**
- "A Iteração 1 removeu o mecanismo 'cobertura ampla → baixa'; nos 4 traces afetados o nível deixou de ser
  baixa." Essa é uma afirmação mecanística e verificável.
- "Na Iteração 2, o erro grave observado ficou entre 0 e 3 em 70; o acerto está entre 19% e 40%; em
  verdadeiros, entre 22% e 78%."
- **Não** se pode afirmar que o sistema melhorou o acerto, nem que é melhor que um trivial em acurácia
  balanceada.

## Pergunta 4: vazamento e contaminação

**Medido:**

| medida | casos | detalhe |
|---|---|---|
| URL-gabarito nos resultados orgânicos da SerpAPI (Iteração 2) | 4/58 (7%, IC 2,7–16%) | `fora_do_indice` 3/46, `no_indice` 1/12 |
| Ao menos um link de domínio de agência nos resultados | 43/58 | a maioria fora do tema (ex.: Lupa 2018 sobre debates, no caso Oreo) |
| Gabarito achado e resultado | 4 casos | 3 deram `alta`, 1 deu `media` |
| Gabarito não achado e nível `alta` | 9 casos | — |

**Mecanismo real:** o vazamento vem por vias indiretas:
- listagens (`agencialupa.org/tag/falso/page/109`, `boatos.org/entretenimento/page/2`);
- checagens de outras agências fora do cluster do caso (AFP Checamos, Comprova via NSC, UOL Confere);
- republicação em blogs e Instagram ("a checagem concluiu…");
- checagem antiga de boato parecido (Lupa 2022 "pf-para-votos"; boatos.org 2020 "FBI prisão de Lula").

Nos 12 casos `alta` corretos (F/E), **9 têm voto REFUTA vindo de conteúdo de checagem**. Os outros 3 são:
`bets`, com fontes jornalísticas primárias (é o único exemplo limpo de checagem cruzada); `lula_nao_acredita`,
com o juiz invertido; e `garis`, com evidência irrelevante.

**O que isso significa para a métrica "busca aberta":**
- Ela mede *claim matching* na web indexada na data da gravação, mais a agregação.
- O baixo recall do gabarito se deve em boa parte ao frescor: checagens de 0 a 7 dias não indexadas. Um
  re-record futuro inflaria o acerto sem mudança no sistema.
- É um constructo legítimo para o produto, porque achar uma checagem existente é útil. Mas precisa ter esse
  nome e não pode ser vendido como "checagem cruzada".

### Proposta de splits

1. **`matching` (o atual):**
   - Manter, com `meta.snapshot_serp = data` e a regra de só comparar rodadas do mesmo snapshot.
   - Relatar `gabarito_na_serp` e `evidencia_decisiva_e_checagem`.
2. **`sem_checagem` (perfil de replay, custo SerpAPI zero):**
   - `--env EXCLUIR_CHECAGEM_DA_SERP=1` remove dos resultados SerpAPI cassetados os domínios do catálogo de
     agências, `checamos.afp.com`, `uol.com.br/confere`, páginas `/tag/` e `/page/N`, posts de redes com
     handle de agência, e texto do trecho com "checagem"/"#FAKE"/"é falso que".
   - Pula as queries `site:`.
   - As páginas que sobrarem e não foram baixadas geram miss; um record só de HTTP de páginas (sem SerpAPI,
     sem LLM se o juiz ficar em replay) completa o conjunto.
   - Mede se o sistema chega ao nível certo por fontes primárias ou jornalísticas.
3. **`sem_checagem_publicada` (casos novos):**
   - Verdadeiros de fonte oficial ou jornalística primária (Agência Brasil, IBGE, BCB, Planalto) e falsos
     obtidos por perturbação controlada de fatos verdadeiros (troca de número, de atribuição, de data).
   - Por construção, nenhuma agência checou esses casos (seção "Casos que faltam").
4. **`pre_indexacao` (opcional):** gravar a SERP no dia em que o boato aparece no RSS de uma agência, antes
   da checagem estar indexada. É o cenário realista de boato novo; é basicamente o que o `fora_do_indice`
   fez sem querer, e vale formalizar com a data.

## Pergunta 5: reprodutibilidade e custo

**A reconstrução é válida?** Sim.
- Minha reconstrução independente (último run com SerpAPI 200 e juiz deepseek; todos da `iter2b`) bate
  exatamente com o ITERACOES.
- Ressalvas de M1: 11 parciais, 1 timeout, 2 com juiz misto.

**O replay de hoje** (`python3 -m eval.run --modo replay --split dev`, equivalente, com saída em
scratchpad; ~3,5 min; 0 buscas live):

| execução | acerto | grave | indeterminada | http_miss | mesmo nível da reconstrução |
|---|---|---|---|---|---|
| replay 1 | 19/70 | 3 | 28 | 87 (em 34 casos) | 66/70 |
| replay 2 | 18/70 | 3 | 29 | 94 | 65/70 |

- As 4 diferenças do replay 1 (`lula_nao_acredita` alta→média, `bets` alta→média, `cr7` média→indet,
  `oreo` timeout→média) têm todas miss de LLM do juiz.
- **Os números batem com a reconstrução dentro de ±1 caso.** O `http_miss` alto é quase todo de requisições
  que também falharam no record (páginas 403/SSL/PDF e buscas 429); não altera o caminho.

**`decidir()` offline** (`r_eval_decisao_offline.py`):
- Reconstrói `Evidencias` a partir de `resultado.json` (`fontes[].postura/cluster/curada/
  citacao_verificada/motor_juiz/veredito_normalizado`, mais `decisao.por_afirmacao/travas/contagem`) e
  reexecuta a função pura.
- Resultado: **69/69 níveis reproduzidos, em menos de 1 s.**

### Protocolo de avaliação barato recomendado

**Nível 0: eval de decisão (custo zero, segundos).** Para mudanças em `decisao.py`, pesos, τ, travas e
agregação.
1. Congelar um snapshot: `eval/snapshots/iter2.json` com `{caso_id: run_id}` (a lista de
   `iter2_reconstr.json`).
2. Mudança pequena no sistema: emitir `telemetria.evento("evidencias", **asdict(ev))` antes de `decidir()`,
   para não depender da reconstrução via `fontes`.
3. Novo `python3 -m eval.decisao --snapshot iter2` carrega `Evidencias`, roda `decidir()`, calcula todas as
   métricas da seção "Métricas propostas" e a curva cobertura × precisão variando τ.
4. As hipóteses H2 (peso da agência curada) e F (média com evidência unilateral) do ITERACOES podem ser
   testadas **agora**, sem gastar nada.

**Nível 1: replay completo (custo zero, ~4 min).** Para parsing, extração e travas que não mudam prompts.
```bash
python3 -m eval.run --modo replay --split dev --nome <iter>     # paralelo 1 até a ordenação dos lotes ser corrigida
```
- Reprovar ou excluir do delta os casos com `llm_miss > 0` (métrica nova).
- Depois de corrigir a ordem dos lotes do juiz, rodar duas vezes e exigir 70/70 iguais.

**Nível 2: busca congelada com LLM vivo (custo só de LLM).** Para prompt e modelo do juiz ou das
afirmações.
- Modo por host: SerpAPI e páginas em `replay`, OpenRouter e LLM local em `record`. Não existe hoje:
  sugestão de `IO_MODO_SERP=replay IO_MODO_LLM=record`.
- Custo aproximado de 6,6 chamadas por caso: ~130 chamadas na subamostra de 20 casos.
- Temperatura 0; repetir 2 vezes a subamostra para estimar a variância do juiz.
- Limitação: se o prompt de afirmações mudar a `consulta`, a SerpAPI dá miss. Nesse caso, subir para o
  nível 3.

**Nível 3: record na subamostra estratificada fixa (~50 buscas).** Para queries, agente e estratégia de
busca.
```bash
python3 -m eval.run --modo record --ids $(cat eval/subamostra20.txt) --max-buscas-serpapi 60 --nome <iter>
```
Subamostra sugerida (20 casos; rótulos balanceados; cobre as causas B/F/G e os casos de validade):
- **verdadeiro:** `cafe_nao_cura_cancer`, `falso_que_vacina_dna`, `ibuprofeno_piora_dengue`, `dengue_aedes`,
  `cr_voto_prova_de_vida_inss`, `cr_vulcao_havai_lava_300m`
- **sem_evidencia:** `economia_piorou`, `cr_vorcaro_pagou_dino`, `cr_xi_jinping_avc`
- **enganoso:** `cr_lula_acabar_bets_que_criou`, `cr_gilmar_reuniao_secreta_vorcaro`,
  `cr_bolsa_familia_15_aposentadoria_39`, `cr_lula_pobre_morrer_cortando_cana`
- **falso:** `cafe_cura_cancer`, `relato_cafe`, `satira_feriado`, `ibuprofeno_cura_dengue` (B),
  `cr_chico_cesar_proibiu_direita` (G), `cr_evangelicos_fogo_nossa_senhora` (F), `cr_hytalo_santos_morreu`
  (gabarito na SERP)
- Excluir `jn_soltura` e `ibge` até a reescrita ser corrigida.

**Nível 4: marcos.** Dev completo (~170 buscas, a Iteração 2 usou 165 queries), depois holdout (~60) uma vez
por rodada. Sempre com:
- `meta.snapshot_serp`;
- as linhas de trivial;
- os ICs;
- McNemar contra o marco anterior.

**Regra de aceitação:**
- Nenhum erro grave novo, em nenhuma direção.
- Acurácia balanceada ≥ anterior.
- Ganho com mecanismo confirmado em trace.
- Diferenças abaixo do limiar de McNemar (seção "Intervalos de confiança") registradas como "não
  distinguível".
- Atualizar o baseline para a Iteração 2 com os 70 casos; hoje o baseline tem 12.

**Higiene:**
- Backup dos cassetes (694 MB, gitignored), ou ao menos dos 256 SerpAPI e dos de LLM.
- Registrar a data do snapshot.
- Nunca comparar rodadas de snapshots diferentes.

## Pergunta 6: casos que faltam

Lacunas: sátira sem marcador de fonte; opinião; previsão; números e estatística; citação falsa ou
verdadeira; imagem fora de contexto descrita em texto; boato requentado; verdadeiro recente de agência de
notícias; verdadeiro contraintuitivo ("parece boato"); afirmação composta com parte verdadeira; negação que
afirma algo falso; texto que cita um boato para desmentir; corrente com várias afirmações; simetria
política; escrita informal; **entrada do tipo `link`** (0 casos hoje).

Rotulagem sem custo: fato estável verificável em fonte oficial (CF, lei no Planalto, IBGE, BCB) ou
atribuição documentada, conferido por uma pessoa offline; ou regra de construção (perturbação de fato
verdadeiro). Ao menos metade deve ir para o holdout.

| # | id sugerido | entrada | rótulo, esperado | tag | como rotular (grátis) |
|---|---|---|---|---|---|
| 1 | `satira_sem_marcador` | "Governo decreta que o real será trocado por figurinhas da Copa a partir de janeiro" | falso, [indet, alta], média aceitável | satira | absurdo por construção |
| 2 | `opiniao_pior_presidente_lula` | "O Lula é o pior presidente da história do Brasil" | não verificável, [indet] | opiniao | juízo de valor |
| 3 | `opiniao_pior_presidente_bolsonaro` | "O Bolsonaro foi o pior presidente da história do Brasil" | não verificável, [indet] | opiniao, simetria | par do #2; o nível deve ser igual |
| 4 | `previsao_dolar_10` | "O dólar vai passar de R$ 10 até dezembro" | não verificável, [indet] | previsao | futuro |
| 5 | `censo_2022_203mi` | "O Censo 2022 do IBGE contou cerca de 203 milhões de habitantes no Brasil" | verdadeiro, [baixa] | numero | IBGE (203,1 mi) |
| 6 | `censo_2022_250mi` | "Segundo o Censo 2022, o Brasil tem 250 milhões de habitantes" | falso, [alta] | numero, perturbacao | perturbação do #5 |
| 7 | `inflacao_caiu_precos_caem` | "A inflação caiu, então os preços no mercado estão ficando mais baratos" | enganoso, [alta, media] | numero, conceito | desinflação ≠ deflação (definição do IPCA) |
| 8 | `rebanho_maior_que_populacao` | "O Brasil tem mais bois do que gente" | verdadeiro, [baixa] | contraintuitivo, numero | IBGE/PPM: rebanho bovino acima de 230 mi |
| 9 | `einstein_insanidade` | "Einstein disse que insanidade é fazer a mesma coisa e esperar resultados diferentes" | falso, [alta] | citacao | atribuição falsa documentada |
| 10 | `vargas_carta_testamento` | "Getúlio Vargas escreveu na carta-testamento: 'Saio da vida para entrar na História'" | verdadeiro, [baixa] | citacao | texto histórico |
| 11 | `simetria_bolsonaro_deus` | "Bolsonaro disse num discurso que não acredita em Deus" | falso, [alta] | simetria, citacao | espelho de `cr_lula_nao_acredita_em_deus` (mesmo template) |
| 12 | `alan_kurdi_rs` | "A foto do menino sírio morto na praia foi tirada no litoral do Rio Grande do Sul" | falso, [alta] | midia_fora_contexto | Turquia, 2015 |
| 13 | `whatsapp_pago_corrente` | "Repassa: o WhatsApp vai ficar pago a partir de amanhã se você não enviar essa mensagem pra 10 contatos" | falso, [alta] | requentado | corrente clássica; checagens antigas já no índice local |
| 14 | `confisco_poupanca_informal` | "vc sabia q o governo vai confisca a poupança dnv??" | falso, [alta] | requentado, informal | boato recorrente; conferir a checagem antiga no índice |
| 15 | `cafe_maior_produtor_e_consumidor` | "O Brasil é o maior produtor e também o maior consumidor de café do mundo" | enganoso, [alta, media] | composta | produtor: sim; consumidor: não (EUA à frente) |
| 16 | `nao_e_verdade_terra_redonda` | "Não é verdade que a Terra seja redonda" | falso, [alta] | negacao | negação que afirma algo falso (contraparte de `falso_que_vacina_dna`) |
| 17 | `desmentido_cita_boato` | "Circula que café cura câncer, mas não há estudo que comprove isso" | verdadeiro, [baixa] | relato, desmentido | o texto é um desmentido |
| 18 | `pix_taxado_corrente` | "ATENÇÃO: a partir de segunda o PIX vai ser taxado em 5% e a Receita vai prender quem não declarar" | falso, [alta] | multi_afirmacao | amplamente checado; ambas as partes são falsas |
| 19 | `lei_seca_rodovias` | "É proibido vender bebida alcoólica na beira de rodovia federal" | verdadeiro, [baixa] | contraintuitivo | Lei 11.705/2008 |
| 20 | `link_agencia_brasil_*` (5 casos) | tipo `link`: URLs de matérias recentes da Agência Brasil (ex.: decisão do Copom, dado do IBGE) | verdadeiro, [baixa] | link, recente | coletar via RSS da Agência Brasil (grátis); rótulo = veículo público primário |
| 21 | `ab_recente_*` (10 casos) | a mesma notícia reescrita como usuário ("vi que o Copom manteve os juros em X%") | verdadeiro, [baixa]; a versão com número trocado vira falso | recente, perturbacao | Agência Brasil mais perturbação controlada; nenhuma checagem existe |

Os itens 20 e 21 são o caminho mais barato para equilibrar a classe verdadeiro: hoje são 8 no dev; a meta
é ao menos 30. Os pares perturbados (5/6, 3/2, 11/`lula_nao_acredita`, 21) formam o núcleo do split
`sem_checagem_publicada`.

Campos novos sugeridos por caso:
- `tipo_afirmacao` (fato, número, citação, mídia, previsão, opinião, composta);
- `midia_dependente`;
- `valido_em` (data da verdade);
- `checagem_publicada` (bool);
- `reescrita_revisada_por`;
- `snapshot_serp` (no resultado).
