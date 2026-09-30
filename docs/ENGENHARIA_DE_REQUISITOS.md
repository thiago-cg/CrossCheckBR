# Crosscheck BR — Engenharia de Requisitos

| | |
|---|---|
| **Produto** | Crosscheck BR — checagem cruzada de fatos em larga escala |
| **Repositório principal** | `thiago-cg/CrossCheckBR` |
| **Repositório do modelo e dos dados** | `thiago-cg/FakenewsBR` |
| **Versão do sistema** | `mvp-0.2.0`, código da `main` em `17fc2bd` (pull request nº 4) |
| **Data deste documento** | 30 de setembro de 2026 |
| **Equipe** | Thiago Correia Gonzaga, Gabriella Oliveira de Souza Dias, Maria Eduarda Denis Duarte Marques, Guilherme Silva Dutra, Eliane Orlandin do Carmo, Daltro Oliveira Vinuto |

Este documento registra os requisitos do produto e a situação de cada um no
código. As situações usam três valores:

- **Atendido**: implementado e coberto por teste automatizado.
- **Parcial**: implementado, com uma lacuna descrita no próprio requisito.
- **Pendente**: ainda não implementado.

Este documento descreve a branch `main` depois do pull request nº 4
(telemetria, avaliação e núcleo de decisão). Os itens marcados com **†** existem
só na branch `melhorias/revisao-arquitetura`, ainda não integrada (P01).

**Onde está cada informação.** Cada dado fica em um único lugar; os demais
pontos apontam para ele.

| Informação | Lugar único |
|---|---|
| Indicadores e metas | [Seção 1.4](#14-indicadores-de-desempenho) |
| Requisitos funcionais, histórias de usuário e regras de negócio | [Seção 2](#2-requisitos-funcionais) |
| Requisitos não funcionais RNF01 a RNF05 e restrições | [Seção 3](#3-requisitos-não-funcionais) |
| Números do modelo e dos dados | [Seção 4](#4-requisitos-do-modelo-de-aprendizado-de-máquina-e-dos-dados) |
| Arquivos, testes, integrações e pendências | [Seção 5](#5-matriz-de-rastreabilidade-e-mapeamento-de-integrações-externas) |
| Requisitos RNF06 a RNF09, ciclo de vida, operação, monitoramento e Machine Learning Canvas | [Engenharia de Produto de Inteligência Artificial](ENGENHARIA_DE_PRODUTO_DE_IA.md) |

**Identificadores.** RF: requisito funcional. RNF: requisito não funcional.
RN: regra de negócio. HU: história de usuário. IND: indicador. P: pendência.

---

## 1. Visão Geral e Proposta de Valor

### 1.1 Descrição do produto

O Crosscheck BR é um assistente de checagem de fatos. A pessoa envia o texto,
o título ou o link de uma notícia e recebe uma avaliação da **propensão** de
aquele conteúdo ser desinformação, em uma escala de quatro valores: baixa,
média, alta ou indeterminada. A resposta vem acompanhada das evidências
encontradas, com os endereços das fontes, e do passo a passo que levou à
avaliação.

> [!IMPORTANT]
> **Diretriz ética: a resposta nunca é binária (RF12).** O sistema é
> probabilístico e não emite veredito. Ele nunca afirma que algo "é falso" ou
> "é verdade". O objetivo é dar à pessoa os elementos para que ela mesma avalie.

A avaliação vem só de evidência sobre a **veracidade** da afirmação:

1. **Checagens existentes**: o selo que uma agência de checagem já deu à
   afirmação, lido de forma tipada (por exemplo, "Distorcido" vira
   "enganoso"), e só quando a página trata da afirmação.
2. **Postura das fontes**: cada notícia encontrada é julgada como
   "sustenta", "refuta", "relata sem endossar" ou "não trata", com um trecho
   citado que o sistema confere no texto da fonte. Veículos que republicam o
   mesmo conteúdo contam como um só.

Dois sinais aparecem na resposta, mas **não entram na avaliação** (RN11),
porque medem o estilo do texto e não a veracidade:

3. **Modelo próprio de aprendizado de máquina**: estima a probabilidade de o
   texto ser desinformação a partir do estilo.
4. **Padrões textuais**: marcas típicas de desinformação, como apelo à
   urgência e fonte vaga.

### 1.2 Objetivos sociais e de negócio

Cada objetivo é medido pelos indicadores da [seção 1.4](#14-indicadores-de-desempenho).

| Objetivo | Descrição | Indicadores |
|---|---|---|
| Reduzir o compartilhamento de desinformação | Oferecer, no momento em que a pessoa recebe a mensagem, os elementos para decidir se compartilha. | IND03, IND04, IND05, IND12 |
| Fortalecer a autonomia de quem lê | Mostrar evidências e ensinar a conferi-las, em vez de entregar uma conclusão pronta. | IND01, IND13 |
| Dar visibilidade ao trabalho das agências de checagem | Levar a pessoa à checagem original, com o endereço e o selo da agência. | IND06 |
| Manter a confiança por meio da transparência | Expor as fontes, as etapas executadas e as limitações de cada resposta. | IND01, IND02, IND07, IND09 |

### 1.3 Público-alvo e partes interessadas

| Grupo | Quem | Necessidade ou interesse |
|---|---|---|
| **Usuários diretos** | Pessoas leigas que recebem notícias em aplicativos de mensagem | Saber, em linguagem simples, se vale a pena desconfiar de uma mensagem. |
| | Estudantes e professores | Usar a resposta como material para aprender a avaliar fontes. |
| | Jornalistas e checadores | Fazer uma triagem rápida do que já foi checado e por quem. |
| | Equipe do projeto | Auditar cada resposta pelo registro das etapas e das fontes. |
| **Usuários indiretos** | Contatos de quem consulta; agências de checagem citadas | Menos boatos em circulação; crédito e visitas às checagens originais. |
| **Clientes** | Não há cliente pagante: o projeto é acadêmico. Docentes e a instituição do curso acompanham | Aprendizado, rigor e resultados demonstráveis. |
| **Outras partes** | Veículos cujo conteúdo é lido; Telegram e serviços externos; pessoas citadas nas notícias; autoridade de proteção de dados | Uso correto do conteúdo; respeito aos termos de uso; não ser rotulado injustamente; privacidade. |

**Classes de usuário**

| Classe | Quem | Como o produto trata |
|---|---|---|
| Favorecida | Pessoa leiga que recebeu uma mensagem duvidosa | As decisões de linguagem e de formato priorizam essa pessoa (RNF04). |
| Desfavorecida | Quem tenta manipular o sistema para validar um boato | Proteções descritas no RNF07; o modelo não decide sozinho. |
| Ignorada, por enquanto | Quem quer checar imagem, vídeo ou áudio | Recebe orientação (RF03); a análise está fora do escopo (HU04). |

Personas e análise estratégica, ainda em rascunho, estão na
[seção 1.7](#17-personas-e-análise-estratégica-rascunho).

### 1.4 Indicadores de desempenho

A tabela traduz o problema em indicadores. A coluna "valor atual" traz apenas
números medidos; onde não há medição, isso está dito. As metas são
**propostas deste documento** e precisam ser validadas pela equipe.

<div class="cc-kpis">
<div class="cc-kpi"><span class="cc-kpi-rotulo">IND01 · Transparência</span><span class="cc-kpi-valor">100%</span><span class="cc-kpi-texto">Respostas com fontes, etapas e limitações. Garantido por construção e por teste automatizado. Meta: 100%.</span></div>
<div class="cc-kpi"><span class="cc-kpi-rotulo">IND02 · Neutralidade</span><span class="cc-kpi-valor">100%</span><span class="cc-kpi-texto">Respostas sem conclusão binária e sem termos partidários, nos testes automatizados. Meta: 100%.</span></div>
<div class="cc-kpi"><span class="cc-kpi-rotulo">IND03 · Cobertura</span><span class="cc-kpi-valor">61%</span><span class="cc-kpi-texto">Casos da avaliação que não terminam em "indeterminada" (43 de 70). Meta: acima de 60%.</span></div>
</div>

Os três indicadores acima não se repetem na tabela. Os números do modelo
ficam só na [seção 4.2](#42-métricas-de-avaliação-fora-de-produção).

Os valores de IND03 a IND05 vêm da avaliação da equipe em `eval/`: 70 casos
de desenvolvimento (12 escritos pela equipe e 58 tirados de checagens com
ClaimReview), iteração 2, com busca e modelo de linguagem ligados. A própria
equipe registra que esses números foram reconstruídos dos registros de
execução de uma rodada interrompida, e não de uma rodada completa
(`eval/ITERACOES.md`).

| Indicador | O que mede | Como medir | Valor atual | Meta proposta |
|---|---|---|---|---|
| IND04 · Acerto | Propensão alta para alegações falsas ou enganosas, baixa para verdadeiras | `eval/run.py` | 29% (20 de 70); cerca de 56% contando "média" em alegação falsa como acerto parcial | Acima de 85% |
| IND05 · Erro grave | Propensão baixa em alegação falsa, ou alta em alegação verdadeira | `eval/run.py` | 4,3% (3 de 70). Era 33% (4 de 12) antes do núcleo de decisão | Abaixo de 5% |
| IND06 · Checagem original encontrada | A checagem da agência aparece entre as fontes | `eval/run.py` (a medir) | Não medido | Acima de 70% |
| IND07 · Estabilidade da resposta | A mesma notícia, em versão curta e longa, recebe a mesma propensão | Conjunto de estabilidade (planejado) | Não medido; instabilidade observada em testes manuais | Acima de 90% |
| IND08 · Qualidade do modelo | Medida F1 macro, pior grupo e calibração | Avaliação do FakenewsBR | Ver [seção 4.2](#42-métricas-de-avaliação-fora-de-produção) | Ver requisitos de qualidade na [seção 4.2](#42-métricas-de-avaliação-fora-de-produção) |
| IND09 · Utilidade percebida | Proporção de avaliações positivas nos botões de avaliação | `feedback.jsonl` † | Sem dados: o recurso não está na `main` | Acima de 70% |
| IND10 · Retenção | Pessoas que voltam a consultar em até 30 dias | Registro de uso (pendente) | Sem medição | A definir |
| IND11 · Tempo de resposta | Tempo entre o envio e a resposta completa | Telemetria por execução (`runs/`) | Ver [RNF06](ENGENHARIA_DE_PRODUTO_DE_IA.md#rnf06--responder-dentro-dos-limites-de-tempo-e-de-custo) | Ver RNF06 |
| IND12 · Desistência de compartilhar | Pessoas que dizem ter desistido de compartilhar após a resposta | Pergunta opcional após a resposta (pendente) | Sem medição | A definir |
| IND13 · Abertura de fontes | Respostas em que a pessoa abre ao menos uma fonte | Contagem de cliques nos endereços (pendente) | Sem medição | A definir |

### 1.5 Escopo inicial e limitações declaradas

**Entradas aceitas**

| Entrada | Tratamento |
|---|---|
| Texto da notícia | Analisado integralmente, até 20.000 caracteres. |
| Título | Mensagem de até 140 caracteres em uma linha; analisada como afirmação. |
| Link | Lido somente quando o endereço pertence a um portal do catálogo. Fora do catálogo, o sistema pede que a pessoa cole o texto. † |

**Entradas bloqueadas**

> [!CAUTION]
> **Restrição de escopo (RF03).** Imagens, vídeos, arquivos, áudios e mensagens
> de voz não são analisados. A pessoa recebe uma mensagem de orientação, nunca
> silêncio.

**Limitações conhecidas**

1. **O sistema não emite veredito.** A resposta é uma estimativa de propensão.
2. **O modelo próprio não entra na avaliação** (RN11). Ele mede o estilo do
   texto e erra muito em textos curtos: ver o acerto por tamanho na
   [seção 4.2](#42-métricas-de-avaliação-fora-de-produção).
3. **Sem o modelo de linguagem não há julgamento das fontes**, e a resposta é
   "indeterminada" (RN12).
4. **Notícias verdadeiras tendem a terminar em "média"**, e afirmações
   compostas ("X, mas foi ele que criou") perdem o sentido ao serem divididas
   (P18, P19).
5. **Portais com acesso pago ou proteção contra robôs** limitam a leitura do
   conteúdo (RN07).
6. **Privacidade**: o texto enviado é comparado com bases públicas e
   consultado em serviços externos de busca. A pessoa é avisada disso no
   início da conversa e orientada a não enviar dados pessoais. O tratamento
   dos dados está no [RNF07](ENGENHARIA_DE_PRODUTO_DE_IA.md#rnf07--proteger-os-dados-e-o-sistema-contra-uso-indevido).

### 1.6 Glossário

| Termo | Significado |
|---|---|
| Propensão | Estimativa de quão provável é que o conteúdo seja desinformação: baixa, média, alta ou indeterminada. |
| Afirmação | Frase factual e verificável extraída do texto enviado. |
| Selo | Classificação dada por uma agência de checagem, como "falso" ou "enganoso". |
| Corroboração | Presença da mesma informação em veículos independentes entre si. |
| Recibo | Registro das etapas, fontes e limitações de uma resposta. |
| Catálogo | Lista de portais conhecidos, com a forma de coletar cada um. |
| Modelo de linguagem | Modelo de inteligência artificial que lê e produz texto, usado para extrair afirmações e julgar a relação entre uma notícia e uma afirmação. |
| Calibração | Ajuste para que a confiança informada pelo modelo corresponda à frequência real de acerto. |

### 1.7 Personas e análise estratégica (rascunho)

> [!IMPORTANT]
> **Rascunho, não validado.** As personas e a análise abaixo são hipóteses
> redigidas a partir do levantamento da equipe e do estado do código. Não
> vieram de entrevistas. A validação com pessoas do público-alvo é a
> pendência P11.

**Personas**

| | A pessoa que recebeu a mensagem | A jornalista de checagem | O professor que ensina a avaliar fontes |
|---|---|---|---|
| **Diz** | "Recebi isso no grupo, será que é verdade?" | "Alguém já checou isso? Quem, e quando?" | "Quero que os alunos aprendam o caminho, não só a resposta." |
| **Pensa** | "Não quero passar vergonha compartilhando mentira." | "Não confio em ferramenta que não mostra de onde tirou a resposta." | "Veredito pronto atrapalha mais do que ajuda." |
| **Faz** | Encaminha a alguém de confiança, ou procura no buscador | Busca em várias agências e portais, uma por uma | Monta atividades com notícias e boatos recentes |
| **Sente** | Insegurança; receio de ser tratada como ingênua | Pressão de tempo; desconfiança de respostas automáticas | Preocupação com a dependência de respostas prontas |
| **Objetivo** | Saber em menos de um minuto se vale desconfiar | Triagem rápida, com fontes originais e datas | Um exemplo em que cada passo da análise apareça |
| **Histórias** | HU01, HU02, HU03, HU08, HU10, HU12, HU13, HU14 | HU05, HU06, HU07 | HU09, HU11 |

**Forças, fraquezas, oportunidades e ameaças**

| | |
|---|---|
| **Forças** | Resposta nunca binária, com fontes e passo a passo (RF11, RF12). Modelo calibrado e avaliado por grupo de origem ([seção 4.2](#42-métricas-de-avaliação-fora-de-produção)). Cada etapa tem um plano de reserva (RNF08). Código e dados abertos |
| **Fraquezas** | Relevância das fontes medida por palavras em comum sem o modelo de linguagem (P02). Modelo erra em textos curtos (P09). Base de checagens é uma amostra (P08). Custo limita o uso a poucas consultas por dia (RNF06). Sem validação com o público-alvo (P11) |
| **Oportunidades** | Períodos eleitorais aumentam a procura por checagem. Parceria com agências. Uso em sala de aula. Novos canais sobre a mesma interface de programação (RNF05) |
| **Ameaças** | Portais com acesso pago ou proteção contra robôs (RN07). Mudança nos termos ou preços dos serviços externos. Desinformação em imagem, vídeo e áudio (HU04). Tentativas de manipulação (RNF07). Risco de a estimativa ser lida como veredito (RF12) |

---

## 2. Requisitos Funcionais

Cada requisito traz o enunciado, os critérios de aceitação e a situação no
código. As histórias de usuário que motivam cada requisito estão na
[seção 2.5](#25-histórias-de-usuário); as regras de negócio, na
[seção 2.6](#26-regras-de-negócio); as pendências citadas (P01, P02…), na
[seção 5.5](#55-pendências).

**Padrão de redação dos requisitos**

Todo requisito, funcional ou não funcional, segue a mesma forma:

| Parte | Regra | Exemplo (RF04) |
|---|---|---|
| Verbo | No infinitivo impessoal, no início do enunciado | **Consultar** |
| Objeto | Complemento direto do verbo, com artigo definido | **as plataformas especializadas em checagem** |
| Complemento | Finalidade, meio ou condição, quando necessário | **para verificar se a afirmação já foi checada** |

Regras de escrita:

1. **O sujeito é o sistema e fica implícito.** Não se escreve "O sistema deve
   consultar" nem se conjuga o verbo ("consulta", "consultará").
2. **Um comportamento por requisito.** Quando o enunciado precisa de um
   segundo verbo, ele descreve o modo do primeiro ("comparar as informações,
   apontando onde divergem"), e não uma segunda obrigação.
3. **Verbos verificáveis.** Preferir verbos cujo cumprimento um teste pode
   observar: aceitar, consultar, comparar, exibir, registrar, recusar. Evitar
   verbos vagos, como "suportar", "gerenciar" ou "tratar", e adjetivos sem
   medida, como "rápido" ou "amigável"; a medida vai nos critérios.
4. **Regência e crase conforme a norma.** Responder **a** algo ("respondendo a
   elas"); oferecer algo **à** pessoa; submeter algo **ao** modelo.
5. **Título e enunciado com o mesmo verbo.** O título é a forma curta do
   enunciado, sem o complemento.
6. **Nos requisitos não funcionais,** a linha "Atributo de qualidade" nomeia a
   característica medida (por exemplo, "transparência e explicabilidade"), e
   os critérios trazem a medida.
7. **Critérios de aceitação no presente do indicativo,** um fato verificável
   por item: "A entrada é classificada automaticamente em texto, título ou
   link."
8. **Termos do glossário** ([seção 1.6](#16-glossário)), sem estrangeirismo
   quando houver palavra em português.

### 2.1 Módulo de Entradas e Interface

#### RF01 — Atender a pessoa por um robô no Telegram

- **Enunciado**: Atender a pessoa por um robô no Telegram, recebendo as mensagens e respondendo a elas.
- **Critérios de aceitação**:
  1. O comando `/start` apresenta o que o robô faz, as entradas aceitas e o
     aviso de privacidade.
  2. Cada consulta recebe uma confirmação imediata e atualizações de
     progresso durante o processamento.
  3. Apenas uma instância do robô fica ativa por vez.
- **Situação**: Atendido. `factcheck_mvp/telegram_bot.py`.

#### RF02 — Aceitar o texto, o título ou o link de uma notícia

- **Enunciado**: Aceitar como entrada o texto, o título ou o link de uma notícia.
- **Critérios de aceitação**:
  1. A entrada é classificada automaticamente em texto, título ou link.
  2. O link só é acessado quando o endereço, inclusive após
     redirecionamentos, pertence a um portal do catálogo.
  3. Quando o link não pode ser lido, o sistema pede o texto da notícia. †
- **Situação**: Parcial. A leitura de links é restrita aos portais do
  catálogo, por segurança. O critério 3 depende de P01.

#### RF03 — Recusar a análise de imagens e vídeos

- **Enunciado**: Recusar a análise de imagens, vídeos, arquivos, áudios e mensagens de voz, orientando a pessoa sobre o que enviar.
- **Critérios de aceitação**:
  1. Imagens, vídeos, arquivos, áudios e mensagens de voz recebem uma
     mensagem de orientação.
  2. Nenhum conteúdo de mídia é enviado para análise.
- **Situação**: Atendido. A cobertura de áudio e voz e a orientação sobre
  busca reversa de imagem dependem de P01.

### 2.2 Módulo de Coleta e Consulta a Fontes

#### RF04 — Consultar as plataformas de checagem

- **Enunciado**: Consultar as plataformas especializadas em checagem para verificar se a afirmação já foi checada.
- **Critérios de aceitação**:
  1. Cada afirmação é buscada no índice de checagens.
  2. A busca na internet inclui uma consulta dirigida a checagens.
  3. O selo original da agência é preservado e exibido.
- **Situação**: Parcial. O índice de checagens é alimentado por RSS e ClaimReview de 8 agências (`ingestor.py`), com selos tipados por tabela (`selos.py`), e é um atalho opcional (`INDICE_CHECAGENS`). O caminho principal é a busca aberta. O histórico anterior aos feeds depende de P08.

#### RF05 — Consultar os principais veículos de comunicação

- **Enunciado**: Consultar os principais veículos de comunicação do país para verificar se a informação foi publicada. A meta é cobrir os 50 principais.
- **Critérios de aceitação**:
  1. São feitas duas consultas de busca por afirmação, com resultados
     guardados por uma hora.
  2. O conteúdo das três fontes mais próximas é lido, não apenas o título.
  3. A falta da chave de busca não interrompe a resposta: o sistema segue
     apenas com o índice de checagens e registra a limitação.
- **Situação**: Parcial. O catálogo tem 23 veículos gerais: 7 com curadoria da equipe e 16 incluídos por descoberta automática, vários com nome errado (lista na [seção 5.3](#53-canais-de-notícias); P05). Faltam 27 para a meta de 50 (P10).

#### RF06 — Comparar as informações das fontes

- **Enunciado**: Comparar as informações encontradas nas fontes, apontando onde elas concordam e onde divergem.
- **Critérios de aceitação**:
  1. Republicações do mesmo texto e veículos do mesmo grupo econômico contam
     como uma única fonte (RN03).
  2. Selos diferentes entre agências são apontados como divergência.
  3. Datas diferentes para o mesmo fato são apontadas como divergência.
- **Situação**: Atendido. A corroboração é por postura: um voto por grupo de fontes independentes (`corroboracao.py`, `decisao.py`), e datas e selos divergentes entre grupos são apontados.

### 2.3 Módulo de Análise Inteligente

#### RF08 — Submeter o texto ao modelo próprio

- **Enunciado**: Submeter o texto enviado ao modelo de aprendizado de máquina treinado pela equipe.
- **Critérios de aceitação**:
  1. O modelo analisa o texto da pessoa, uma vez por consulta.
  2. O modelo só opina em textos com 80 palavras ou mais (RN04); abaixo
     disso, a etapa é registrada como "pulada", com o motivo. †
  3. Sem o modelo configurado, o sistema usa um substituto identificado como
     tal, cujo número não é exibido como medição.
- **Situação**: Parcial. O modelo BERTimbau calibrado está na `main` (`FAKE_MODEL_PATH`); o critério 2 depende de P01. Desde o pull request nº 4, a probabilidade não entra na avaliação (RN11). A especificação do modelo está na [seção 4.1](#41-especificação-da-tarefa).

#### RF09 — Exibir a estimativa de confiança do modelo

- **Enunciado**: Exibir a probabilidade calculada pelo modelo como estimativa, e não como certeza.
- **Critérios de aceitação**:
  1. A probabilidade é calibrada antes de ser exibida.
  2. O valor aparece acompanhado do nome do modelo.
  3. A confiança de cada sinal usado na avaliação é exibida.
- **Situação**: Atendido. A probabilidade aparece como "sinal de estilo do texto (não indica veracidade)".

#### RF10 — Identificar padrões de desinformação no texto

- **Enunciado**: Identificar, com um modelo de linguagem, os padrões linguísticos e estruturais típicos de desinformação.
- **Critérios de aceitação**:
  1. Os padrões pertencem a uma lista fechada: apelo à urgência, tom
     alarmista, fonte vaga, ausência de data ou local, título sensacionalista.
  2. Cada padrão encontrado vem com o trecho que o evidencia.
  3. Sem o modelo de linguagem, regras fixas fazem a mesma análise.
  4. O conteúdo analisado é tratado como dado, nunca como instrução. †
- **Situação**: Atendido. Os padrões aparecem como sinal de estilo e não entram na avaliação (RN11).

### 2.4 Módulo de Consolidação e Apresentação

#### RF07 — Apresentar as fontes e seus endereços

- **Enunciado**: Apresentar as fontes consultadas com os respectivos endereços.
- **Critérios de aceitação**:
  1. Até três fontes aparecem lado a lado, com portal, selo, trecho citado e
     endereço.
  2. A resposta indica se o conteúdo da fonte foi lido ou se só o título foi
     considerado; a fonte lida por inteiro pesa mais (RN10).
  3. Páginas iniciais e institucionais não contam como evidência (RN05).
- **Situação**: Atendido. Sem julgamento de conteúdo, uma fonte aparece na resposta, mas não vota (RN12).

#### RF11 — Apresentar o passo a passo da análise

- **Enunciado**: Apresentar as etapas executadas na análise e a situação de cada uma.
- **Critérios de aceitação**:
  1. Cada etapa aparece com nome e situação: concluída, parcial, pulada ou
     com falha.
  2. A falha de uma etapa não interrompe a resposta; vira uma limitação
     declarada.
  3. As limitações da consulta são listadas.
- **Situação**: Atendido.

#### RF12 — Apresentar a avaliação como propensão, e não como veredito

- **Enunciado**: Apresentar a avaliação em uma escala de propensão, sem responder "fato" nem "fake".
- **Critérios de aceitação**:
  1. A avaliação usa a escala baixa, média, alta ou indeterminada (RN01).
  2. Expressões como "é falso" e "é verdade" são proibidas no texto da
     resposta, e um teste automatizado verifica isso (RN01).
  3. A conclusão é redigida por modelos de texto fixos; o modelo de linguagem
     não redige a conclusão (RN08).
- **Situação**: Atendido.

#### RF13 — Fundamentar a avaliação com evidências

- **Enunciado**: Fundamentar cada avaliação com as evidências encontradas, tanto as que aumentam quanto as que reduzem a propensão.
- **Critérios de aceitação**:
  1. A justificativa lista o que aumenta e o que reduz a propensão. Uma
     fonte que refuta a afirmação conta contra ela, qualquer que seja o selo
     (RN02).
  2. Sem evidência relevante, a avaliação é "indeterminada".
- **Situação**: Atendido. Nenhuma avaliação sai de "indeterminada" sem ao menos uma fonte com postura ou um selo aplicável (`decisao.py`).

#### RF14 — Oferecer à pessoa os elementos para uma decisão autônoma

- **Enunciado**: Oferecer à pessoa os elementos para que ela avalie a notícia por conta própria.
- **Critérios de aceitação**:
  1. Toda resposta traz três perguntas-guia: a data é atual, quem assina, e
     se a informação aparece em mais de um veículo independente.
  2. Toda resposta declara que a avaliação não é um veredito.
- **Situação**: Atendido.

#### RF15 — Disponibilizar a consulta por uma página na internet

- **Enunciado**: Disponibilizar a mesma consulta por uma página na internet, além do Telegram.
- **Critérios de aceitação**:
  1. A lógica de checagem fica separada do canal de conversa.
  2. Existe uma interface de programação com entrada e saída tipadas.
  3. Existe uma página mínima que usa a mesma lógica.
- **Situação**: Atendido. Os pontos de acesso estão na
  [seção 5.4](#54-interfaces-e-serviços-externos).

### 2.5 Histórias de usuário

As prioridades (essencial, importante, desejável, fora do escopo) são uma
**proposta**, a validar pela equipe. Os critérios de aceitação de cada
história são os do requisito indicado.

| História | Eu, como… | quero… | para… | Requisito | Prioridade |
|---|---|---|---|---|---|
| **Épico 1: consultar uma notícia** | | | | | |
| HU01 | pessoa que recebeu uma mensagem | enviar o texto ao robô | saber se devo desconfiar | RF01, RF02 | Essencial |
| HU02 | pessoa com pressa | enviar só o link | não precisar copiar o texto | RF02 | Importante |
| HU03 | pessoa que enviou uma imagem | ser avisada de que ela não é analisada | não ficar sem resposta | RF03 | Essencial |
| HU04 | pessoa que quer checar imagem ou vídeo | que a mídia seja analisada | avaliar o que recebi | Nenhum | Fora do escopo |
| **Épico 2: reunir evidências** | | | | | |
| HU05 | jornalista | saber se uma agência já checou a afirmação | não repetir trabalho | RF04 | Essencial |
| HU06 | pessoa que consulta | saber se outros veículos publicaram a informação | avaliar se ela é isolada | RF05 | Essencial |
| HU07 | pessoa que consulta | ver onde as fontes concordam e discordam | formar minha opinião | RF06 | Importante |
| **Épico 3: analisar o texto** | | | | | |
| HU08 | pessoa que consulta | uma estimativa do modelo | ter um indício a mais | RF08, RF09 | Importante |
| HU09 | pessoa que consulta | saber que marcas de boato o texto tem | aprender a reconhecê-las | RF10 | Desejável |
| **Épico 4: entender a resposta** | | | | | |
| HU10 | pessoa que consulta | os endereços das fontes | conferir por conta própria | RF07, RF13 | Essencial |
| HU11 | professor | ver cada passo da análise | usar em aula | RF11 | Importante |
| HU12 | pessoa que consulta | uma avaliação sem veredito | decidir por mim | RF12, RF14 | Essencial |
| HU13 | pessoa que consulta | avaliar se a resposta ajudou | que o sistema melhore | IND09 | Desejável |
| **Épico 5: ampliar os canais** | | | | | |
| HU14 | pessoa sem Telegram | consultar por uma página na internet | usar sem instalar nada | RF15 | Importante |
| HU15 | pessoa que usa outro aplicativo de mensagem | consultar por ele | não trocar de aplicativo | RNF05 | Desejável |

### 2.6 Regras de negócio

Regras já escritas como critério de aceitação não são repetidas aqui: a
coluna "Onde está definida" aponta para o texto.

| Regra | Enunciado | Onde está definida |
|---|---|---|
| RN01 | A resposta nunca é binária | RF12, critérios 1 e 2 |
| RN02 | A relação entre a fonte e a afirmação prevalece sobre o selo: um selo "verdadeiro" em uma peça que refuta a afirmação conta contra a afirmação | Aqui. Aplica-se a RF04 e RF13 |
| RN03 | Republicações e veículos do mesmo grupo econômico contam como uma fonte | RF06, critério 1 |
| RN04 | O modelo próprio só opina em textos com 80 palavras ou mais | RF08, critério 2 |
| RN05 | Páginas iniciais e institucionais não são evidência | RF07, critério 3 |
| RN06 | Nenhum domínio é bloqueado por lista; o filtro é a relevância para a consulta | RNF02 |
| RN07 | O sistema não contorna acesso pago nem proteção contra robôs | Aqui. Aplica-se a RF05 |
| RN08 | O modelo de linguagem não redige a conclusão | RF12, critério 3 |
| RN09 | Textos de opinião ou sátira não são classificados como fato | RNF02 |
| RN10 | Uma fonte lida por inteiro vale mais do que uma lida só pelo título | Aqui. Aplica-se a RF05 e RF07 |
| RN11 | O modelo próprio e os padrões textuais não entram na avaliação: aparecem como sinais de estilo | Aqui. Aplica-se a RF08, RF09 e RF10 |
| RN12 | Sem julgamento do conteúdo das fontes, não há avaliação: a resposta é "indeterminada" | Aqui. Aplica-se a RF13 |

---

## 3. Requisitos Não-Funcionais

### RNF01 — Explicar como cada avaliação foi obtida

**Atributo de qualidade:** transparência e explicabilidade.

| Critério | Verificação |
|---|---|
| Toda resposta lista as etapas executadas e a situação de cada uma. | Teste do fluxo completo (`tests/test_pipeline.py`). |
| Todo sinal tem origem identificada (checagem, postura das fontes, e, à parte, sinais de estilo) e peso numérico. | Estrutura `SinalAnalise` em `factcheck_mvp/schemas.py`. |
| A justificativa separa o que aumenta do que reduz a propensão. | `factcheck_mvp/agregador.py`. |
| A avaliação é calculada por uma única função, com parâmetros documentados, não por um modelo de linguagem. | `decidir` em `factcheck_mvp/decisao.py`. |
| Um componente provisório é sempre identificado como tal. | `tests/test_bot_modelo.py`. |
| As limitações da consulta são declaradas na resposta. | Campo `limitacoes` do relatório. |

**Situação**: Atendido. Os parâmetros da decisão são iniciais e estão sendo
ajustados com a avaliação em `eval/`.

### RNF02 — Apresentar a avaliação com imparcialidade

**Atributo de qualidade:** imparcialidade e neutralidade da apresentação.

| Critério | Verificação |
|---|---|
| A resposta cumpre o RF12: sem conclusão binária e com conclusão redigida por modelos de texto fixos. | Lista de expressões proibidas e teste de neutralidade. |
| A resposta não contém adjetivos de julgamento nem termos partidários. | `verificar_neutralidade` em `factcheck_mvp/agregador.py`. |
| Nenhum domínio é bloqueado por lista: o filtro é a relevância para a consulta (RN06). | `factcheck_mvp/pipeline.py`. |
| Textos de opinião ou sátira não são classificados como fato (RN09). | Regra de contenção em `factcheck_mvp/pipeline.py`. |

**Situação**: Atendido.

### RNF03 — Registrar a origem de cada fonte e de cada etapa

**Atributo de qualidade:** rastreabilidade e auditoria de fontes.

| Critério | Verificação |
|---|---|
| Toda fonte exibida tem endereço, portal, tipo e trecho citado. | Estrutura `FonteEvidencia`. |
| A resposta informa se o conteúdo da fonte foi lido. | Campo `corpo_lido`. |
| O selo original da agência é preservado. | Campo `selo_original`. |
| Cada etapa registra os endereços que usou. | Estrutura `EtapaRecibo`. |
| A inclusão de um portal no catálogo passa por aprovação. | Parcial: o portal descoberto não vale na consulta em curso, mas é gravado no catálogo (P05). |
| As execuções ficam registradas para auditoria posterior. | Telemetria: um registro por execução em `runs/`, com etapas, fontes e chamadas (`telemetria.py`). |

**Situação**: Parcial.

### RNF04 — Redigir as respostas em linguagem simples

**Atributo de qualidade:** usabilidade e acessibilidade.

| Critério | Verificação |
|---|---|
| A primeira linha da resposta traz a avaliação, com um símbolo de cor. | `gerar_header` em `factcheck_mvp/agregador.py`. |
| A segunda linha resume o motivo em uma frase. | Campo `why_1linha`. |
| A resposta cabe em uma mensagem do Telegram e nunca é cortada no meio de um endereço. | `formatar` em `factcheck_mvp/telegram_bot.py`. |
| Mensagens de erro não expõem detalhes técnicos. | Tratamento de exceções no robô e na interface de programação. |
| A linguagem foi validada com pessoas leigas. | Pendente (P11). |

**Situação**: Parcial. A resposta ainda usa termos técnicos como "corpo lido"
e nomes internos dos sinais.

### RNF05 — Permitir novos canais sem alterar a lógica de checagem

**Atributo de qualidade:** extensibilidade e arquitetura modular.

| Critério | Verificação |
|---|---|
| A lógica de checagem não depende do canal de conversa. | `factcheck_mvp/pipeline.py` não importa o Telegram. |
| A entrada e a saída são tipadas e compartilhadas por todos os canais. | `factcheck_mvp/schemas.py`. |
| O modelo próprio pode ser trocado sem alterar o fluxo. | Interface `DetectorFake` em `factcheck_mvp/modelo_fake.py`. |
| A busca no índice pode ser trocada sem alterar o fluxo. | Método `Indice.buscar`. |
| Um novo canal, como o WhatsApp, é um adaptador sobre a mesma interface de programação. | Pendente (P17). |

**Situação**: Atendido para Telegram e página na internet.

### RNF06 a RNF09 — Requisitos próprios de inteligência artificial

Desempenho, segurança, confiabilidade e ética têm critérios específicos em um
produto de inteligência artificial. Eles estão definidos, com valores medidos,
no documento de [Engenharia de Produto de Inteligência Artificial](ENGENHARIA_DE_PRODUTO_DE_IA.md):

| Requisito | Trata de |
|---|---|
| [RNF06 — Responder dentro dos limites de tempo e de custo](ENGENHARIA_DE_PRODUTO_DE_IA.md#rnf06--responder-dentro-dos-limites-de-tempo-e-de-custo) | Latência, vazão, capacidade diária, controle de custo e reaproveitamento de respostas |
| [RNF07 — Proteger os dados e o sistema contra uso indevido](ENGENHARIA_DE_PRODUTO_DE_IA.md#rnf07--proteger-os-dados-e-o-sistema-contra-uso-indevido) | Dados em trânsito e em repouso, acesso a endereços, injeção de instruções, manipulação |
| [RNF08 — Manter a resposta disponível diante de falhas](ENGENHARIA_DE_PRODUTO_DE_IA.md#rnf08--manter-a-resposta-disponível-diante-de-falhas) | Falhas de etapa, estabilidade da resposta, retreino, monitoramento |
| [RNF09 — Medir e mitigar os vieses do modelo e dos dados](ENGENHARIA_DE_PRODUTO_DE_IA.md#rnf09--medir-e-mitigar-os-vieses-do-modelo-e-dos-dados) | Vieses do conjunto de dados e do modelo, e salvaguardas do produto |

### Restrições

| Restrição | Origem |
|---|---|
| Mensagens de até 4.096 caracteres no Telegram | Plataforma |
| Limites diários de buscas e de chamadas ao modelo de linguagem (valores no RNF06) | Custo: serviços em plano gratuito ou de baixo custo |
| Treino sem placa de vídeo própria | Infraestrutura da equipe |
| Arquivos grandes fora do controle de versão | Limite de armazenamento do repositório |
| Só texto em português | Escopo inicial e dados disponíveis |
| Conteúdo de terceiros usado conforme os termos de cada fonte | Legal |

---

## 4. Requisitos do Modelo de Aprendizado de Máquina e dos Dados

### 4.1 Especificação da tarefa

> [!NOTE]
> **Fonte única dos números do modelo e dos dados.** Os valores desta seção
> não se repetem em outro lugar: os demais pontos, neste documento e no de
> produto, apontam para cá.

| Item | Especificação |
|---|---|
| Tarefa | Classificação binária de texto em português. |
| Entrada | Texto da notícia, truncado em 192 unidades de texto do modelo. |
| Saída | Probabilidade calibrada, entre 0 e 1, de o texto ser desinformação. |
| Modelo | BERTimbau base (`neuralmind/bert-base-portuguese-cased`), ajustado no conjunto FakenewsBR versão 6, execução R0. |
| Calibração | Método de Platt, ajustado em um conjunto separado do treino e do teste. |
| Condição de uso | Textos com 80 palavras ou mais †. Na `main`, o modelo roda em qualquer texto. |
| Papel na avaliação | Nenhum: fica fora da avaliação e aparece como sinal de estilo (RN11). Nas sondas da equipe, o modelo não distingue uma afirmação da sua negação e muda de resultado só pela caixa das letras (`docs/review/RELATORIO.md`, item D4). |

### 4.2 Métricas de avaliação fora de produção

Conjunto de teste com 13.661 textos, separado por agrupamento de textos
semelhantes para que nenhum quase duplicado apareça no treino e no teste.

| Métrica | Valor medido |
|---|---:|
| Acurácia | 0,816 |
| Medida F1 macro | 0,778 |
| Medida F1 da classe desinformação | 0,870 |
| Precisão na classe desinformação | 0,903 |
| Revocação na classe desinformação | 0,839 |
| Precisão na classe verdadeira | 0,630 |
| Revocação na classe verdadeira | 0,753 |
| Área sob a curva característica de operação do receptor | 0,890 |
| Área sob a curva de precisão e revocação | 0,956 |
| Erro de calibração esperado | 0,019 |
| Acurácia no pior grupo de fontes | 0,575 |

**Modelo de referência a superar**, no mesmo conjunto de teste. O modelo do
produto precisa ficar acima dele no pior grupo, não só na média:

| Modelo de referência | Acurácia | Medida F1 macro | Pior grupo |
|---|---:|---:|---:|
| Frequência de termos com regressão logística | 0,803 | 0,740 | 0,507 |

**Acerto por tamanho do texto**:

| Palavras | Acerto nas verdadeiras | Acerto nas falsas |
|---|---:|---:|
| 1 a 15 | 0,738 | 0,839 |
| 16 a 25 | 0,735 | 0,780 |
| 26 a 40 | 0,647 | 0,872 |
| 41 a 80 | 0,720 | 0,919 |
| 81 a 200 | 0,892 | 0,951 |
| Mais de 200 | 0,922 | 0,952 |

**Leitura das métricas.** Quando o modelo aponta desinformação, acerta 90% das
vezes; quando aponta "verdadeira", acerta 63%. Ele deixa passar 16% das
desinformações e aponta como suspeitas 25% das notícias verdadeiras.

A métrica principal do projeto é a **acurácia no pior grupo**, não a média.
O conjunto de dados tem grupos de fontes em que todos os textos têm o mesmo
rótulo; um modelo pode aprender a reconhecer a origem do texto em vez do
conteúdo. A medição por grupo expõe esse atalho. Os recortes por canal e por
variedade do português, com a mitigação de cada viés, estão no
[RNF09](ENGENHARIA_DE_PRODUTO_DE_IA.md#rnf09--medir-e-mitigar-os-vieses-do-modelo-e-dos-dados).

**Requisitos de qualidade propostos para a próxima versão do modelo**:

1. Acurácia no pior grupo acima de 0,70.
2. Acerto nas notícias verdadeiras com menos de 80 palavras acima de 0,85.
3. Erro de calibração esperado abaixo de 0,05.
4. Revocação e precisão por classe relatadas em toda avaliação, como na tabela
   acima.

### 4.3 Métricas em produção

| Métrica | Fonte | Situação |
|---|---|---|
| Acerto, erro grave e cobertura do sistema completo | `eval/run.py`: 70 casos de desenvolvimento e um conjunto reservado, com gravação e reprodução das chamadas externas | Disponível; valores na [seção 1.4](#14-indicadores-de-desempenho) |
| Registro de cada execução | Telemetria em `runs/<execução>/` | Disponível; sem acompanhamento ao longo do tempo (P07) |
| Utilidade percebida | Votos dos botões de avaliação † | Não está na `main` |
| Distribuição das probabilidades em uso real | Telemetria | Pendente (P07) |

Os sinais de desvio dos dados e de conceito, e o que acompanhar ao longo do
tempo, estão em [Monitoramento contínuo](ENGENHARIA_DE_PRODUTO_DE_IA.md#10-monitoramento-contínuo).

### 4.4 Fontes de dados e coleta contínua

| Fonte | Conteúdo | Uso |
|---|---|---|
| FakenewsBR versão 6 | 297.672 textos. Conjunto de treino verificado: 91.080 textos, sendo 66.772 de desinformação e 24.308 verdadeiros, em 36 grupos de origem. | Treino e teste do modelo. |
| Rótulos em camadas | Cada texto registra a origem do rótulo: agência de checagem, correspondência com checagem, procedência da fonte ou modelo de linguagem local. | Separar os rótulos confiáveis dos rótulos por procedência. |
| Coletor de portais | `br_news_crawler.py`, com a biblioteca Crawl4AI, percorre os portais do catálogo e extrai título, texto, data, autor e selo. | Alimentar o índice de checagens e de notícias. |
| Busca de notícias | Serviço SerpAPI, mecanismo Google Notícias. | Cobertura do que foi publicado recentemente. |
| Avaliações das pessoas | Votos registrados com o texto consultado e a propensão informada, sem identificação. † | Calibrar os pesos do agregador e retreinar o modelo. |

**Requisitos de dados**:

1. Nenhum texto quase duplicado pode aparecer ao mesmo tempo no treino e no
   teste. Verificação atual: zero pares.
2. Toda comparação entre versões do modelo usa o mesmo conjunto de teste.
3. O conteúdo de terceiros segue os termos de cada fonte, registrados em
   `docs/SOURCES_AND_LICENSES.md` no repositório FakenewsBR.
4. Arquivos grandes de dados e os pesos do modelo ficam fora do controle de
   versão e são publicados nas versões do repositório FakenewsBR.

### 4.5 Estratégia de transparência e confiança

1. **A probabilidade é uma estimativa.** A resposta apresenta o valor do
   modelo como um dos indícios, com o nome do modelo, e nunca como conclusão.
2. **A probabilidade é calibrada.** Um valor de 0,80 deve corresponder a
   acerto em cerca de 80% dos casos. O erro medido está na
   [seção 4.2](#42-métricas-de-avaliação-fora-de-produção).
3. **O modelo se abstém quando não é confiável.** Em textos curtos ele não
   opina, e a resposta explica o motivo (RN04). †
4. **O modelo não decide.** A probabilidade aparece como sinal de estilo e
   não entra na avaliação (RN11).
5. **Um componente provisório nunca se passa por medição.** Sem o modelo
   configurado, a resposta informa que o modelo está em treino e não mostra
   número.

---

## 5. Matriz de Rastreabilidade e Mapeamento de Integrações Externas

### 5.1 Matriz de rastreabilidade dos requisitos

Cada relação é registrada em um só lugar:

| Relação | Onde está |
|---|---|
| Objetivo → indicador | [Seção 1.2](#12-objetivos-sociais-e-de-negócio) |
| História de usuário → requisito | [Seção 2.5](#25-histórias-de-usuário) |
| Regra de negócio → requisito | [Seção 2.6](#26-regras-de-negócio) |
| Requisito → situação | No próprio requisito, nas [seções 2](#2-requisitos-funcionais) e 3 |
| Requisito → arquivo → teste | Tabela abaixo |
| Pendência → requisitos afetados | [Seção 5.5](#55-pendências) |

| Requisito | Arquivo principal | Teste automatizado |
|---|---|---|
| RF01 | `factcheck_mvp/telegram_bot.py` | `tests/test_guard_unica.py`, `tests/test_bot_modelo.py` |
| RF02 | `factcheck_mvp/telegram_bot.py` | `tests/test_schemas.py`, `tests/test_loop1.py` |
| RF03 | `factcheck_mvp/telegram_bot.py` | Sem teste automatizado |
| RF04 | `factcheck_mvp/indice.py`, `factcheck_mvp/ingestor.py`, `factcheck_mvp/selos.py`, `factcheck_mvp/jsonld.py` | `tests/test_indice.py`, `tests/test_indice_checagens.py`, `tests/test_ingestor.py`, `tests/test_selos.py`, `tests/test_jsonld.py` |
| RF05 | `factcheck_mvp/serpapi_layer.py`, `factcheck_mvp/agente.py`, `factcheck_mvp/aprofundar.py`, `factcheck_mvp/extracao.py` | `tests/test_serpapi_layer.py`, `tests/test_agente.py`, `tests/test_extracao.py`, `tests/test_loop1.py` |
| RF06 | `factcheck_mvp/corroboracao.py`, `factcheck_mvp/decisao.py` | `tests/test_corroboracao.py`, `tests/test_decisao.py` |
| RF07 | `factcheck_mvp/telegram_bot.py`, `factcheck_mvp/pipeline.py` | `tests/test_review_loop4.py`, `tests/test_pipeline.py` |
| RF08 | `factcheck_mvp/modelo_fake.py` | `tests/test_bot_modelo.py`, `tests/test_modelo_texto_longo.py` † |
| RF09 | `factcheck_mvp/modelo_fake.py`, `factcheck_mvp/pipeline.py` | `tests/test_bot_modelo.py` |
| RF10 | `factcheck_mvp/padroes_llm.py` | `tests/test_review_loop4.py`, `tests/test_padroes_local.py` †, `tests/test_blindagem.py` † |
| RF11 | `factcheck_mvp/pipeline.py`, `factcheck_mvp/telemetria.py` | `tests/test_pipeline.py`, `tests/test_regressao_36.py`, `tests/test_telemetria.py` |
| RF12 | `factcheck_mvp/agregador.py`, `factcheck_mvp/decisao.py` | `tests/test_schemas.py`, `tests/test_decisao.py` |
| RF13 | `factcheck_mvp/decisao.py`, `factcheck_mvp/juiz_llm.py` | `tests/test_decisao.py`, `tests/test_juiz_llm.py`, `tests/test_review.py` |
| RF14 | `factcheck_mvp/agregador.py` | `tests/test_pipeline.py` |
| RF15 | `factcheck_mvp/api.py` | `tests/test_schemas.py` (contrato de entrada e saída); sem teste dos pontos de acesso |
| RNF01 | `factcheck_mvp/decisao.py`, `factcheck_mvp/schemas.py` | `tests/test_decisao.py`, `tests/test_schemas.py` |
| RNF02 | `factcheck_mvp/agregador.py` | `tests/test_schemas.py` |
| RNF03 | `factcheck_mvp/schemas.py`, `factcheck_mvp/catalogo.py`, `factcheck_mvp/telemetria.py` | `tests/test_descoberta_site.py`, `tests/test_catalogo_roteamento.py`, `tests/test_telemetria.py` |
| RNF04 | `factcheck_mvp/telegram_bot.py` | Sem teste automatizado |
| RNF05 | `factcheck_mvp/api.py`, `factcheck_mvp/pipeline.py`, `factcheck_mvp/llm.py` | `tests/test_pipeline.py`, `tests/test_llm.py` |
| RNF06 | `factcheck_mvp/config.py`, `factcheck_mvp/api.py`, `factcheck_mvp/replay.py` | `tests/test_serpapi_layer.py`, `tests/test_replay.py`, `tests/test_cache_relatorio.py` † |
| RNF07 | `factcheck_mvp/aprofundar.py`, `factcheck_mvp/telemetria.py`, `factcheck_mvp/juiz_llm.py`, `factcheck_mvp/blindagem.py` † | `tests/test_loop1.py`, `tests/test_telemetria.py`, `tests/test_juiz_llm.py`, `tests/test_blindagem.py` †, `tests/test_feedback.py` † |
| RNF08 | `factcheck_mvp/pipeline.py`, `factcheck_mvp/decisao.py` | `tests/test_pipeline.py`, `tests/test_review.py` |
| RNF09 | Repositório FakenewsBR: `models/v6/`, `scripts/analise_por_tamanho.py`; `eval/` | Avaliação estatística (`eval/run.py`), sem teste automatizado |

A `main` tem 319 testes automatizados, todos passando em 30 de setembro de
2026. Os arquivos marcados com † estão só na branch `melhorias/revisao-arquitetura`.

### 5.2 Canais de checagem

Treze plataformas no catálogo. "Extração por seletores" é a leitura da página
por regras fixas de estrutura; "classificador" é o modelo que reconhece o selo
no texto.

| Plataforma | Endereço | Estratégia de integração | Observações |
|---|---|---|---|
| Fato ou Fake (G1) | g1.globo.com/fato-ou-fake | Modelo de linguagem local para o selo; extração por seletores para o texto. Tem alimentador de notícias e mapa do site. | O selo costuma vir no título. |
| Aos Fatos | aosfatos.org | Modelo de linguagem local para o selo; extração por seletores para o texto. Tem alimentador de notícias e mapa do site. | Selos próprios: falso, enganoso, verdadeiro. |
| Agência Lupa | lupa.uol.com.br | Modelo de linguagem local para o selo; extração por seletores para o texto. Tem mapa do site. | Taxonomia própria de selos, convertida para a escala comum. |
| Fato ou Boato (Tribunal Superior Eleitoral) | justicaeleitoral.jus.br/fato-ou-boato | Modelo de linguagem local e classificador para o selo; extração por seletores. | Proteção contra robôs: exige navegador real. |
| Projeto Comprova | projetocomprova.com.br | Modelo de linguagem local e classificador para o selo; extração por seletores. Tem mapa do site. | Coalizão de veículos, com selo próprio. |
| Saúde com Ciência (Ministério da Saúde) | gov.br/saude | Extração por seletores; classificador para o selo. | Sucessor do programa Saúde sem Fake News. |
| Rede de Checagem em Saúde (Fiocruz, Canal Saúde) | canalsaude.fiocruz.br | Extração por seletores; classificador para o selo. | Em setembro de 2026 a página inicial exibia aviso de período eleitoral. |
| E-Farsas | e-farsas.com | Extração por seletores; classificador para o selo. | O selo costuma estar no título. |
| Boatos.org | boatos.org | Extração por seletores; classificador para o selo. | Foco em boatos de aplicativos de mensagem. |
| UOL Confere | noticias.uol.com.br/confere | Modelo de linguagem local e classificador para o selo; extração por seletores. | Proteção contra robôs: exige navegador real. |
| Estadão Verifica | estadao.com.br/estadao-verifica | Modelo de linguagem local e classificador para o selo; extração por seletores. | Acesso pago parcial: coleta só o conteúdo público. |
| Agência Tatu | agenciatatu.com.br | Classificador para separar checagem de reportagem de dados; extração por seletores. | Agência de jornalismo de dados, não só de checagem. |
| ValorInveste (cobertura de golpes) | valorinveste.globo.com | Busca por termos de golpe e fraude; extração por seletores. | Sem seção dedicada de checagem. |

### 5.3 Canais de notícias

**Veículos com curadoria da equipe** (7):

| Veículo | Endereço | Estratégia de integração | Observações |
|---|---|---|---|
| G1 | g1.globo.com | Extração por seletores; modelo de linguagem local como reserva. Tem alimentador de notícias e mapa do site. | Proteção moderada contra robôs. |
| CNN Brasil | cnnbrasil.com.br | Extração por seletores; modelo de linguagem local para autor e data. Tem mapa do site. | Parte do conteúdo carrega depois da página. |
| UOL Notícias | noticias.uol.com.br | Metadados e extração por seletores. Tem mapa do site. | Acesso pago em parte do conteúdo. |
| Folha de S.Paulo | folha.uol.com.br | Página inicial e listagens por seletores; texto completo só quando público. Tem mapa do site. | Acesso pago: não é contornado. |
| Estadão | estadao.com.br | Extração por seletores e metadados. Tem mapa do site. | Acesso pago parcial. |
| BBC News Brasil | bbc.com/portuguese | Extração por seletores. Tem mapa do site. | Estrutura estável, sem acesso pago. |
| Poder360 | poder360.com.br | Alimentador de notícias e extração por seletores. Tem mapa do site. | Estrutura estável. |

**Veículos incluídos por descoberta automática, com curadoria pendente** (16):
Brasil de Fato, Exame, Autoesporte, O Globo, Olhar Digital, NC News, Agora
Litoral, National Geographic Brasil, Record, SBT News, Jornal Opção, Banda B,
Folhamax, Rádio Difusora e dois sites de saúde. Nos quatro últimos, o nome
gravado é o título de uma matéria, e não o do veículo (por exemplo, "Ações
importantes que ajudam a combater a dengue"), o que mostra por que a inclusão
precisa de curadoria (P05).

**Lacuna em relação ao RF05**: 23 veículos catalogados, de uma meta de 50.
Entre os citados no levantamento, a Forbes ainda não está no catálogo.

### 5.4 Interfaces e serviços externos

**Interfaces oferecidas pelo sistema**

| Interface | Para quem | Descrição |
|---|---|---|
| Robô no Telegram | Pessoas | Conversa: texto, título ou link (RF01, RF02) |
| Página na internet, `GET /` | Pessoas | Formulário de consulta e resposta (RF15) |
| `POST /checar` | Sistemas | Recebe o tipo e o conteúdo; devolve o relatório tipado (RF15) |
| `GET /saude`, `GET /portais` | Sistemas | Estado do serviço e catálogo de portais |

**Serviços de que o sistema depende**

| Serviço | Uso no sistema | Configuração | Comportamento sem o serviço |
|---|---|---|---|
| Telegram | Canal de conversa. | `TELEGRAM_TOKEN` | A interface de programação e a página funcionam sem ele. |
| SerpAPI | Busca de notícias e de checagens recentes (`SERP_ENGINE`, padrão Google). | `SERPAPI_KEY`; limite diário em `SERPAPI_DAILY_CAP`. | A etapa de descoberta é pulada; o sistema usa só o índice de checagens, se ligado. |
| OpenRouter | Modelo de linguagem para extrair afirmações e consultas, julgar a postura de cada fonte e identificar padrões. | `OPENROUTER_API_KEY`; `OPENROUTER_MODEL_JUIZ` permite um modelo próprio para o julgamento; limite diário em `LLM_DAILY_CAP`. | Sem julgamento das fontes, a resposta é "indeterminada" (RN12). |
| Modelo de linguagem local | Reserva para todas as finalidades do modelo de linguagem. | `UNSLOTH_BASE_URL`, `UNSLOTH_MODEL_NAME`. | Regras fixas assumem nas afirmações; sem julgamento, RN12. |
| Feeds RSS e ClaimReview das agências | Índice de checagens com selo tipado. | `INDICE_CHECAGENS`; lista de feeds em `ingestor.py` | O índice não é atualizado; a busca aberta continua. |
| Trafilatura | Extração do corpo das páginas, na cascata JSON-LD, trafilatura, seletor e regex. | `requirements-mvp.txt` | Os níveis seguintes da cascata assumem. |
| ReaderLM-v2 (opcional) | Último nível da cascata de extração. Licença não comercial. | `EXTRACAO_READERLM`, `READERLM_BASE_URL` | Desligado por padrão. |
| Crawl4AI | Coletor antigo dos portais (`br_news_crawler.py`), substituído pelos feeds como fonte do índice. | `requirements.txt` | Nenhum efeito na consulta. |
| Modelo BERTimbau R0 | Sinal de estilo exibido na resposta, fora da avaliação. | `FAKE_MODEL_PATH` | Substituto identificado como provisório. |

Nenhuma chave de serviço tem valor padrão utilizável. Todas são lidas do
ambiente e ficam fora do controle de versão.

### 5.5 Pendências

Lista única das pendências do produto. Impacto e esforço são uma **proposta**
de priorização. Os diagnósticos técnicos estão no relatório de revisão da
equipe (`docs/review/RELATORIO.md`) e no registro das iterações da avaliação
(`eval/ITERACOES.md`).

| Pendência | O que falta | Afeta | Impacto | Esforço | Situação |
|---|---|---|---|---|---|
| P01 | Integrar da branch `melhorias/revisao-arquitetura` o que a `main` ainda não tem: proteção contra injeção de instruções, reaproveitamento de respostas, botões de avaliação, orientação para áudio, pedido de texto quando o link não abre e piso de 80 palavras | RF02, RF03, RF08, RNF06, RNF07 | Alto | Médio | Aberta |
| P02 | Ligar o modelo de linguagem em produção | RF04, RF06, RF07, RF13 | Alto | Baixo | Aberta |
| P03 | Estabilizar a avaliação: evidência mínima, faixas simétricas, estilo fora da nota, sem fontes "relevantes" inventadas | RF07, RF10, RF13, RNF08, IND07 | Alto | Baixo | Resolvida no pull request nº 4 |
| P04 | Medir o sistema completo com todos os módulos ligados | IND03 a IND06 | Alto | Baixo | Parcial: avaliação de 70 casos existe; falta uma rodada completa com a cota de busca garantida |
| P05 | Aprovar portais novos antes de entrarem no catálogo; curadoria dos votos de avaliação | RNF03, RNF07 | Médio | Baixo | Parcial: o portal novo não vale na consulta, mas ainda é gravado |
| P06 | Corrigir a comparação de datas entre fontes | RF06 | Médio | Baixo | Resolvida no pull request nº 4 |
| P07 | Acompanhar ao longo do tempo os sinais de desvio e de desempenho | RNF08, IND09 a IND13 | Alto | Médio | Parcial: registro por execução existe; falta a agregação |
| P08 | Formar o histórico completo do índice de checagens | RF04, RF05 | Alto | Alto | Parcial: os feeds trazem o passado recente; o histórico exige mapa do site |
| P09 | Retreinar o modelo para textos curtos | RF08, RNF09, IND08 | Médio | Alto | Aberta. Impacto reduzido desde que o modelo saiu da avaliação |
| P10 | Ampliar o catálogo para 50 veículos | RF05 | Médio | Alto | Aberta |
| P11 | Validar a linguagem e as personas com pessoas do público-alvo | RNF04, [seção 1.7](#17-personas-e-análise-estratégica-rascunho) | Alto | Médio | Aberta |
| P12 | Executar os testes automaticamente a cada envio ao repositório | RNF08 | Médio | Baixo | Aberta |
| P13 | Definir a frequência de retreino e adotar o modo sombra | RNF08 | Médio | Médio | Aberta |
| P14 | Medir o viés por tema e por posição política | RNF09 | Médio | Médio | Aberta |
| P15 | Escrever a política de retenção de dados, incluindo os registros de execução | RNF07 | Médio | Baixo | Aberta |
| P16 | Imagem de contêiner e versões de bibliotecas fixadas | Reprodutibilidade | Baixo | Baixo | Aberta |
| P17 | Canal em outro aplicativo de mensagem | RNF05, HU15 | Médio | Alto | Aberta |
| P18 | Tratar afirmações compostas sem perder o conector ("X, mas foi ele que criou") | RF13, IND05 | Médio | Médio | Aberta |
| P19 | Levar notícias verdadeiras e refutações de agência a "baixa" e "alta", e não a "média": veredito pelo título das fontes de checagem e peso maior para agência curada | RF04, RF13, IND04 | Alto | Médio | Aberta, com hipóteses registradas em `eval/ITERACOES.md` |

**Por onde começar:** P02 e P04 têm esforço baixo e destravam a medição do
sistema. P19 ataca a maior fonte de erro registrada na avaliação.

### 5.6 Gestão dos requisitos

| Momento | O que foi feito | Situação |
|---|---|---|
| Elicitação | Sessão de ideias da equipe, que gerou RF01 a RF15 e RNF01 a RNF05; análise de plataformas de checagem e de portais | Atendido. Entrevistas com o público-alvo: P11 |
| Análise | Protótipo funcional do robô e da interface de programação; revisão de código da equipe (`docs/review/`) | Atendido |
| Especificação | Este documento, com critérios de aceitação, histórias e regras, no padrão de redação da [seção 2](#2-requisitos-funcionais); RNF06 a RNF09 no documento de produto | Atendido |
| Validação | Testes automatizados por requisito ([seção 5.1](#51-matriz-de-rastreabilidade-dos-requisitos)); avaliação com casos rotulados e reprodução determinística (`eval/`) | Parcial: P04 e P11 |
| Gerenciamento | Requisitos versionados no repositório; mudanças entram por pedido de integração revisado; cada relação registrada em um só lugar | Atendido |
