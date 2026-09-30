# Crosscheck BR — Engenharia de Produto de Inteligência Artificial

| | |
|---|---|
| **Produto** | Crosscheck BR — checagem cruzada de fatos em larga escala |
| **Documento** | O que é próprio de um produto de inteligência artificial: o canvas do modelo, o ciclo de vida, os requisitos RNF06 a RNF09, a validação, a operação e o monitoramento |
| **Data deste documento** | 30 de setembro de 2026 |
| **Equipe** | Thiago Correia Gonzaga, Gabriella Oliveira de Souza Dias, Maria Eduarda Denis Duarte Marques, Guilherme Silva Dutra, Eliane Orlandin do Carmo, Daltro Oliveira Vinuto |

Um produto de inteligência artificial difere de um software tradicional em
quatro pontos: depende de dados que mudam, tem comportamento probabilístico,
precisa de operação contínua e só funciona como sistema integrado. Este
documento registra como o Crosscheck BR trata cada um deles.

**Como este documento se liga ao de requisitos.** Cada dado fica em um único
lugar. Os números do modelo e dos dados, os indicadores, as regras de negócio
e as pendências estão na
[Engenharia de Requisitos](ENGENHARIA_DE_REQUISITOS.md); aqui eles são
citados pelo identificador (RF, RNF, RN, HU, IND, P) ou por link, sem
repetição. Onde o texto diz **proposta**, o valor precisa ser validado pela
equipe.

---

## 1. Machine Learning Canvas

O canvas resume, em uma página, como o modelo gera valor: da coleta de dados
ao uso da predição. A coluna "Detalhes" aponta para onde cada bloco é
desenvolvido.

> [!IMPORTANT]
> **A predição é um indício, não uma decisão.** Desde o pull request nº 4, o
> modelo não entra na avaliação: a propensão vem só das checagens de agências e
> da postura das fontes, e a predição aparece na resposta como sinal de estilo,
> ao lado do nível (RN11).

| Bloco | Resumo | Detalhes |
|---|---|---|
| **Proposta de valor** | Estimar a propensão de um texto ser desinformação e mostrar as evidências, para a pessoa decidir se compartilha. Para quem recebe notícias em aplicativos de mensagem, para estudantes e professores, para jornalistas e checadores | [Requisitos, seção 1](ENGENHARIA_DE_REQUISITOS.md#1-visão-geral-e-proposta-de-valor) |
| **Tarefa de aprendizado de máquina** | Classificação binária de texto em português. Entrada: o texto da notícia. Saída: probabilidade calibrada de ser desinformação | [Requisitos, seção 4.1](ENGENHARIA_DE_REQUISITOS.md#41-especificação-da-tarefa) |
| **Decisões** | A predição é exibida como sinal de estilo, com a ressalva de que não é veredito, e não altera a propensão (RN11). Não aprova, não bloqueia e não rotula nada automaticamente | [Seção 3](#3-decisões-e-jornadas) |
| **Predições** | A cada consulta, em tempo real, uma vez por texto. O modelo se abstém em textos curtos (RN04) | [RNF06](#rnf06--responder-dentro-dos-limites-de-tempo-e-de-custo) |
| **Fontes de dados** | Conjunto FakenewsBR, com rótulos em camadas; agências de checagem; portais de notícias; busca de notícias recentes | [Requisitos, seção 4.4](ENGENHARIA_DE_REQUISITOS.md#44-fontes-de-dados-e-coleta-contínua) |
| **Coleta de dados** | O coletor percorre os portais do catálogo; os votos de avaliação registram o texto e a propensão informada. Os votos precisam de curadoria antes de virar rótulo | [Requisitos, seção 4.4](ENGENHARIA_DE_REQUISITOS.md#44-fontes-de-dados-e-coleta-contínua); pendências P05 e P08 |
| **Variáveis de entrada** | O próprio texto; não há variáveis construídas à mão. Em estudo: ocultar nomes de pessoas e lugares no treino, para o modelo não decorar quem aparece em boatos. Tamanho do texto, grupo de origem e canal servem para medir vieses, não para treinar | [RNF09](#rnf09--medir-e-mitigar-os-vieses-do-modelo-e-dos-dados) |
| **Construção do modelo** | Ajuste das seis últimas camadas do BERTimbau, duas passagens pelos dados, com pesos por grupo de origem e calibração em conjunto separado. Um treino leva cerca de 7 horas sem placa de vídeo, ou de 30 a 55 minutos com uma, em caderno na nuvem. A frequência de retreino ainda não está definida | [Seção 6](#6-reprodutibilidade); pendência P13 |
| **Avaliação antes da implantação** | Métrica principal: acurácia no pior grupo de origem. Recortes obrigatórios por grupo, canal, tamanho do texto e variedade do português. O modelo precisa superar o de referência | [Requisitos, seção 4.2](ENGENHARIA_DE_REQUISITOS.md#42-métricas-de-avaliação-fora-de-produção) |
| **Avaliação em produção e monitoramento** | Sistema completo medido em casos rotulados (`eval/`); registro de cada execução em `runs/`; sinais de desvio | [Requisitos, seção 4.3](ENGENHARIA_DE_REQUISITOS.md#43-métricas-em-produção); [seção 10](#10-monitoramento-contínuo) |

---

## 2. Ciclo de vida do produto

| Fase | O que existe hoje | Onde está | Situação |
|---|---|---|---|
| Planejamento | Requisitos levantados em sessão de ideias da equipe; roteiro em quatro fases | [Engenharia de Requisitos](ENGENHARIA_DE_REQUISITOS.md), `ROADMAP.md` | Atendido |
| Preparação de dados | Coleta em agências e portais, remoção de duplicados, rótulos em camadas, separação de treino e teste por agrupamento de textos semelhantes | Repositório FakenewsBR: `investigation/expansion/`, `models/v6/prepare_v6.py` | Atendido |
| Engenharia do modelo | Ajuste do BERTimbau com pesos por grupo de origem; 23 experimentos e 4 controles; revisão crítica do desenho experimental | FakenewsBR: `models/v6/train_bertimbau_v6.py`, `models/v6/autoresearch/` | Atendido |
| Avaliação do modelo | Métricas por grupo de origem, por canal e por tamanho de texto; comparação com o modelo de referência | FakenewsBR: `models/v6/compare/`, `scripts/analise_por_tamanho.py` | Atendido |
| Implantação | Pesos publicados como versão no repositório; o robô carrega o modelo por configuração | `factcheck_mvp/modelo_fake.py` | Parcial: manual ([seção 9](#9-operação-e-implantação)) |
| Operação | Limites de custo e de requisições, limites de tempo por etapa, instância única do robô | `factcheck_mvp/config.py`, `factcheck_mvp/api.py` | Parcial: sem automação (P12) |
| Monitoramento e avaliação | Registro de cada execução (etapas, chamadas, fontes, decisão); reprodução determinística; avaliação com casos rotulados | `factcheck_mvp/telemetria.py`, `factcheck_mvp/replay.py`, `eval/` | Parcial: sem acompanhamento agregado (P07) |

---

## 3. Decisões e jornadas

Os objetivos do produto e os indicadores que os medem estão em
[Requisitos, seções 1.2 e 1.4](ENGENHARIA_DE_REQUISITOS.md#12-objetivos-sociais-e-de-negócio).

### 3.1 Decisões que o sistema apoia

O sistema **apoia** decisões; não automatiza nenhuma (RF12, RF14).

| Decisão | Quem decide | O que o sistema entrega | Requisitos |
|---|---|---|---|
| Compartilhar ou não uma mensagem | A pessoa que recebeu | Propensão, fontes e três perguntas para conferir | RF12, RF13, RF14 |
| Em que fonte confiar | A pessoa | Fontes lado a lado, com o selo de cada agência | RF06, RF07 |
| Vale uma checagem aprofundada? | Jornalista ou checador | O que já foi checado e por quem | RF04 |
| Um portal novo entra no catálogo? | Equipe do projeto | Proposta de inclusão para curadoria | RNF03, P05 |

### 3.2 Jornadas de uso

| Jornada | Passos | Histórias |
|---|---|---|
| Mensagem recebida em aplicativo | A pessoa copia o texto, envia ao robô, acompanha o progresso, lê a resposta e, se quiser, avalia se ajudou | HU01, HU08, HU10, HU12, HU13 |
| Consulta pela página na internet | A pessoa cola o texto no formulário e lê a mesma resposta do robô | HU14 |
| Link de notícia | A pessoa envia o link; se o portal não é do catálogo, o robô pede o texto | HU02 |
| Título ou boato curto | A pessoa envia uma frase; o modelo não opina (RN04); a busca e as checagens respondem | HU01, HU05, HU06 |
| Imagem, vídeo ou áudio | A pessoa envia mídia; o robô explica que não analisa e orienta | HU03 |
| Auditoria pela equipe | A equipe consulta o registro de etapas de uma resposta | HU11 |

---

## 4. Estratégia do produto mínimo viável

Um produto mínimo viável de inteligência artificial prova que o modelo resolve
um problema real, antes do acabamento visual.

| Elemento | Critério | Situação |
|---|---|---|
| Desempenho mínimo viável | O modelo supera o de referência no pior grupo, não só na média | Atendido: ver a comparação em [Requisitos, seção 4.2](ENGENHARIA_DE_REQUISITOS.md#42-métricas-de-avaliação-fora-de-produção) |
| Calibração | A confiança exibida corresponde ao acerto real | Atendido: mesma seção |
| Estratégia de dados | Dados rotulados suficientes, sem vazamento entre treino e teste | Atendido: [Requisitos, seção 4.4](ENGENHARIA_DE_REQUISITOS.md#44-fontes-de-dados-e-coleta-contínua) |
| Potencial de integração | O canal escolhido reduz o atrito de adoção | Atendido: RF01 e RF15 |
| Valor sem o modelo | O produto continua útil se o modelo estiver fora | Atendido: RF04 a RF07 não dependem do modelo |
| Validação com pessoas reais | Pessoas do público-alvo usam e avaliam | Pendente: P11 |

O limite conhecido do produto mínimo viável é o desempenho do modelo em
textos curtos (RN04; pendência P09).

---

## 5. Requisitos não funcionais próprios de inteligência artificial

Continuam a numeração dos RNF01 a RNF05, que estão em
[Requisitos, seção 3](ENGENHARIA_DE_REQUISITOS.md#3-requisitos-não-funcionais),
e seguem o mesmo [padrão de redação](ENGENHARIA_DE_REQUISITOS.md#2-requisitos-funcionais):
verbo no infinitivo e objeto no título, e o atributo de qualidade logo abaixo.

### RNF06 — Responder dentro dos limites de tempo e de custo

**Atributo de qualidade:** desempenho e escalabilidade.

| Critério | Valor atual | Meta proposta | Situação |
|---|---|---|---|
| Latência do modelo próprio | 37 milissegundos por texto, sem placa de vídeo (medido em um Apple M4) | Abaixo de 200 milissegundos | Atendido |
| Latência da resposta completa (IND11) | Medida pela avaliação: mediana de 39,9 segundos por caso na iteração 0, com modelo de linguagem local. Limites: 60 segundos no agente de busca, 180 na interface de programação, 240 por chamada ao modelo de linguagem e 600 no julgamento das fontes | Mediana abaixo de 30 segundos | Parcial: P07 |
| Vazão | 30 requisições por minuto por endereço de rede | A definir com o uso real | Parcial |
| Capacidade diária e controle de custo | 100 buscas e 50 chamadas ao modelo de linguagem por dia. Uma consulta com julgamento das fontes faz várias chamadas, e a avaliação completa precisou de limites de 1.000 | 200 consultas por dia | Pendente |
| Picos de demanda | Sem fila. Buscas repetidas em até uma hora vêm de cache; o reaproveitamento da resposta inteira está só na branch de melhorias (P01) | Fila com limite de concorrência | Parcial |

### RNF07 — Proteger os dados e o sistema contra uso indevido

**Atributo de qualidade:** segurança e privacidade.

| Critério | Como é tratado | Situação |
|---|---|---|
| Dados em trânsito | Conexões cifradas com o Telegram e com os serviços externos de busca e de modelo de linguagem. A interface de programação própria ainda não tem cifragem configurada | Parcial |
| Dados em repouso | Cada execução grava em `runs/` o texto da consulta, as chamadas ao modelo de linguagem e as fontes, sem identificar a pessoa. Não há prazo de retenção | Parcial: P15 |
| Chaves de serviço | Lidas do ambiente, fora do controle de versão, sem valor padrão | Atendido |
| Acesso a endereços | Só portais do catálogo, com limite de tamanho e de tempo | Atendido |
| Injeção de instruções | Texto da pessoa e das páginas é tratado como dado. A delimitação explícita e o teste de blindagem estão só na branch de melhorias | Parcial: P01 |
| Texto que imita jornal para enganar o modelo | O modelo reage ao formato: um boato escrito como reportagem recebe probabilidade baixa. Mitigação: o modelo não entra na avaliação (RN11) | Atendido no produto; pendente no modelo (P09) |
| Invenção pelo modelo de linguagem | O julgamento de cada fonte só vale com um trecho citado que exista na página (semelhança mínima de 0,9) | Atendido |
| Envenenamento dos dados de avaliação | Votos podem ser manipulados e exigem curadoria antes de entrar em treino | Pendente: P05 |
| Envenenamento do catálogo | Portais novos não valem na consulta em que são descobertos, mas entram no catálogo sem aprovação | Parcial: P05 |
| Lei Geral de Proteção de Dados | Aviso no início da conversa; orientação para não enviar dados pessoais | Parcial: falta a política de retenção (P15) |

### RNF08 — Manter a resposta disponível diante de falhas

**Atributo de qualidade:** confiabilidade e manutenção.

| Critério | Como é tratado | Situação |
|---|---|---|
| Falha de uma etapa | A resposta continua e a falha vira limitação declarada (RF11) | Atendido |
| Falha do modelo de linguagem | Regras fixas extraem as afirmações; sem julgamento das fontes, a resposta é "indeterminada" (RN12) | Atendido, com perda de qualidade (P02) |
| Falha da busca | O sistema usa só o índice de checagens, se ligado | Atendido |
| Falha do modelo próprio | A etapa é registrada como falha e a resposta segue sem o sinal de estilo | Atendido |
| Estabilidade da resposta (IND07) | O estilo saiu da avaliação e as faixas ficaram simétricas, o que remove a principal causa de propensões diferentes para a mesma notícia. Falta medir | Parcial: P04 |
| Frequência de retreino | Não definida. **Proposta:** a cada três meses, ou quando um sinal da [seção 10](#10-monitoramento-contínuo) passar do limite | Pendente: P13 |
| Monitoramento de desvio | Ver [seção 10](#10-monitoramento-contínuo) | Parcial: P07 |
| Retorno à versão anterior | O modelo é trocado por configuração. Hoje só há uma versão publicada; sem ela, o substituto identificado como provisório assume | Parcial |

### RNF09 — Medir e mitigar os vieses do modelo e dos dados

**Atributo de qualidade:** ética e justiça.

O conjunto de dados tem vieses conhecidos, medidos no conjunto de teste
descrito em [Requisitos, seção 4.2](ENGENHARIA_DE_REQUISITOS.md#42-métricas-de-avaliação-fora-de-produção).
Os valores que já estão naquela seção não são repetidos aqui.

| Viés | Evidência | Mitigação | Situação |
|---|---|---|---|
| Origem do texto | Em 11 grupos de origem, todos os textos têm o mesmo rótulo. Um modelo pode aprender a origem em vez do conteúdo | Treino só nos grupos informativos, com pesos; a métrica principal é o pior grupo | Parcial: ver o pior grupo na [seção 4.2 dos requisitos](ENGENHARIA_DE_REQUISITOS.md#42-métricas-de-avaliação-fora-de-produção) |
| Canal | Acerto de 0,97 em portais, 0,85 em alegações de agências, 0,67 em mensagens de aplicativo, 0,63 em alegações externas | Relato por canal em toda avaliação | Parcial |
| Tamanho do texto | Acerto menor nas notícias verdadeiras curtas: ver a tabela por tamanho na [seção 4.2 dos requisitos](ENGENHARIA_DE_REQUISITOS.md#42-métricas-de-avaliação-fora-de-produção) | O modelo não opina em textos curtos (RN04) | Atendido no robô; pendente no modelo (P09) |
| Classe | A maioria dos textos de treino é desinformação, e a precisão na classe verdadeira é menor: ver a [seção 4.2 dos requisitos](ENGENHARIA_DE_REQUISITOS.md#42-métricas-de-avaliação-fora-de-produção) | Calibração feita em um conjunto separado do treino e do teste | Parcial |
| Variedade do português | Acerto de 0,71 em português de Portugal, contra 0,84 no do Brasil | Relato separado | Pendente |
| Tema e posição política | Não medido | **Proposta:** medir o acerto por tema e por espectro político das fontes | Pendente: P14 |

**Salvaguardas do produto**, independentes do modelo: a resposta nunca é
binária (RF12); nenhum domínio é bloqueado por lista (RN06); termos
partidários são proibidos no texto da resposta (RNF02); o modelo de linguagem
não redige a conclusão (RN08); o modelo próprio não altera a propensão (RN11).

---

## 6. Reprodutibilidade

| O que é versionado | Como | Situação |
|---|---|---|
| Código | Git, com uma mudança por registro e testes em cada uma | Atendido |
| Dados | Arquivos grandes publicados como versão do repositório. A assinatura de cada arquivo fica registrada em `prepare_stats.json` | Atendido |
| Separação de treino e teste | Arquivo próprio, com assinatura registrada e semente fixa (42) | Atendido |
| Modelo | Pesos publicados como versão `modelo-v6-R0`, com a calibração no mesmo pacote | Atendido |
| Experimentos | Cada execução grava `run_config.json` com as assinaturas do código, dos dados e da separação, e todos os parâmetros | Atendido |
| Ambiente | Lista de dependências; versões das bibliotecas registradas no relatório do treino | Parcial: P16 |
| Avaliação do sistema | Casos rotulados em `eval/` (70 de desenvolvimento e um conjunto reservado); gravação das respostas externas para reprodução determinística (`factcheck_mvp/replay.py`) | Atendido |

Assinaturas da execução R0: dados `596b0911…`, separação `6df3d748…`, código
de treino `dfa29ba7…`. Com o mesmo código, os mesmos dados e a mesma semente,
o treino é determinístico. Os requisitos sobre os dados estão em
[Requisitos, seção 4.4](ENGENHARIA_DE_REQUISITOS.md#44-fontes-de-dados-e-coleta-contínua).

---

## 7. Validação

Um produto de inteligência artificial é validado de duas formas, e cada parte
do sistema usa a que lhe cabe.

| | Partes determinísticas | Partes probabilísticas |
|---|---|---|
| O que é | Regras, formatação, segurança, contratos de entrada e saída | Modelo próprio, modelo de linguagem, avaliação final |
| Como validar | Testes automatizados com resultado fixo | Desempenho estatístico em conjuntos de referência; `eval/run.py --gate` compara com a rodada anterior |
| Critério | Todos os testes passam | Indicadores acima das metas; nenhum grupo abaixo do mínimo |
| Onde está | [Requisitos, seção 5.1](ENGENHARIA_DE_REQUISITOS.md#51-matriz-de-rastreabilidade-dos-requisitos): requisito, arquivo e teste | Modelo: [seção 4.2](ENGENHARIA_DE_REQUISITOS.md#42-métricas-de-avaliação-fora-de-produção). Sistema em uso: [seção 4.3](ENGENHARIA_DE_REQUISITOS.md#43-métricas-em-produção). Resultado e experiência: [seção 1.4](ENGENHARIA_DE_REQUISITOS.md#14-indicadores-de-desempenho), IND04 a IND13 |
| Situação | Atendido: 319 testes passando | Parcial: o modelo está medido; o sistema completo tem uma medição reconstruída em 70 casos, e falta uma rodada completa (P04) |

---

## 8. Experiência de uso para sistemas probabilísticos

| Prática | Requisito que a garante | Situação |
|---|---|---|
| Comunicar capacidades e limitações | RF01 (mensagem inicial), RF11 (limitações em cada resposta) | Atendido |
| Explicar como a decisão foi tomada | RNF01, RF11, RF13 | Atendido |
| Indicar o nível de confiança | RF09 | Atendido |
| Projetar para falhas | RNF08; estado "indeterminada" (RF13) | Atendido |
| Manter a pessoa no controle | RF12, RF14 | Atendido |
| Linguagem acessível | RNF04 | Parcial: P11 |

---

## 9. Operação e implantação

| Prática | Situação atual | Proposta |
|---|---|---|
| Integração contínua | Os testes e a comparação da avaliação (`eval/run.py --gate`) são executados à mão | Executar os testes a cada envio ao repositório (P12) |
| Treinamento contínuo | O treino é manual, em computador local ou em caderno na nuvem | Retreino periódico, com o mesmo conjunto de teste (P13) |
| Entrega contínua | O robô é iniciado por um roteiro local | Implantação automatizada depois dos testes |
| Modo sombra | Não existe | O modelo novo recebe as consultas reais sem influenciar a resposta, e os resultados são comparados (P13) |
| Liberação gradual | Não existe | Liberar o modelo novo para uma parte das consultas antes de todas |
| Dois ambientes | Não existe | Manter a versão anterior pronta para retorno imediato |
| Troca do modelo | Por configuração, sem alterar código (RNF05) | Manter |

Situação geral: **Pendente**. O produto está em fase de validação, e a
automação está prevista na fase 3 do `ROADMAP.md`.

---

## 10. Monitoramento contínuo

Um produto de inteligência artificial nunca está concluído: o desempenho cai
quando o mundo muda. Notícias mudam de tema todos os dias, e as formas de
desinformação também.

| Fenômeno | O que é | Sinal proposto | Situação |
|---|---|---|---|
| Desvio dos dados | As consultas ficam diferentes dos textos de treino | Proporção de consultas curtas demais para o modelo; distribuição do tamanho dos textos | Pendente: P07 |
| Desvio de conceito | O que caracteriza desinformação muda | Acerto no conjunto de alegações checadas, refeito todo mês com checagens novas (IND04, IND05) | Pendente: P07 |
| Deterioração de desempenho | Os indicadores caem | Proporção de respostas "indeterminada" (IND03); avaliações negativas (IND09); distribuição da probabilidade do modelo | Pendente: P07 |
| Falhas de serviço | Busca ou modelo de linguagem fora | Proporção de etapas com falha | Gravado por execução; sem agregação |

Cada execução já grava seus sinais em `runs/` (`docs/TELEMETRIA.md`). Falta
agregá-los e acompanhá-los ao longo do tempo (P07).

---

As pendências citadas neste documento (P01 a P16) estão reunidas, com impacto
e esforço, em [Requisitos, seção 5.5](ENGENHARIA_DE_REQUISITOS.md#55-pendências).
