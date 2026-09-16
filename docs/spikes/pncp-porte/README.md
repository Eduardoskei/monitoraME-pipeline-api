# Spike: cobertura de porte no PNCP

Execução UTC: 2026-09-14T19:25:05.019423+00:00. Semente: 20260914.

## Método e população

Recorte exploratório intencional: quatro municípios do Ceará, janeiro, maio e setembro de 2025; pregão eletrônico (6), dispensa (8) e inexigibilidade (9). Listagem integral de cada estrato município × mês × modalidade, seguida de sorteio uniforme sem reposição de até 2 compras por estrato. Até 5 itens são sorteados uniformemente por compra, e todos os seus resultados são consultados. A unidade é resultado de item/fornecedor, não compra nem fornecedor distinto. Município é o da unidade compradora; período é o mês de publicação da compra, não de homologação.

A distribuição abaixo é descritiva da amostra, sem ponderação. Compras com mais itens sorteados ou mais resultados por item pesam mais. Municípios e meses não foram sorteados e os estratos têm frações amostrais diferentes; não extrapolar percentuais para Ceará/Brasil, nem usar intervalo binomial como se os registros fossem independentes. A diversidade de órgãos e fornecedores é observada, não uma cota garantida.

## Distribuição

| Porte | Registros | Percentual |
|---|---:|---:|
| MEI | 0 | 0.00% |
| ME | 40 | 40.40% |
| EPP | 5 | 5.05% |
| Demais Empresas | 47 | 47.47% |
| Não se aplica | 0 | 0.00% |
| Não informado | 7 | 7.07% |

Total: 99 resultados ativos; 57 fornecedores identificados; 16 órgãos; 47 compras com resultados.

## Comparação por município e período

| Município | Período | Total | Não informado | % |
|---|---|---:|---:|---:|
| Amontada | 2025-01 | 0 | 0 | N/D |
| Amontada | 2025-05 | 3 | 1 | 33.33% |
| Amontada | 2025-09 | 3 | 1 | 33.33% |
| Fortaleza | 2025-01 | 9 | 0 | 0.00% |
| Fortaleza | 2025-05 | 7 | 0 | 0.00% |
| Fortaleza | 2025-09 | 11 | 0 | 0.00% |
| Juazeiro do Norte | 2025-01 | 9 | 5 | 55.56% |
| Juazeiro do Norte | 2025-05 | 17 | 0 | 0.00% |
| Juazeiro do Norte | 2025-09 | 14 | 0 | 0.00% |
| Sobral | 2025-01 | 2 | 0 | 0.00% |
| Sobral | 2025-05 | 11 | 0 | 0.00% |
| Sobral | 2025-09 | 13 | 0 | 0.00% |

As distribuições completas das seis categorias por município, período, município/período, órgão, modalidade e fornecedor estão nos CSVs adjacentes. Estratos vazios/falhos e compras sem resultados constam no manifesto, nunca como porte ausente. N/D indica denominador zero, não cobertura de 100%.

## Qualidade e conclusão

Das 67 compras sorteadas, 47 têm resultados elegíveis nos itens amostrados e 20 não têm. Foram consultados 154 itens sorteados; 50 retornaram lista de resultados vazia. Isso limita a observabilidade de fornecedores e é distinto da ausência do campo porte.

Alerta semântico: 3 resultados identificam o fornecedor como pessoa física (PF), mas atribuem MEI, ME, EPP ou Demais Empresas. O catálogo prevê Não se aplica para situações como pessoas físicas. Revisar a consistência da declaração sem reclassificar automaticamente; os valores originais permanecem preservados e o recorte por tipo de pessoa está no CSV.

Motivos de classificação: {"valido": 92, "codigo_5": 7}. 'Não informado' agrega código 5, campo ausente/nulo/vazio e código inválido; os motivos permanecem separados em resultados.csv e no manifesto. 'Não se aplica' permanece categoria própria e integra o denominador total.

Falhas de coleta: 0; resultados cancelados excluídos: 5. Falhas não entram no denominador. Consultas e respostas bem-sucedidas estão em raw/, com URL e horário. Nova execução no mesmo diretório reutiliza o cache; use outro diretório para uma nova fotografia. Os números refletem os dados disponíveis na coleta, sujeitos a retificação.

Fornecedores com mais de uma categoria: 0. Essa divergência pode decorrer de datas distintas ou declaração inconsistente e não demonstra, sozinha, erro cadastral. Cobertura não comprova exatidão: não foi feita validação contra a base CNPJ.

Na amostra, **7.07% (7/99)** dos resultados não possuem porte utilizável. O campo pode integrar o processo como informação complementar, preservando fonte e data. O enriquecimento pela base CNPJ continua necessário para validação e preenchimento; a amostra não autoriza sua substituição.

O catálogo consultado informa inclusão do código 6 (MEI) em 2026-06-11. Zero MEI neste recorte de 2025 não significa inexistência de MEIs; não inferir MEI a partir de ME. Para avaliar adoção do novo código, repetir com períodos posteriores à inclusão.

Próximo passo para decisão abrangente: ampliar municípios e meses, planejar precisão considerando conglomerados e pesos e confrontar CNPJs com referência temporal compatível. Não há limiar de aprovação de cobertura definido pelo produto.

## Fontes

- [Catálogo oficial consultado](https://pncp.gov.br/api/pncp/v1/portes-empresa).
- [Consulta de porte no manual PNCP](https://pncp.gov.br/manual/pt-br/latest/tabelas_de_dominio/consultar_porte_de_empresa.html).
- [Resultado de item no manual PNCP](https://pncp.gov.br/manual/pt-br/latest/contratacao/inserir_resultado_do_item_de_uma_contratacao.html).

## Reprodução

```powershell
.venv/Scripts/python.exe scripts/spike_pncp_porte.py --output docs/spikes/pncp-porte
.venv/Scripts/python.exe -m unittest discover -s tests -p test_spike_pncp_porte.py
```
