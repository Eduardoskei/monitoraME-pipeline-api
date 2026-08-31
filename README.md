# monitoraME Pipeline API

API em FastAPI para consultar, limpar, enriquecer e analisar dados de contratações públicas a partir do PNCP, TCE-CE, IBGE e OpenCNPJ. O foco atual é apoiar análises de compras públicas e participação de microempresas (ME), especialmente no contexto do Ceará/Amontada.

## O Que Este Projeto Faz

- Consulta contratações publicadas no PNCP.
- Consulta contratos e contratados no TCE-CE.
- Limpa dados tabulares vindos de JSONs heterogêneos: nomes em `snake_case`, datas ISO, números, nulos e duplicatas.
- Normaliza e valida CNPJ/CPF sem descartar registros auditáveis.
- Enriquece municípios com dados oficiais do IBGE.
- Enriquece fornecedores com dados cadastrais da OpenCNPJ.
- Calcula KPI de participação mensal de ME em contratos do TCE-CE.
- Mantém cache em Postgres para municípios IBGE e fornecedores ME.
- Registra execuções de ingestão TCE-CE em uma tabela de logs no banco configurado por `LOG_DATABASE_URL`.

## Tree Do Projeto

```text
.
├── .env.example
├── .gitignore
├── Pipfile
├── Pipfile.lock
├── README.md
├── requirements.txt
├── app
│   ├── __init__.py
│   ├── main.py
│   ├── utils.py
│   ├── api
│   │   ├── __init__.py
│   │   ├── router.py
│   │   └── endpoints
│   │       ├── __init__.py
│   │       ├── health.py
│   │       └── pipeline.py
│   ├── core
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── logging.py
│   │   └── log_database.py
│   └── pipeline
│       ├── __init__.py
│       ├── analisys.py
│       ├── cleaners
│       │   ├── __init__.py
│       │   ├── ibge.py
│       │   ├── opencnpj.py
│       │   ├── pncp.py
│       │   └── tce.py
│       ├── kpis.py
│       ├── merge.py
│       └── ingestion
│           ├── __init__.py
│           ├── pagination.py
│           ├── fornecedores.py
│           ├── ibge.py
│           ├── pncp.py
│           └── tce.py
└── tests
    ├── test_analisys.py
    ├── test_cleaning.py
    ├── test_config.py
    ├── test_fornecedores_ingestion.py
    ├── test_ibge_ingestion.py
    ├── test_kpis.py
    ├── test_logging.py
    ├── test_log_database.py
    ├── test_main.py
    ├── test_merge.py
    ├── test_pncp_ingestion.py
    ├── test_tce_ingestion.py
    └── test_utils.py
```

Arquivos gerados como `__pycache__/`, `.pytest_cache/`, `.venv/` e `.env` ficam fora da árvore acima.

## Requerimentos

Para rodar o projeto localmente:

- Windows com Git Bash instalado.
- Python `3.14`.
- `pip`.
- Acesso à internet para instalar pacotes e consultar PNCP, TCE-CE, IBGE e OpenCNPJ.
- Postgres configurado via `DATABASE_URL`, usado para cache local.
- Postgres configurado via `LOG_DATABASE_URL`, usado para logs de execução da ingestão.

Dependências Python principais:

- `fastapi`
- `uvicorn[standard]`
- `pandas`
- `plotly`
- `requests`
- `psycopg2-binary`
- `pydantic`
- `python-dotenv`

Essas dependências estão declaradas em `requirements.txt`.

## Quick Start No Windows Com Git Bash

### 1. Crie o ambiente virtual

No Git Bash:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure as variáveis de ambiente

```bash
cp .env.example .env
```

Revise o `.env` se precisar trocar URLs, UF padrão, município padrão ou credenciais de Postgres. O arquivo `.env` não deve ser versionado.

Variáveis esperadas:

| Variável | Obrigatória | Uso |
| --- | --- | --- |
| `DATABASE_URL` | Sim | Conexão obrigatória com o Postgres usado pelo cache local. |
| `LOG_DATABASE_URL` | Sim | Conexão obrigatória com o Postgres usado pela tabela `logs_ingestao`. |
| `DATABASE_SSLMODE` | Não | Modo SSL do Postgres. Padrão: `require`. |
| `LOG_DATABASE_SSLMODE` | Não | Modo SSL do Postgres de logs. Se ausente, usa `DATABASE_SSLMODE` ou `require`. |
| `TCE_CE_BASE_URL` | Sim | Base da API de dados abertos do TCE-CE. |
| `IBGE_LOCALIDADES_BASE_URL` | Sim | Base da API de localidades do IBGE. |
| `PNCP_CONSULTA_BASE_URL` | Sim | Base da API de consulta do PNCP. |
| `PNCP_GESTAO_BASE_URL` | Sim | Base da API de gestão/detalhes do PNCP. |
| `OPENCNPJ_BASE_URL` | Sim | Base da API OpenCNPJ. |
| `UF_PADRAO` | Sim | UF usada como filtro padrão. |
| `CODIGO_IBGE_PADRAO` | Sim | Código IBGE padrão do município. |
| `CODIGO_MUNICIPIO_TCE_PADRAO` | Sim | Código interno do município no TCE-CE. |
| `MODALIDADE_ID_PADRAO` | Sim | Modalidade padrão usada na consulta PNCP. |

Valores padrão atuais em `.env.example`:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/monitorame
LOG_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/monitorame_logs
DATABASE_SSLMODE=require
UF_PADRAO=CE
CODIGO_IBGE_PADRAO=2304400
CODIGO_MUNICIPIO_TCE_PADRAO=010
MODALIDADE_ID_PADRAO=6
```

### 3. Rode a API

```bash
uvicorn app.main:app --reload
```

Acesse:

- API: `http://127.0.0.1:8000`
- Swagger/OpenAPI: `http://127.0.0.1:8000/docs`

## Fluxo Interno

```text
Fontes externas
  ├─ PNCP
  ├─ TCE-CE
  ├─ IBGE
  └─ OpenCNPJ
        ↓
app/pipeline/ingestion
        ↓
app/pipeline/cleaners
        ↓
app/pipeline/merge
        ↓
app/pipeline/kpis
        ↓
app/pipeline/analisys
        ↓
app/api/endpoints
```

Responsabilidades por módulo:

- `app/main.py`: cria a aplicação FastAPI, registra rotas e inicializa/fecha o cache Postgres durante o lifespan.
- `app/api/endpoints/`: define os endpoints HTTP e traduz erros de domínio em códigos HTTP.
- `app/core/config.py`: carrega variáveis de ambiente obrigatórias via `python-dotenv`.
- `app/core/database.py`: gerencia pool Postgres e tabelas de cache `ibge_municipios` e `fornecedores_me`.
- `app/pipeline/ingestion/`: encapsula chamadas HTTP para PNCP, TCE-CE, IBGE e OpenCNPJ.
- `app/utils.py`: concentra funções utilitárias compartilhadas, incluindo o motor genérico de normalização de estruturas, colunas, tipos, datas, documentos, nulos e duplicatas.
- `app/pipeline/cleaners/pncp.py`, `tce.py`, `ibge.py` e `opencnpj.py`: aplicam as regras de limpeza específicas de cada API.
- `app/pipeline/merge.py`: cruza tabelas limpas entre fontes e aplica enriquecimentos.
- `app/pipeline/kpis.py`: calcula agregações e KPIs sobre bases já limpas/enriquecidas.
- `app/pipeline/analisys.py`: orquestra os fluxos completos usados pela API e serializa DataFrames para JSON.

## Cache Postgres

O Postgres é obrigatório para a API subir. `DATABASE_URL` precisa estar preenchida no ambiente ou no `.env`; se estiver ausente/vazia, a configuração falha na inicialização. Durante o lifespan, a API inicializa o schema do cache e encerra o pool ao desligar.

O cache mantém:

- `ibge_municipios`: código, nome e UF de municípios.
- `fornecedores_me`: CNPJs de fornecedores confirmados como ME.

As integrações com IBGE e OpenCNPJ usam esse cache para reduzir chamadas externas e reaproveitar dados já consultados.

## Logs De Ingestão TCE-CE

A ingestão TCE-CE grava uma linha por execução das funções públicas de coleta (`buscar_contratos`, `buscar_contratados`, `buscar_contratacoes`, `buscar_itens_contratacao` e `buscar_municipios`) na tabela `logs_ingestao`, criada automaticamente no banco apontado por `LOG_DATABASE_URL`.

Cada registro inclui fonte, etapa, status, data/hora de início e término, quantidade de registros processados, quantidade de falhas ocorridas, parâmetros da execução, totais em JSONB e mensagem de erro quando houver.

## Rodando Testes

Use descoberta explícita:

```bash
python -m unittest discover -s tests
```
