# PEN-005 — Ceará, ano completo de 2025

**Status: coleta incompleta. PEN-005 ainda não concluído.**

População confirmada: contratações publicadas no PNCP em 2025, com unidade compradora no Ceará, abrangendo todos os municípios e modalidades disponíveis.

## Implementado e validado

- Cadastro: 76 estratos de trimestre × modalidade.
- Universo identificado: 55270 contratações.
- Dimensionamento: 383 compras; referência AAS de 95% de confiança, p=0,5 e ±5 p.p., com correção para população finita.
- Sorteio sem reposição por posições em todas as páginas, com semente e probabilidades preservadas em plano_amostral.csv. Não há seleção intencional de municípios.
- Até dois itens sorteados por compra; estimativa ponderada por probabilidade de inclusão.
- Variância e IC95% por linearização, considerando os dois estágios e população finita.
- Teste matemático por enumeração exata dos 27 sorteios de uma população de teste; a referência de ±5 p.p. não é uma precisão já alcançada pelos resultados.

## Impedimento observado

Etapa interrompida: paginas_das_compras_sorteadas.
- ReadTimeout: HTTPSConnectionPool(host='pncp.gov.br', port=443): Read timed out. (read timeout=60)

Diagnóstico adicional da API: HTTP 500 — Erro na comunicação com o banco de dados. Evidência registrada em diagnostico_api.json.

Não foram produzidos percentuais populacionais nem intervalos de confiança reais para esta rodada incompleta. Os 7,07% do piloto de 99 resultados continuam sendo apenas descritivos daquele recorte e não representam Ceará/2025.

## Para concluir

Executar novamente quando a consulta de contratações do PNCP responder. A rotina reaproveita o cache, conclui as páginas sorteadas e os resultados dos itens, revalida o cadastro e somente então gera as estimativas e marca a execução como concluída.

```powershell
python -m scripts.spike_pncp_representatividade --output docs/spikes/pncp-ce-2025
python -m unittest discover -s tests
```

O requisito de consulta sobre amostra representativa depende da conclusão da coleta. O plano estatístico implementado, isoladamente, não satisfaz esse requisito.
