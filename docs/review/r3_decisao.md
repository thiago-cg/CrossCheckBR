# R3: premissas de decisão do CrossCheckBR (agregador, sinais, modelo, testes)

Revisão só de leitura. O repositório não foi alterado (`git status` continua igual ao do início: `.gitignore`, `catalogo.json` e `modelo_fake.py` modificados pelo dono). As sondagens estão no scratchpad, todas offline, com SerpAPI, juiz, deep crawl e extração falsos:
`p1_agregador.py`, `p2_pipeline.py`, `p3_corrob.py`, `p4_selo.py`, `p5_bert.py`, `p6_bert_batch.py`, `p7_bert_vies.py`, `p8_duplo.py`, `p9_contagem.py`, `p10_neutro_relevante.py`, `p11_mutacao.py`.
Suíte do escopo (7 arquivos, 43 testes): **43 passed**. `torch` e `transformers` estão instalados. O BERTimbau foi carregado e rodou de verdade (2,8 s para carregar).

## Resumo executivo

1. **Três veículos independentes que DESMENTEM a afirmação resultam em propensão BAIXA.** Acontece com o juiz certo (score -90) e também sem LLM. O sinal "cobertura ampla" sempre reduz a propensão, qualquer que seja o sentido das fontes, e as faixas de decisão são assimétricas: tudo abaixo de +0,35 vira "baixa".
2. **O bug "Café cura câncer → BAIXA" não foi corrigido, só ficou escondido.** Ele volta sempre que o juiz marca `relevante=true` com score perto de 0, e também no modo sem LLM. Com `LLM_DAILY_CAP=50` e cerca de 16 chamadas por consulta, esse modo é o que roda a partir da ~4ª consulta do dia. Nesse modo o resultado depende **só de quantos veículos mencionam as palavras-chave**: 1 dá ALTA, 2 dá INDETERMINADA, 3 ou mais dá BAIXA.
3. **A direção do selo é tirada de substrings do rótulo, e o rótulo contém o nome do portal.** Qualquer selo do "Fato ou Fake" conta como +1, inclusive VERDADEIRO. Qualquer selo não-falso do "Aos Fatos" ou do "Fato ou Boato" conta como -1, e "BOATO" vira BAIXA.
4. **O BERTimbau/FakeBR mede tema e estilo, não veracidade.** É insensível à negação: "É falso que a vacina altera o DNA" dá 0,957, igual ao boato (0,947). Muda com a caixa das letras (0,27 passa a 0,94). Mesmo assim tem peso efetivo de 0,17, quase o mesmo de um sinal de corroboração que resume N fontes (0,19-0,20).
5. **Não existe eval rotulado.** Mutações absurdas nos `PESOS` (todos 0,01, ou invertidos) passam em 43 de 43 testes. O único teste que quebra ao inverter "cobertura ampla" é o que **trava o bug do item 1** como comportamento esperado.

---

## Achados por severidade

### C1: "Cobertura ampla" reduz a propensão mesmo quando as fontes refutam (CRÍTICO, confirmado)

**Onde:** `agregador.py:64-70` (convergência/ampla sem veredito dá -0,5), `pipeline.py:572-575` (emite "cobertura ampla" com n ≥ 3 relevantes, sem olhar sustenta/refuta), `pipeline.py:487-491` ("fontes refutam" é um único sinal).

**Evidência:** `p2_pipeline.py`, cenário 1a. Três veículos (G1 Fato ou Fake, Estadão Verifica, BBC) desmentem "Café cura câncer", com corpo lido e juiz em -90:
```
HEADER: 🟢 Propensão BAIXA a se tratar de desinformação
WHY   : 1 indício(s) elevam a propensão, 1 a reduzem; 3 fonte(s), 3 com corpo lido.
sinal [corroboracao] fontes refutam a afirmação | 3 fonte(s) contestam | conf=0.8
sinal [corroboracao] cobertura ampla | informação presente em 3+ veículos | conf=0.75
```
Pelo agregador puro (`p1`, cenário A), o score dá +0,227, que cai em "baixa". Com padrão de urgência somado (A3), dá +0,322 e continua "baixa". Com o BERTimbau real, o mesmo caso sobe para "media".

**Por que falha:** as mesmas 3 fontes geram dois sinais de sinais opostos. "Presença em N veículos" mede se o assunto é noticiável, não se a afirmação é verdadeira. Um desmentido amplo é justamente o caso mais coberto.

**Correção:**
- Remover "cobertura ampla" como sinal com direção.
- O número de clusters independentes deve apenas **modular a confiança** dos sinais de postura, por exemplo `conf = 1 - 0.5**n_clusters` aplicado a "refutam" e a "sustentam" separadamente.
- Teste de regressão: 3 fontes refutam deve dar `alta`.

**Trade-off:** sem "ampla", uma notícia verdadeira muito coberta depende do juiz marcar "sustenta" (≥ 60). Isso está correto: presença não é apoio.

### C2: Faixas assimétricas; neutro e ausência de evidência viram "baixa" (CRÍTICO, confirmado)

**Onde:** `agregador.py:99-107`.

**Evidência (`p1`):**
- Largura das faixas em [-1, 1]: **baixa 67,5% / media 15% / alta 17,5%**.
- Caso H: três sinais neutros (modelo 0,5, selo EXAGERADO, corroboração neutra) dão score 0,0 e resultado **BAIXA**.
- Caso D: evidência empatada (1 refuta, 1 sustenta), com `den < 0,5`, dá INDETERMINADA. Basta somar um sinal neutro de peso ≥ 0,05 (por exemplo o BERTimbau dizendo 0,45) para `den` passar de 0,5 e o resultado virar **BAIXA**.

"Indeterminada" depende da massa de pesos, não da evidência. Sinais com direção 0 entram no denominador e **diluem** o score em direção a 0, que já está dentro da faixa "baixa".

**Correção:** faixas simétricas e indeterminação por evidência. Por exemplo, trabalhar em log-odds: `L = Σ w_i · d_i · c_i`, só com sinais de d ≠ 0. Então:
- `|L| < τ` ou nenhum sinal externo de postura: indeterminada;
- `L ≥ τ_alta`: alta; `L ≤ -τ`: baixa; o resto: média.

Sinais neutros não entram. Os limiares τ saem do eval (ver o harness no fim).

**Trade-off:** mais "indeterminada" no começo. É honesto, e o recibo mostra as fontes.

### C3: A causa raiz de "Café cura câncer → BAIXA" continua ativa (CRÍTICO, confirmado)

**Onde:**
- `implicacao.py:42-45`: o fallback lexical dá `sustenta = 0.15 + 0.5·overlap`, ou seja, sobreposição de palavras é tratada como apoio, e `relevante=True` a partir de 0,30.
- `juiz_llm.py:86-89`: `relevante` booleano do juiz é aceito independentemente do score.
- `pipeline.py:461-471`: `lexico-top3` impõe `sustenta 0.35, relevante True`.
- `pipeline.py:572-580`: ampla/isolada contam só `relevante is True`.
- `pipeline.py:634`: a trava `tem_corpo` exige só "relevante + corpo lido", não postura.

**Evidência:**
- `p9_contagem.py`, sem LLM, com notícias que só falam de *risco* (nenhuma fala de cura):
  ```
  1 veículo  -> ALTA           sinais=['cobertura isolada']
  2 veículos -> INDETERMINADA  sinais=[]
  3 veículos -> BAIXA          sinais=['cobertura ampla']
  4 veículos -> BAIXA          sinais=['cobertura ampla']
  ```
- `p10_neutro_relevante.py`: o juiz devolve o que o prompt permite, `{"score": -10, "relevante": true}` (no tema, levemente contra). Resultado: **BAIXA**, "0 elevam, 1 reduz; 3 fontes, 3 com corpo lido".
- `p2` 1b: três desmentidos sem LLM dão **BAIXA**. O lexical nunca chega a `refuta ≥ 0,6` (teto 0,55), então no modo lexical **a evidência web só consegue empurrar a propensão para baixo**, e a faixa "alta" fica inalcançável por evidência.
- `p2` 3a: três matérias que só *reportam* o boato ("influenciador afirma que café cura câncer") dão **BAIXA**.

**Causa raiz real:** relevância (tema) e postura (sustenta/refuta) estão fundidas. Três mecanismos transformam "fala do tema" em "reduz propensão":
1. o lexical chama overlap de "sustenta";
2. "cobertura ampla" vale -0,5;
3. faixa "baixa" larga (C2).

A "correção" do loop 12 funcionou no teste ao vivo só porque o juiz marcou as fontes como `relevante=false`. O caminho que causa o bug continua intacto.

**Correção:**
- (a) O fallback lexical só pode decidir **recuperação e ordenação**, nunca postura nem relevância para decisão. Sem juiz, o resultado é "indeterminada + fontes para comparar".
- (b) Relevância para decisão exige `|score| ≥ JUIZ_RELEVANCIA_MIN`. Ignorar o booleano do juiz quando o score for próximo de 0.
- (c) A trava `tem_corpo` deve exigir **postura** (sus ou ref ≥ 0,6) além de corpo lido.
- (d) Aplicar C1 e C2.

**Trade-off:** sem cota de LLM o bot não opina. Isso é preferível a opinar errado. Subir o `LLM_DAILY_CAP` é decisão de custo.

### C4: Direção do selo por substring em texto com nome do portal (CRÍTICO, confirmado)

**Onde:** `agregador.py:57` (`v = rotulo + valor`), `agregador.py:62-63` (`"fake"`/`"falso"` dá +1; `"verdadeiro"`/`"fato"` dá -1). `pipeline.py:268` põe o nome do portal no rótulo.

**Evidência:**
- `p4_selo.py`, pipeline real sem LLM, mesma checagem e mesmo selo:
  ```
  selo VERDADEIRO, portal "Fato ou Fake (G1)" -> 🔴 ALTA
  selo VERDADEIRO, portal "Lupa"              -> 🟢 BAIXA
  selo DISTORCIDO, portal "Aos Fatos"         -> 🟢 BAIXA   ("fato" em "Aos Fatos")
  selo BOATO,      portal "Fato ou Boato (TSE)" -> 🟢 BAIXA  (boato vira "reduz")
  ```
- `p1`, casos F4 a F6: "EXAGERADO", "INSUSTENTÁVEL", "BOATO" e "FARSA" dão direção 0. "NÃO É FATO" e "VERDADEIRO, MAS" dão -1. O portal "Saúde sem Fake News" transforma qualquer selo em +1.

**Por que falha:** a taxonomia das 13 agências (Lupa: EXAGERADO/INSUSTENTÁVEL/DE OLHO; Aos Fatos: DISTORCIDO/IMPRECISO/NÃO É BEM ASSIM; Boatos.org: BOATO; E-Farsas: FARSA) não cabe em três substrings. Além disso, o texto de exibição é usado como dado.

**Correção:**
- Campo tipado `veredito_norm: Literal["falso","enganoso","impreciso","sem_contexto","verdadeiro","inconclusivo"]`, preenchido no crawl por uma tabela de mapeamento por checador (ou vindo do `ClaimReview.reviewRating`).
- `_direcao` lê **só** esse campo e a postura da implicação, nunca `rotulo` ou `valor`. O mesmo vale para `corroboracao` e `laya`.

**Trade-off:** mapear as taxonomias dá trabalho manual (uma vez por checador).

### C5: O modelo BERTimbau/FakeBR não serve para afirmações curtas e pesa quase o mesmo que toda a corroboração (CRÍTICO, confirmado nas sondas)

**Onde:**
- `modelo_fake.py:98-109` (diff local); `pipeline.py:586-590` (`conf_modelo = 0.85` fixo, texto de entrada = mensagem do usuário);
- `agregador.py:76-81` (degrau: p ≥ 0,6 dá +1, p ≤ 0,4 dá -1);
- peso efetivo 0,20 × 0,85 = **0,17**, quase igual ao sinal "refutam" (0,25 × 0,8 = 0,20), que é **um** sinal para N fontes. Só o selo (0,40 × até 0,9) pesa mais, e o selo quase nunca existe (A1).

**Evidência (`p5`, `p7`; prob calibrada, isto é, a saída de produção):**
```
0.625 Café cura câncer                 | 0.873 Café não cura câncer   (a verdade pontua MAIS "fake")
0.947 Vacina da covid altera o DNA     | 0.957 É falso que a vacina da covid altera o DNA
0.952 Estudo confirma que vacina da covid não altera o DNA humano
0.268 O Senado aprovou nesta terça ... (formal) | 0.939 mesma frase em minúsculas
0.67  Governo anuncia reajuste do salário mínimo | 0.966 O governo anuncia reajuste ... para 2027
0.974 Lula / 0.967 Bolsonaro / 0.960 Tarcísio anuncia reajuste ... para 2027
```
- Lote `p6` (feito à mão, com viés de estilo meu): 10 manchetes verdadeiras dão 8 abaixo de 0,4 e 2 acima de 0,6; 6 boatos dão 6 acima de 0,6. A separação aparente é **tema e estilo** (manchete com número e formal contra tema típico de boato), não verificação. Isso fica claro com a negação e com a caixa das letras.
- No agregador (`p1` e script inline): 3 fontes **sustentam** com corpo lido + BERTimbau 0,957 (usuário mandou um desmentido) dá `indeterminada`, porque o modelo anula 3 corpos lidos. 3 fontes refutam + modelo em "Café não cura câncer" dá `media`.

**Ordem das labels e calibração:**
- `config.json` **não tem `id2label`** (`{0:'LABEL_0',1:'LABEL_1'}`). O código supõe `logit[1] = fake`. As sondas são coerentes com isso (sensacionalista dá 0,955, texto formal dá 0,268). Classifico como **plausivelmente correta, mas não documentada nem verificável**: o script de treino não está no repo, só há `script_sha256`.
- A margem `logit[1]-logit[0]` é a margem certa para o Platt de 2 classes, se a suposição acima valer.
- O Platt (`a=0,62, b=0,058`) foi ajustado em `val_calib` do próprio FakeBR (`calib_prior_fake=0,52`), então a calibração **não transfere** para títulos curtos de usuário.
- O `threshold_val_opt=0,42` do treino é ignorado; o agregador usa 0,6/0,4.
- O carregamento falha em silêncio para o mock (`modelo_fake.py:124-126`), apesar do docstring dizer "falha no boot".

**Mock:** com `FAKE_MODEL_PATH` vazio, o mock devolve 0,5 (direção 0, mas entra no denominador e dilui o score, ver C2). Com "URGENTE/COMPARTILHE" ele devolve cerca de 0,73 (+1) e duplica o regex de `padroes_llm.py:15`: mesma pista, contada duas vezes (A2). A justificativa chega a listar "Aumentam: modelo mock (PLACEHOLDER)" como motivo (`p4` S5).

**Correção:**
- Tirar o detector de estilo do agregador de *veracidade*. Exibir à parte como "estilo do texto recebido: se parece com boatos (X%)", e só para `tipo="texto"` com ≥ ~80 tokens, que é o domínio do FakeBR.
- Se ficar no agregador: confiança derivada de `|p-0,5|`, contínua e sem degrau, peso aprendido no eval, e um teste de invariância à negação e à caixa.
- Gravar `id2label` no `config.json` e checar no boot.

**Trade-off:** perde-se o "% do modelo", que é uma promessa do README. Mas o número atual induz ao erro.

### A1: O índice de vereditos, o caminho feliz com peso 0,40, está praticamente vazio (ALTO, confirmado)

**Onde:** `data/amostra_checagem.json`; `pipeline.py:204-229`; `PESOS["veredito-existente"]=0.40`.

**Evidência:**
- São 17 documentos: 16 homepages ou páginas "sobre" e **1** com veredito (`VERDADEIRO`, `confianca_veredito 1.0`, numa *reportagem sobre golpes* do ValorInveste, que não é checagem).
- Sem SerpAPI, 10 afirmações comuns dão **9 INDETERMINADA e 1 BAIXA** (a que casou com a matéria de golpes).
- O "80% resolvido em segundos" do README não tem base de dados que o sustente.

**Correção:** popular o índice com `ClaimReview` (schema.org, que as agências brasileiras publicam, ou a Google Fact Check Tools API). Isso já traz `claimReviewed` (a afirmação checada, necessária para comparar polaridade) e `reviewRating` (resolve C4). Buscar por **similaridade da afirmação checada**, não por título + corpo.

**Trade-off:** dependência externa e cobertura parcial de agências.

### A2: Dupla contagem e contagem inconsistente (ALTO, confirmado)

**Onde:** `pipeline.py:237-274` (um sinal `veredito-existente` **e** um `laya` por *página*); `corroboracao.py:84-87` (convergência com 2+ peças com veredito, sem checar se são checadores diferentes); `pipeline.py:487-512` (um único sinal para N fontes).

**Evidência (`p8_duplo.py`):** 2 páginas da **mesma** Lupa com FALSO:
```
PROP: alta | WHY: 5 indício(s) elevam a propensão ...
veredito-existente ×2, laya ×2, "checadores convergem para o mesmo veredito" ×1
```
Já 10 veículos refutando geram **1** sinal. Isso também está correlacionado:
- `modelo-fake` e `llm-padroes` leem o mesmo texto do usuário;
- o mock e o regex de padrões disparam na mesma palavra;
- "refutam", "ampla", "isolada" e "convergência" saem todos do mesmo conjunto de fontes julgadas pelo mesmo juiz.

A soma ponderada supõe independência, e essa suposição não vale.

**Correção:** agregar em dois níveis.
1. Agrupar a evidência em **clusters independentes** (checador ou grupo de mídia; republicação conta 1). Cada cluster dá **um** voto de postura com a confiança do juiz.
2. Combinar clusters (por exemplo `L_ext = Σ_clusters logit(conf)·postura`, com teto) e somar **um** termo de "estilo" com peso pequeno, que nunca inverte o sentido de `L_ext`.

O `laya` deve só ajustar a confiança do selo, não virar um voto novo.

### A3: A "independência" é por domínio + Jaccard de título; agências não são detectadas; divergência de data não funciona (ALTO, confirmado)

**Onde:** `corroboracao.py:56,60` lê `corpo_texto`, mas o pipeline passa `FonteEvidencia.model_dump()` (`pipeline.py:563-566`), que tem `trecho_corpo`, então a comparação é **só por título**. `corroboracao.py:74` lê `data_publicacao`, mas o schema tem `data_pub` (`schemas.py:46`).

**Evidência (`p3_corrob.py`):**
```
A) Estadão Conteúdo em 4 domínios, corpo IDÊNTICO, títulos editados (dicts crus): 3
B) mesmo caso via FonteEvidencia.model_dump() (como no pipeline):                   4
C) Agência Brasil republicada (só títulos):                                         3
D) divergencias com datas 2019 vs 2026 via model_dump: []        <- nunca dispara
   mesmas peças com chave 'data_publicacao': [{'campo': 'data', 'tipo': 'divergencia', ...}]
E) blogspot.com / medium.com / youtube.com: canais distintos viram "mesmo grupo"
```
Não há nenhuma detecção de agência ("Estadão Conteúdo", "Agência Brasil", "Folhapress", "Reuters", "AFP"). O caso do "vídeo antigo reciclado", que é o motivo da pergunta-guia ① sobre datas, está morto.

**Correção:**
- Consertar as chaves (`trecho_corpo`, `data_pub`).
- Detectar agência por regex no corpo lido ou na assinatura.
- Usar MinHash/SimHash do corpo (limiar ~0,6) e URL canônica.
- Tabela de grupos econômicos em vez de "domínio base".
- Tratar plataformas (youtube, blogspot, medium) por *canal*.

**Trade-off:** o limiar de near-duplicate precisa de calibração; sem corpo lido, o título continua sendo o fallback.

### A4: Reportar o boato ≠ sustentá-lo; resumo "sem veredito" + juiz cego ao texto (ALTO; 3a confirmado, 3b plausível)

**Onde:** `juiz_llm.py:27-38`. O resumidor recebe a instrução "sem dar veredito", o que pode apagar o "é falso" do desmentido. O juiz vê **só o resumo**. A escala "+100 sustenta como fato verdadeiro" não tem classe para "relata que alguém afirma".

**Evidência:** `p2` 3a (sem LLM): matérias que reportam o boato viralizando dão **BAIXA**. Em 3b, um juiz que pontua "relata que um influenciador afirma X" como +70 também dá **BAIXA**. A resposta de um LLM real aqui é **plausível**, não medida (sem rede nesta revisão).

O caso da pergunta 4 ("É falso que café cura câncer", palavras-chave batendo):
- **com juiz**, é tratado corretamente no nível do item (-90);
- **sem juiz**, `_fallback_lexico` dá `sus 0,48 / ref 0,55`, os dois abaixo de 0,6, e a peça conta como relevante;
- nos **dois casos** o resultado agregado é BAIXA, por causa de C1 e C2.

**Correção:**
- Postura em 4 classes: `SUSTENTA / REFUTA / RELATA_SEM_ENDOSSO / NAO_TRATA`, com **citação obrigatória** do trecho da fonte que justifica. Rejeitar a resposta se a citação não estiver no texto.
- Julgar sobre o **texto da fonte** (estilo NLI), não sobre um resumo feito sob a instrução "sem veredito".
- Se o resumo ficar, exigir atribuição ("segundo X…", "a checagem classifica como…").

### A5: As travas do pipeline anulam o agregador; a decisão real é uma árvore implícita (ALTO, confirmado)

**Onde:** `pipeline.py:614-643`.

**Evidência:** `p11_mutacao.py`:
```
PESOS todos = 0.01                              -> 43 passed
PESOS invertidos (modelo 0.40, veredito 0.05)   -> 43 passed
```
No caminho lexical, `tem_veredito` nunca é verdadeiro (teto 0,55 < 0,6), mas `tem_corpo` é. Então o selo decide mesmo assim (`p4` S1-S4), contrariando o "lexical nunca crava veredito".

**Correção:** tornar explícita a política de decisão: (1) elegibilidade, (2) postura agregada, (3) mapeamento para a escala. Pesos e limiares ficam em um único lugar, testado pelo eval.

### M1: Incoerência entre o "why" e o nível (MÉDIO, confirmado)

**Onde:** `pipeline.py:626-643` anexa texto a `agg["justificativa"]`, que foi escrita com o nível **anterior** à trava. `pipeline.py:667-669` descarta fontes julgadas irrelevantes que não são genéricas. `agregador.py:127-134` conta *sinais*, não fontes.

**Evidência:**
- `p4` S5: `HEADER: ⚪ INDETERMINADA` e `JUSTIF: "Propensão alta a se tratar de desinformação. Aumentam…: modelo mock (PLACEHOLDER)… Sem checagem prévia…: elementos insuficientes"`.
- `p2` 2b: 3 fontes lidas e julgadas fora do tema, e o why diz "**0 fonte(s) consultada(s)**". O PROGRESSO diz que elas iam "p/ o fim da lista (resto auditável)", mas `uteis + resto` exclui `relevante is False` que não é genérica.
- `p2` 1a: "1 indício eleva, 1 reduz" para 3 refutações, com resultado BAIXA.

**Correção:**
- Redigir a justificativa **depois** da decisão final, a partir de uma estrutura `{nivel, motivo_principal, clusters_pro, clusters_contra, descartadas}`.
- O why conta *clusters de fontes*.
- Seção "consultadas e descartadas (fora do tema)".

### M2: Pesos decorativos e confiança fixa (MÉDIO, confirmado)

- `PESOS["llm-juiz"]` é código morto: nenhum sinal usa `motor="llm-juiz"`; o juiz alimenta `corroboracao` e `veredito-existente`.
- Os `PESOS` somam 1,05.
- As confianças são constantes (0,8 / 0,75 / 0,7 / 0,65) multiplicadas no peso, o que escala o peso duas vezes.
- Um sinal isolado satura: `modelo 0,61` dá ALTA (score 1,0); `0,59` dá INDETERMINADA (`p1` G/G2).
- O degrau em 0,6 no termômetro faz um -59 valer 0 e um -60 valer uma refutação inteira.

**Correção:** pesos e limiares ajustados no eval; postura contínua.

### M3: Ausência tratada como evidência (MÉDIO, confirmado)

`pipeline.py:576-580`: "cobertura isolada" vale +1 com confiança 0,7. Na maioria dos caminhos a trava esconde isso, mas **1 veículo relevante com corpo** dá ALTA sem LLM (`p9`) e anula uma fonte que sustenta com LLM. Para fato do dia, estar em um só veículo é normal. **Correção:** remover; ausência só reduz a confiança e gera limitação no recibo.

### M4: Padrões com rótulo "sem_data_local" (MÉDIO; LLM plausível, regex confirmado no código)

`padroes_llm.py:20-21` oferece ao LLM o rótulo `sem_data_local`. Em título curto, o LLM tende a marcá-lo sempre (**plausível**, não medido), o que dá +1 com confiança 0,65. No fallback regex, qualquer texto com ≥ 300 caracteres sem nome de mês dispara o padrão. **Correção:** remover do catálogo de padrões, porque ausência de data não é padrão de desinformação, ou aplicá-lo só a `tipo="texto"`.

### B1: Link pela API (BAIXO, confirmado no código)

Na API, `tipo="link"` passa só a URL para o detector e para os padrões (`api.py:27`; `pipeline.py:586`). O bot concatena o corpo, a API não.

---

## Pergunta 5: os testes

| Teste | O que verifica de fato |
|---|---|
| `test_regressao_36` | Só que `propensao ∈ {4 valores}` (o `Literal` já garante), que as etapas existem e que o header não está vazio. Os "36 artigos" são quase todos homepages. **Não há rótulo esperado.** É smoke test, não regressão. |
| `test_pipeline_sem_nada_indeterminada_ou_baixa`, `test_opiniao_nao_vira_veredito` | **Aceitam "baixa"** sem nenhuma evidência. Isso codifica C2 como aceitável. |
| `test_agregador_extremos` | Com um sinal só, `score = ±1` para qualquer peso. Mutação dos `PESOS`: 43/43 passam. |
| `test_cobertura_ampla_pesa_mesmo_com_valor_neutro` | **Trava o bug C1** (`_direcao == -0.5`). É o único teste que quebra quando a mutação corrige o sentido. |
| `test_pipeline_offline_com_veredito` | O comentário diz "FALSO + padrões + cobertura isolada", mas "isolada" nunca dispara quando há veredito (`pipeline.py:576`). Passa por outro motivo: selo com confiança lexical 0,55 + mock 0,81 + regex, e os dois últimos são a mesma pista. |
| `test_juiz_llm.*`, `test_review::test_llm_juiz_*` | Mockam `resumir` **e** `termometro`. Testam bem o encanamento (teto, remapeamento, concorrência), nada da semântica. |
| Caminho web (`pipeline.py:300-557`) | Nenhum teste do escopo alimenta resultados SerpAPI **que refutam**. Os casos das sondas `p2` e `p9` não existem na suíte. |

**Eval rotulado:** não existe. Não há conjunto `afirmação → rótulo`, nem métrica (acurácia, macro-F1, ECE, taxa de indeterminada), nem baseline (ROADMAP Fase 2, pendente). Sem isso, nenhuma mudança de peso, limiar ou prompt pode ser dita melhor ou pior. Os "fixes" dos loops 9-12 foram validados por um caso ao vivo cada, e C3 mostra que o caso ao vivo passou pelo motivo errado.

---

## Diagnóstico das premissas de decisão

| Premissa | Situação |
|---|---|
| "Agregar sinais heterogêneos numa escala única de propensão" | Mistura três grandezas diferentes: **veracidade da afirmação** (juiz/selos), **estilo da mensagem** (modelo, padrões) e **noticiabilidade** (cobertura). Não são comensuráveis, e a soma ponderada gera saídas sem significado, como 3 desmentidos resultando em "baixa". |
| "Sinais independentes, pesos como priors" | São fortemente correlacionados (mesmo texto, mesmas fontes, mesmo juiz). Os priors nunca foram testados; a suíte é indiferente a eles. |
| "Implicação domina o selo" | Correta como regra, mas implementada por substring de texto de exibição (C4), e o `laya` a ignora (cenário F3: selo FALSO + fonte sustenta dá um voto de cada lado). |
| "Ausência de evidência é informativa" (cobre/isola) | Falsa para boatos novos e notícias do dia. Hoje funciona como evidência nos dois sentidos, dependendo de N. |
| "Lexical honesto como fallback" | Lexical mede tema, não postura. Tratá-lo como "sustenta" com teto não o torna honesto: só impede "alta" e deixa "baixa" livre. Na prática é o modo **dominante** por causa do teto diário de LLM. |
| "Modelo de fake news com % explícita" | Um classificador de estilo treinado em artigos FakeBR (2016-18) aplicado a títulos curtos: insensível à negação e sensível à caixa das letras. O "%" é preciso na forma e sem validade no conteúdo. |
| "Sem veredito + templates neutros = neutro" | A neutralidade lexical está garantida, mas a **cor e o nível** (🟢 BAIXA) já são um veredito implícito, e hoje frequentemente o errado. Neutralidade que importa é a do nível estar correto e o why ser coerente. |
| "Índice de 13 agências resolve 80%" | Sem dados: 1 veredito em 17 documentos. |

A saída é interpretável? **Parcialmente.** O recibo por etapa é bom. Mas o nível, o emoji e o why frequentemente não batem entre si (M1), e o nível responde a quantas fontes existem, não ao que elas dizem.

---

## Proposta de desenho corrigido (curta)

1. **Separar três eixos na resposta:**
   - (i) *Evidência sobre a afirmação*: postura agregada das fontes, em `refutada / contestada / sem evidência / corroborada`;
   - (ii) *Checagens existentes*: selo normalizado, com link;
   - (iii) *Sinais de estilo do texto recebido*: modelo e padrões, informativos, **fora** do nível.
   A "propensão" passa a ser função **só** de (i) e (ii).
2. **Unidade de evidência = cluster independente** (checador ou grupo de mídia, com dedupe de agência/MinHash). Cada cluster dá um voto `postura ∈ {+1, -1, 0}` × confiança do juiz, só se `|score| ≥ 30` **e** houver citação verificada no texto da fonte.
3. **Decisão:**
   - `L = Σ_clusters c_k·d_k` (+ selo normalizado com peso maior e polaridade em relação ao `claimReviewed`);
   - `indeterminada` se `n_clusters_com_postura = 0` ou `|L| < τ₀`;
   - senão, faixas **simétricas** `τ₀ < |L| < τ₁` resultam em "média" no sentido de L, e acima de τ₁ em alta ou baixa.
   - Sem juiz, nunca há nível: só as fontes.
4. **Juiz:** 4 classes (`SUSTENTA / REFUTA / RELATA_SEM_ENDOSSO / NAO_TRATA`) sobre o texto da fonte, com citação obrigatória; a polaridade da afirmação é extraída ("É falso que X" vira alvo X com negação).
5. **Justificativa** gerada a partir da decisão final. O why conta clusters. As fontes descartadas ficam visíveis.

### Harness de eval mínimo (prioridade, antes de qualquer novo ajuste)

```
evals/claims_ptbr.jsonl   # 150-300 linhas, congelado
{"id":"c001","afirmacao":"Café cura câncer","rotulo":"falso","fonte_rotulo":"https://lupa.../x",
 "tipo":"titulo","data":"2026-08", "tags":["saude","curto"]}
# rótulos: falso | enganoso | verdadeiro | sem_evidencia ; ~40% falso, 30% verdadeiro, 30% difícil
# fonte: ClaimReview das 13 agências (rótulo humano) + manchetes verdadeiras de agências (Agência Brasil)
# + pares adversariais: negação ("É falso que X"), relato ("Post diz que X"), minúsculas, sátira

evals/snapshots/<id>.json # resultados SerpAPI + corpos lidos + saídas do juiz CONGELADOS
                          # -> replay offline determinístico (pipeline com FakeSerp/aprofundar, como nas sondas p2)
evals/run_eval.py
  para cada linha: rel = Pipeline(...).executar(...) em modo replay
  métricas: matriz de confusão {alta,media,baixa,indet} x rótulo;
            "erro grave" = falso->baixa ou verdadeiro->alta (meta ~0);
            taxa de indeterminada; cobertura (fração com nível);
            ECE/Brier do termômetro do juiz e do BERTimbau por item;
            invariâncias: nivel(X) vs nivel("É falso que X") devem ser opostos; minúsculas não mudam o nível
  saída: evals/relatorio_<git-sha>.json + diff contra o último baseline
CI: falha se "erro grave" subir ou a macro-F1 cair > 2 p.p. contra o baseline commitado
```
Os casos das sondas `p2` 1a, 2a, 3a, `p4` S1-S4 e `p9` entram como os primeiros 12 itens, com o nível esperado. Com isso, C1 a C4 viram testes que falham hoje e servem como critério de pronto da correção.
