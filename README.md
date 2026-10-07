# monitoraME Pipeline API

API em FastAPI para o módulo analítico retrospectivo do MonitoraME. O serviço coleta despesas empenhadas do TCE-CE, trata anulações, enriquece fornecedores com dados cadastrais e calcula indicadores de participação por porte empresarial, natureza da despesa e origem geográfica.

O módulo usa TCE-CE, IBGE e OpenCNPJ. Dados de editais, dispensas, PCA e demais oportunidades de contratação não fazem parte desta codebase.

## Funcionalidades

- Coleta mensal de notas de empenho e suas anulações no TCE-CE.
- Paginação da API externa e validação completa da competência antes da publicação.
- Persistência versionada por município e competência, com substituição atômica do lote publicado.
- Valores monetários armazenados e agregados como inteiros em centavos.
- Identificação e enriquecimento cadastral de fornecedores por CNPJ.
- Classificação pelas sete naturezas de despesa monitoradas.
- Indicadores por porte, natureza, origem geográfica e fornecedor.
- Registro separado das execuções de ingestão.

## Fontes de dados

| Fonte | Uso |
| --- | --- |
| TCE-CE | Empenhos, anulações, contratos, contratados e municípios. |
| OpenCNPJ | Porte, município de sede e dados cadastrais dos fornecedores. |
| IBGE | Catálogo e validação de municípios. |

## Estrutura principal

```text
app/
├── api/endpoints/       # rotas HTTP e tradução de erros
├── core/                # configuração, ORM e conexões
└── pipeline/
    ├── ingestion/       # clientes TCE-CE, IBGE e OpenCNPJ
    ├── cleaners/        # normalização por fonte
    ├── persistence/     # lotes versionados de empenhos e anulações
    ├── analitico.py     # base canônica de análise
    ├── analisys.py      # orquestração dos fluxos da API
    ├── kpis.py          # agregações e indicadores
    └── tce_despesas.py  # ingestão mensal do TCE-CE
```

## Requisitos

- Python 3.14
- PostgreSQL
- Acesso às APIs do TCE-CE, IBGE e OpenCNPJ
- Dependências de `requirements.txt`

## Configuração

Crie o arquivo local de ambiente:

```bash
cp .env.example .env
```

Variáveis usadas pela aplicação:

| Variável | Obrigatória | Uso |
| --- | --- | --- |
| `DATABASE_URL` | Sim | Banco principal com caches e lotes analíticos. |
| `LOG_DATABASE_URL` | Sim | Banco da tabela `logs_ingestao`. |
| `LOG_LEVEL` | Não | Nível dos logs operacionais (`DEBUG`, `INFO`, `WARNING`, `ERROR`); padrão `INFO`. |
| `DATABASE_SSLMODE` | Não | Modo SSL do banco principal; padrão `require`. |
| `LOG_DATABASE_SSLMODE` | Não | Modo SSL do banco de logs. |
| `TCE_CE_BASE_URL` | Sim | API de dados abertos do TCE-CE. |
| `IBGE_LOCALIDADES_BASE_URL` | Sim | API de localidades do IBGE. |
| `OPENCNPJ_BASE_URL` | Sim | API cadastral OpenCNPJ. |
| `UF_PADRAO` | Sim | UF compradora usada pela análise. |
| `CODIGO_MUNICIPIO_TCE_PADRAO` | Sim | Código interno padrão do município no TCE-CE. |

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
alembic upgrade head
alembic -n logs upgrade head
uvicorn app.main:app --reload
```

No Windows com Git Bash, ative o ambiente com `source .venv/Scripts/activate`.

A documentação OpenAPI fica em `http://127.0.0.1:8000/docs`.

## Rotas do pipeline

- `GET /pipeline/tce/contratos`: consulta e enriquece contratos do TCE-CE.
- `GET /pipeline/tce/kpis/portes-por-mes`: calcula a participação mensal por porte.
- `GET /pipeline/tce/overview`: calcula o overview exclusivo de ME e MEI.
- `GET /pipeline/tce/overview/municipal`: calcula o overview de um município por período, código TCE e portes opcionais.
- `POST /pipeline/tce/comparisons`: compara dois overviews usando somente dados armazenados.
- `GET /pipeline/tce/empenhos`: lista e pagina empenhos publicados armazenados.
- `GET /pipeline/tce/empenhos/{chave_empenho}`: detalha um empenho e suas anulações.
- `GET /pipeline/tce/fornecedores`: lista fornecedores e valores no recorte.
- `GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}`: retorna cadastro, indicadores e gráficos do fornecedor.
- `GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}/empenhos`: pagina as notas consideradas do fornecedor.
- `GET /pipeline/tce/analitico/indicadores`: atualiza as competências e calcula os indicadores analíticos principais sobre empenhos líquidos.
- `GET /pipeline/tce/analise-territorial`: calcula retenção local, destino dos recursos, concentração e rankings usando somente dados publicados no banco local.

As rotas analíticas recebem datas no formato `YYYY-MM-DD`. Uma competência só entra no cálculo depois de ser coletada, validada e publicada. Falhas totais de atualização retornam `503`; meses que falharem durante uma consulta parcialmente bem-sucedida são sinalizados nos metadados.

### Overview municipal por porte

Por padrão, `GET /pipeline/tce/overview/municipal` considera e discrimina MEI,
ME, EPP, demais portes e fornecedores sem porte identificado. O parâmetro
repetível `company_sizes` restringe todo o cálculo; para reproduzir o recorte
anterior de microempresas, envie `ME` e `MEI`:

```http
GET /pipeline/tce/overview/municipal?data_inicial=2025-01-01&data_final=2025-12-31&codigo_municipio_tce=010&company_sizes=ME&company_sizes=MEI
```

Os valores aceitos são `ME`, `MEI`, `EPP`, `OTHER` e `UNKNOWN`. No retorno,
`OTHER` é apresentado como `DEMAIS` e `UNKNOWN` como `NAO_IDENTIFICADO`.

### Análise territorial

`GET /pipeline/tce/analise-territorial` é somente leitura e não consulta fontes
externas. A rota recebe `start_date`, `end_date`, `municipality_tce_code`, `uf`,
listas opcionais de `company_sizes`, `supplier_origins` e
`expense_element_codes`, além de `ranking_limit`. O retorno inclui valores
empenhado, anulado e líquido, retenção local, evasão municipal, retenção no
Ceará, cobertura cadastral e geográfica, destinos dos recursos, portes,
elementos, evolução mensal, concentração HHI e ranking de fornecedores.

```http
GET /pipeline/tce/analise-territorial?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057&ranking_limit=10
```

Todos os valores monetários são inteiros em centavos e os percentuais usam a
escala de `0` a `100`. Competências ausentes aparecem explicitamente na
cobertura e não são convertidas em meses com valor zero.

Competências publicadas são reutilizadas enquanto estiverem dentro da política
de atualização: uma hora para o mês atual, 24 horas para os três meses
anteriores e sete dias para os meses mais antigos. Quando uma publicação
vencida não pode ser atualizada, sua última versão continua no cálculo e a
competência aparece em `competencias_desatualizadas`. Competências sem qualquer
publicação continuam aparecendo em `competencias_ausentes`.

### Overview de ME e MEI

`GET /pipeline/tce/overview` recebe `data_inicial`, `data_final` e aceita
`codigo_municipio` opcional. Sem o código, consolida todo o Ceará usando apenas
os lotes e cadastros já publicados no banco local. Com o código, mantém o fluxo
municipal, atualizando as competências e o cadastro dos fornecedores antes do
cálculo. A resposta restringe o detalhamento geográfico a empenhos líquidos de
ME e MEI. Essa restrição não altera os demais indicadores da API.

O total de compras consideradas corresponde aos portes identificados `ME +
MEI + EPP + DEMAIS`. A participação é `(ME + MEI) / compras consideradas`;
portes não identificados ficam fora da razão. A evolução mensal compara esses
dois valores. Os indicadores geográficos e o destino dos recursos usam
exclusivamente ME e MEI. Origem não identificada permanece no denominador
geográfico, sem ser tratada como recurso local ou externo.

O KPI `total_compras_ME_centavos` representa a soma líquida de ME e MEI no
período completo.

`participacao_por_porte_empresarial` apresenta valor e percentual de `ME`,
`MEI`, `OUTROS_PORTES` (`EPP + DEMAIS`) e `NAO_IDENTIFICADO`. Nesse indicador,
o denominador inclui as quatro categorias para que seus percentuais totalizem
`100` quando houver valor no período.

`elementos_despesa` apresenta os sete elementos monitorados no período total,
com código de dois dígitos, nome e valor líquido em centavos. O cálculo usa
somente empenhos de ME e MEI e mantém elementos sem movimento com valor zero.

Valores monetários são inteiros em centavos e percentuais variam de `0` a
`100`. Um período publicado sem compras de porte identificado retorna `200`,
KPIs zerados, evolução vazia e os quatro destinos com valor e percentual zero.

### Comparação de overviews

`POST /pipeline/tce/comparisons` recebe dois lados independentes e permite
comparar períodos (`PERIODS`), municípios (`MUNICIPALITIES`) ou ambos
(`MIXED`). Os municípios são informados pelo código IBGE e resolvidos para o
código interno do TCE usando o mapeamento armazenado.

A rota é estritamente somente leitura. Ela não atualiza competências, não
renova o cadastro de fornecedores e não consulta TCE-CE, IBGE ou OpenCNPJ.
Autenticação e assinatura são responsabilidades do proxy que publica a API.

Exemplo de comparação mista:

```json
{
  "comparison_mode": "MIXED",
  "left": {
    "label": "Acaraú 2025",
    "filters": {
      "uf": "CE",
      "municipality_ibge_code": "2300200",
      "start_date": "2025-01-01",
      "end_date": "2025-12-31",
      "company_sizes": ["ME", "EPP", "MEI", "OTHER", "UNKNOWN"],
      "supplier_origins": [],
      "expense_element_codes": []
    }
  },
  "right": {
    "label": "Sobral 2024",
    "filters": {
      "uf": "CE",
      "municipality_ibge_code": "2312908",
      "start_date": "2024-01-01",
      "end_date": "2024-12-31",
      "company_sizes": ["ME", "EPP", "MEI", "OTHER", "UNKNOWN"],
      "supplier_origins": [],
      "expense_element_codes": []
    }
  }
}
```

Listas vazias significam que a dimensão não restringe a base. Os portes
aceitos são `ME`, `MEI`, `EPP`, `OTHER` e `UNKNOWN`; as origens aceitas são
`NO_MUNICIPIO_COMPRADOR`, `EM_OUTRO_MUNICIPIO`, `FORA_DO_CEARA` e
`ORIGEM_NAO_IDENTIFICADA`. Os elementos aceitos são `30`, `32`, `35`, `39`,
`40`, `51` e `52`.

`left.overview` e `right.overview` preservam todos os blocos do overview. O
objeto `sections` acrescenta maior, menor e diferença absoluta para cada KPI,
porte, elemento, destino e posição da série mensal. Competências publicadas
sem movimento são válidas e aparecem com zero na seção mensal; competências
sem publicação aparecem em `competencias_ausentes` e não são convertidas em
zero.

Erros da rota usam um envelope próprio e propagam `X-Request-ID` quando o
cabeçalho é enviado pelo proxy:

```json
{
  "error": {
    "code": "INVALID_DATE_RANGE",
    "message": "start_date deve ser menor ou igual a end_date.",
    "details": [
      {
        "field": "left.filters.start_date",
        "reason": "after_end_date",
        "value": "2026-12-31"
      }
    ],
    "request_id": "req_01K6ME9W3H"
  }
}
```

### Consulta de empenhos armazenados

`GET /pipeline/tce/empenhos` é uma rota somente leitura: não executa ingestão
nem acessa TCE-CE, IBGE ou OpenCNPJ. Ela considera apenas empenhos dos sete
elementos monitorados pertencentes a lotes com status `PUBLICADO`.

Os parâmetros obrigatórios são `start_date` e `end_date`. A consulta aceita
também `uf`, `municipality_tce_code`, listas repetidas de `company_sizes`,
`supplier_origins` e `expense_element_codes`, além de `search`, `page`,
`page_size`, `sort_by` e `sort_order`. Filtros de dimensões diferentes usam
`AND`; valores repetidos na mesma dimensão usam `OR`. Sem filtro de elemento,
os sete elementos monitorados são incluídos.

Exemplo:

```http
GET /pipeline/tce/empenhos?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057&company_sizes=ME&company_sizes=MEI&expense_element_codes=39&page=1&page_size=50&sort_by=net_value&sort_order=desc
```

Cada item retorna a `chave_empenho` canônica, um `display_id` no formato
`EMP-AAAA-NUMERO`, data, município comprador, fornecedor, porte, origem,
elemento de despesa e valores bruto, anulado e líquido em centavos. A busca
aceita chave, número, `display_id`, documento ou nome do fornecedor.

`GET /pipeline/tce/empenhos/{chave_empenho}` recebe a chave com caracteres
reservados codificados na URL (por exemplo, `|` como `%7C`). O detalhe inclui
classificação orçamentária, dados cadastrais armazenados do fornecedor,
referências de contrato e licitação, lista das anulações, valores consolidados
e metadados do lote publicado. O payload bruto das fontes não é exposto.

As duas rotas usam o mesmo envelope padronizado de erro e propagam
`X-Request-ID`. Autenticação e assinatura permanecem sob responsabilidade do
proxy que publica a API.

### Consulta de fornecedores armazenados

As três rotas de fornecedores são somente leitura e consideram exclusivamente
empenhos dos lotes `PUBLICADO`. Elas recebem o mesmo recorte por período,
município TCE, porte empresarial, origem e elemento de despesa usado pela
consulta de empenhos.

`GET /pipeline/tce/fornecedores` agrupa as notas por tipo e documento do
fornecedor. A resposta apresenta nome, porte, disponibilidade cadastral,
origens no recorte, valores bruto, anulado e considerado, quantidade de notas,
municípios e datas da primeira e última nota. Fornecedores sem cadastro no
OpenCNPJ continuam na listagem com `cadastro_status=NAO_DISPONIVEL`.

```http
GET /pipeline/tce/fornecedores?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057&page=1&page_size=50&sort_by=net_value&sort_order=desc
```

`GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}` aceita
`CNPJ` ou `CPF` no tipo e retorna o cadastro armazenado, totais do recorte,
evolução mensal, distribuição pelos sete elementos e participação no valor
total filtrado. O cadastro é a observação mais recente disponível e não uma
fotografia histórica do exercício.

```http
GET /pipeline/tce/fornecedores/CNPJ/05537536000164?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057
```

`GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}/empenhos`
retorna as notas em uma consulta separada e paginada, preservando o mesmo
formato dos itens de `/pipeline/tce/empenhos`. Assim, navegar ou ordenar a
tabela não recalcula os gráficos do detalhe.

Nenhuma dessas rotas atualiza o cache cadastral ou consulta TCE-CE, IBGE ou
OpenCNPJ. O payload bruto do cadastro não é exposto.

## Persistência

O banco principal mantém:

- `ibge_municipios`, `tce_municipios`, `fornecedores_me` e
  `fornecedores_cache`, usados como caches;
- `tce_despesa_ingestion_runs`, com o estado de cada carga mensal;
- `tce_empenhos`, com as notas de empenho versionadas por lote;
- `tce_anulacoes_empenhos`, com as anulações vinculadas aos empenhos.

A resolução do código interno de município do TCE consulta primeiro
`tce_municipios`, associada a `ibge_municipios`. Quando o código ainda não
existe no cache, a aplicação consulta a lista do TCE uma vez e persiste os
mapeamentos em lote para as consultas seguintes.

O cache cadastral completo de fornecedores mantém os dados normalizados e o
payload bruto do OpenCNPJ. Registros encontrados valem por 30 dias, respostas
`nao_encontrado` por sete dias e indisponibilidades por uma hora. A consulta ao
OpenCNPJ ocorre apenas para CNPJs ausentes ou vencidos. Se a renovação falhar,
o último dado conhecido é preservado e devolvido com
`cache_desatualizado=true` até uma nova tentativa.

O banco de logs mantém `logs_ingestao`, para auditoria das coletas, e
`logs_aplicacao`, para alertas operacionais. Os dois bancos usam ambientes
Alembic independentes.

## Observabilidade

A aplicação escreve logs operacionais estruturados em JSON na saída padrão. Cada
registro contém `level`, `logger`, `message` e apenas o contexto
pertinente, como duração, contagens, fonte e etapa. Segredos conhecidos são
removidos dos campos estruturados e payloads das fontes externas não são registrados.

As execuções de ingestão do TCE-CE continuam sendo auditadas no banco separado em
`logs_ingestao`; uma falha nesse banco gera um alerta operacional, mas não interrompe
o pipeline principal. Logs `WARNING`, `ERROR` e `CRITICAL` são persistidos em
`logs_aplicacao`; registros `INFO` permanecem apenas na saída padrão para evitar
volume desnecessário no PostgreSQL.

A migration `0003_remove_pncp` exclui as antigas tabelas do módulo de oportunidades. A aplicação dessa migration remove definitivamente os dados existentes nessas tabelas.

## Testes

```bash
python -m unittest discover -s tests
```
