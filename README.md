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
- `GET /pipeline/tce/analitico/indicadores`: atualiza as competências e calcula os indicadores analíticos principais sobre empenhos líquidos.

As rotas analíticas recebem datas no formato `YYYY-MM-DD`. Uma competência só entra no cálculo depois de ser coletada, validada e publicada. Falhas totais de atualização retornam `503`; meses que falharem durante uma consulta parcialmente bem-sucedida são sinalizados nos metadados.

## Persistência

O banco principal mantém:

- `ibge_municipios` e `fornecedores_me`, usados como caches;
- `tce_despesa_ingestion_runs`, com o estado de cada carga mensal;
- `tce_empenhos`, com as notas de empenho versionadas por lote;
- `tce_anulacoes_empenhos`, com as anulações vinculadas aos empenhos.

O banco de logs mantém `logs_ingestao`. Os dois bancos usam ambientes Alembic independentes.

A migration `0003_remove_pncp` exclui as antigas tabelas do módulo de oportunidades. A aplicação dessa migration remove definitivamente os dados existentes nessas tabelas.

## Testes

```bash
python -m unittest discover -s tests
```
