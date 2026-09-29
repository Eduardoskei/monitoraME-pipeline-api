# Contrato TCE-CE: empenhos e anulações

Este documento congela o contrato externo usado pelo núcleo analítico do
MonitoraME. A fonte é a API de Dados Abertos do SIM/TCE-CE, grupo DCD
(Documentação Comprobatória de Despesas).

## Unidade de coleta

A coleta ocorre por município e competência mensal. Para `2025-01`:

```text
codigo_municipio=010
exercicio_orcamento=202500
data_referencia_doc=202501
$count=1000
$start_index=0
$format=json
```

`$start_index` avança de 1.000 em 1.000 até uma página com menos de 1.000
elementos. A infraestrutura que executa a coleta precisa ter IP brasileiro.

## Notas de empenho

Endpoint: `GET /notas_empenhos`.

Campos obrigatórios para o contrato canônico:

| Campo externo | Campo canônico | Tratamento |
| --- | --- | --- |
| `codigo_municipio` | `codigo_municipio_tce` | texto sem espaços externos |
| `exercicio_orcamento` | `exercicio_orcamento`, `exercicio` | `AAAA00` e `AAAA` |
| `codigo_orgao` | `codigo_orgao` | texto sem espaços externos |
| `codigo_unidade_orcamentaria` | `codigo_unidade_orcamentaria` | texto sem espaços externos |
| `data_emissao_empenho` | `data_empenho` | data civil `AAAA-MM-DD` |
| `numero_empenho` | `numero_empenho` | texto |
| `data_referencia_doc` | `competencia` | `AAAAMM` para `AAAA-MM` |
| `codigo_elemento_despesa` | códigos de natureza e elemento | preserva 8 dígitos e extrai posições 5 e 6 |
| `valor_empenhado` | `valor_empenhado_centavos` | decimal para inteiro, half-up |
| `codigo_tipo_negociante` | `tipo_documento_fornecedor` | `1=CNPJ`, `2=CPF`, `6=folha`, `7=diárias` |
| `numero_documento_negociante` | documento/CPF/CNPJ | somente dígitos e validação conforme o tipo |

Nome, município e UF informados pelo TCE são preservados para auditoria. A
classificação geográfica definitiva será feita posteriormente com o
estabelecimento da base cadastral e código IBGE.

A chave natural do empenho é:

```text
codigo_municipio
+ exercicio_orcamento
+ codigo_orgao
+ codigo_unidade_orcamentaria
+ data_emissao_empenho
+ numero_empenho
```

No contrato canônico, os componentes são unidos por `|` em `chave_empenho`.

## Anulações de empenho

Endpoint: `GET /notas_anulacoes_empenhos`.

`numero_nota_empenho` é normalizado para `numero_empenho`. Município,
exercício, órgão, unidade, data do empenho e número do empenho produzem a mesma
`chave_empenho` da nota original.

A chave natural da anulação acrescenta:

```text
data_anulacao + numero_nota_anulacao
```

O valor é convertido para `valor_anulacao_centavos` usando a mesma regra
monetária do empenho.

## Tratamento monetário

O JSON financeiro é decodificado com `Decimal`. O valor é arredondado uma
única vez para duas casas usando `ROUND_HALF_UP` e armazenado como inteiro em
centavos. Exemplo normativo:

```text
"1,005" -> 101
```

## Regra de apuração líquida

Para um período de apuração, o valor líquido de cada empenho será:

```text
valor_empenhado_centavos
- soma(valor_anulacao_centavos das anulações aplicáveis até o fim do período)
```

A ingestão preserva empenhos e anulações como fatos separados. A consolidação
não altera nem elimina o registro original. Anulações órfãs, duplicadas ou que
façam o valor líquido ficar negativo devem invalidar o lote antes da publicação.

## Publicação versionada

Empenhos e anulações da mesma competência formam um único lote. A coleta e a
normalização terminam antes da publicação. No banco, a validação, a gravação
dos fatos e a troca da versão publicada ocorrem na mesma transação.

Só pode existir um lote `PUBLICADO` para cada município e competência. Ao
reprocessar uma competência válida, o lote anterior muda para `SUBSTITUIDO` e
permanece disponível por sete dias. Lotes rejeitados também permanecem por
sete dias. A limpeza exclui apenas lotes `SUBSTITUIDO` ou `REJEITADO` cuja
expiração já venceu; nunca exclui o lote publicado.

Uma falha de rede ou de contrato não equivale a uma resposta vazia e gera um
lote `REJEITADO`, preservando a versão publicada. Duplicatas idênticas são
deduplicadas. Duplicatas com a mesma chave e conteúdo divergente rejeitam a
competência inteira.

Anulações são verificadas contra os empenhos publicados em outras competências
e contra os empenhos do lote em validação. Assim, uma anulação publicada em
fevereiro pode corrigir o valor líquido de um empenho de janeiro. Se janeiro
for reprocessado depois, seu novo valor também é validado contra a anulação de
fevereiro já publicada.

## Consumo pelos indicadores

As rotas `/pipeline/tce/kpis/portes-por-mes` e
`/pipeline/tce/analitico/indicadores` atualizam primeiro cada competência
mensal coberta pelo intervalo solicitado. Em seguida, consultam apenas lotes
publicados e somente empenhos marcados com `natureza_considerada=true`.

O valor usado em todos os indicadores é expresso em reais e deriva de:

```text
(valor_empenhado_centavos - soma_das_anulacoes_publicadas_centavos) / 100
```

A soma das anulações considera qualquer competência atualmente publicada. Por
isso, uma anulação posterior que já exista no banco corrige o mês original do
empenho. Nesta etapa, uma consulta não coleta automaticamente competências
posteriores ao período solicitado.

Quando parte das competências não pode ser atualizada, elas aparecem em
`competencias_ausentes` e ficam fora do cálculo daquela resposta. Se nenhuma
competência puder ser atualizada, a API responde com HTTP `503`.

O porte e os dados geográficos são consultados na OpenCNPJ durante a execução.
Fornecedores sem enriquecimento permanecem no denominador com porte e origem
não identificados.

## Payload e evolução externa

O registro bruto completo é preservado em `payload_bruto`. Campos adicionais
da API podem ser incorporados ao modelo sem mudar a identidade do fato. O nome
externo `data_emissao_empenho_susbtituto` contém a grafia publicada pelo TCE e
deve ser lido exatamente assim enquanto permanecer no contrato oficial.
