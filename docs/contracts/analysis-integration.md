# Indicador publico de ME ? integracao 0.9.1

Referencia: `contrato de dados.md` fornecido pelo usuario, versao 0.9.1.
Esta implementacao cobre a Visao Geral publica necessaria ao indicador.
Nao cria endpoints restritos nem implementa a disposicao dos cards no frontend.

## Rota e consumo

`GET /api/v1/analysis/public/overview`

Datas `start_date` e `end_date` sao obrigatorias, em `YYYY-MM-DD`, inclusivas.
Filtros: `uf` (padrao CE), `municipality_ibge_code`, `expense_element_codes`
e `supplier_origins`. Arrays usam parametros repetidos; omissao ou o literal
`[]` significa todos os valores do escopo. A resposta expande os arrays.
Parametros desconhecidos, valores fora do escopo e quaisquer filtros de porte
sao rejeitados com 422. Municipio inexistente no catalogo da carga retorna 404.

O frontend deve usar:

- `kpis.me_committed_net_cents`: empenhado liquido exclusivo de ME.
- `kpis.me_share_of_all_purchases_rate`: `null` enquanto o gate estiver pendente.
- `kpis.me_share_of_all_purchases_status`: `PENDING_PRODUCT_LEGAL_APPROVAL`.
- `meta.calculation_rule`: regra explicita do indicador.

A taxa pendente nao deve aparecer como 0%, nem ser substituida pela taxa de MPE
ou por uma taxa do indicador de capa. A posicao ao lado do indicador de capa
continua sendo uma alteracao no repositorio do frontend.

A rota antiga `/pipeline/tce/kpis/me-por-mes` retorna 410: valores contratuais
nao sao a base contabil especificada. Os helpers legados de contratos ficam
apenas para compatibilidade interna e nao alimentam a nova rota.

## Carga canonica

`ANALYTICS_SNAPSHOT_PATH` aponta para JSON UTF-8 sem BOM validado por
`analytics-snapshot.schema.json`. `ANALYTICS_PREVIOUS_SNAPSHOT_PATH` e opcional.
Nao configure dados de exemplo como carga de producao.

O arquivo deve ser produzido pelo adaptador de ingestao apos confirmar o
dicionario de eventos TCE. `event_dictionary_version` identifica esse mapeamento.
A implementacao nao presume que `valor_empenhado` seja original, reforco ou
saldo atualizado, nem infere eventos pela repeticao de uma nota.

Cada empenho inclui a chave composta (municipio TCE, exercicio, orgao, unidade,
data de emissao e numero), municipio IBGE + UF, CNPJ do estabelecimento,
porte, flag MEI, elemento de seis digitos e eventos normalizados. Cada evento
possui ID estavel dentro do empenho, data civil, tipo ORIGINAL/REINFORCEMENT/
CANCELLATION e valor inteiro em centavos. Uma anulacao deve ser associada a
chave composta completa pelo adaptador; IDs nao podem depender apenas do
numero do empenho. Apenas anulacoes validas devem entrar na carga.

Existe exatamente um original. Eventos iguais repetidos contam uma vez.
Empenhos iguais repetidos contam uma vez. Duplicatas conflitantes, mapeamentos
municipais invalidos e saldo liquido negativo rejeitam a carga e geram log de
anomalia. Valores nao sao truncados nem inferidos. Para fontes em reais, use
`brl_to_cents` e leia JSON com `parse_float=Decimal`; float nao e aceito.

O recorte seleciona a data de emissao do empenho; seus eventos sao considerados
ate `end_date`, limitados a `data_as_of`. Datas canonicas devem ser normalizadas
pelo adaptador no fuso America/Fortaleza. A entrada nao aceita timestamps no
lugar de datas civis. Liquidacoes e pagamentos nao integram o schema.

MEI tem precedencia sobre porte ME. Todos os agregados publicos filtram ME
antes de somar. O total de todos os portes nem sequer e calculado nessa rota.
O helper interno por porte adota MPE = ME + EPP, mantendo MEI separado.

A carga deve informar catalogo municipal, dimensao de elementos autorizados,
cobertura por municipio/mes, fontes efetivamente utilizadas, data de referencia
e horario UTC da ultima carga valida. Ausencia de cobertura significa NO_DATA.
Meses sem carga possuem valor null na serie; municipios sem carga sao mantidos
com data_available=false, zero monetario e taxas null, seguindo a secao 2.8.
Taxas com denominador zero sao null. O periodo anterior e deslocado em um ano,
com 29/02 ajustado para 28/02. Variacao de participacao fica null junto do gate.

A participacao de um municipio no total estadual e null quando a consulta esta
filtrada em um municipio: nao e substituida silenciosamente por 100%.

## Disponibilidade e seguranca

Sem carga valida: 503 NO_VALID_LOAD, nunca uma resposta de sucesso com zeros
inventados. Carga atual invalida e anterior valida: 200 com is_stale=true e
stale_reason=CURRENT_LOAD_UNAVAILABLE. Os horarios e fontes sao os da carga,
nao sao fabricados a partir da data da requisicao.

Configure `ANALYTICS_CORS_ORIGINS` com origens explicitas separadas por virgula.
Wildcard nao e habilitado. Ha limite local de 60 requisicoes/minuto por IP;
em producao com varios workers, o ingress deve aplicar o limite global.
Nenhum CPF, nome, endereco, telefone ou documento de fornecedor e exposto.
Os schemas rejeitam campos extras. Nao ha cache compartilhado nesta entrega.

## Pendencias externas

O contrato fornecido explicita gates para confirmar o dicionario de eventos
TCE e disponibilidade historica do porte. A rota esta pronta para uma carga
canonizada; a ingestao bruta atual do repositorio continua sem esse mapeamento.
Portanto, a API respondera 503 ate que uma carga valida seja disponibilizada.

O schema publico foi gerado dos modelos desta implementacao para revisao;
nao representa aprovacao formal dos schemas mencionada no documento.
Produto/Juridico ainda precisa decidir sobre exposicao da taxa publica.

Documentacao de origem consultada para verificar a lacuna do adaptador:
[OpenAPI TCE-CE SIM](https://api-dados-abertos.tce.ce.gov.br/sim/openapi_prod.yaml).
Os endpoints de notas e anulacoes exigem exercicio_orcamento e data_referencia_doc,
nao o mesmo contrato de consulta por periodo usado para contratos.
