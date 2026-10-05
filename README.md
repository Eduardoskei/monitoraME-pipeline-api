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
- `POST /pipeline/tce/comparisons`: compara dois overviews usando somente dados armazenados.
- `GET /pipeline/tce/analitico/indicadores`: atualiza as competências e calcula os indicadores analíticos principais sobre empenhos líquidos.

As rotas analíticas recebem datas no formato `YYYY-MM-DD`. Uma competência só entra no cálculo depois de ser coletada, validada e publicada. Falhas totais de atualização retornam `503`; meses que falharem durante uma consulta parcialmente bem-sucedida são sinalizados nos metadados.

Competências publicadas são reutilizadas enquanto estiverem dentro da política
de atualização: uma hora para o mês atual, 24 horas para os três meses
anteriores e sete dias para os meses mais antigos. Quando uma publicação
vencida não pode ser atualizada, sua última versão continua no cálculo e a
competência aparece em `competencias_desatualizadas`. Competências sem qualquer
publicação continuam aparecendo em `competencias_ausentes`.

### Overview de ME e MEI

`GET /pipeline/tce/overview` recebe `data_inicial`, `data_final` e
`codigo_municipio`. A rota atualiza as competências e o cadastro dos
fornecedores antes do cálculo, mas restringe sua resposta a empenhos líquidos
de ME e MEI. Essa restrição não altera os demais indicadores da API.

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
