# Leilões de Imóveis

Ferramenta para buscar oportunidades de leilão de imóveis (Caixa, Zukerman/Zuk,
Sold, Banco do Brasil), calcular o retorno esperado de cada uma e acompanhar
tudo num dashboard. Foco atual: imóveis **residenciais e terrenos** (salas e
imóveis comerciais são filtrados automaticamente).

Dashboard ao vivo: https://luizfelipe1989.github.io/leiloes-imoveis/ — atualizado
todo dia às 8h por um LaunchAgent local (`run_daily.sh` + `com.luizfelipe.leiloes-imoveis.plist`
em `~/Library/LaunchAgents`). Não roda na nuvem porque os ambientes de agente
de nuvem disponíveis bloqueiam acesso à internet fora de uma allowlist de
infraestrutura (npm/pypi/GitHub) — nenhum dos sites de leilão está nela.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium   # só necessário para o scraper da Caixa
```

## Uso

```bash
# Busca imóveis nas fontes configuradas e salva no banco (data/leiloes.db)
# --ufs default é SP e MG (nosso foco); passe outras UFs se quiser ampliar
.venv/bin/python cli.py atualizar --fontes zukerman sold --ufs SP MG

# A Caixa precisa de uma janela de navegador real (não funciona headless nem
# em servidor sem display — veja "Limitações" abaixo)
.venv/bin/python cli.py atualizar --fontes caixa --ufs SP MG

# Roda uma triagem automática (premissas padrão) nos imóveis novos
.venv/bin/python cli.py analisar

# Lista os imóveis ordenados por retorno anualizado
.venv/bin/python cli.py listar --limit 30

# Gera o dashboard.html
.venv/bin/python dashboard/build_dashboard.py
```

Abra `dashboard.html` no navegador para ver a tabela filtrável/ordenável de
oportunidades.

## Como funciona a calculadora

`calculator/model.py` implementa o fluxo padrão de um investimento em leilão:

1. **Aquisição**: lance + comissão do leiloeiro (5%) + ITBI (3%) + custas de
   cartório (1,5%) + due diligence + custo de desocupação (se ocupado).
2. **Posse**: reforma + condomínio/IPTU/outros custos mensais × meses até a venda.
3. **Venda**: valor de venda estimado − corretagem (6%) − IR sobre ganho de
   capital (15%).
4. **Retorno**: lucro líquido ÷ investimento total, anualizado por juros
   compostos considerando o período de posse.

Todas as premissas percentuais têm default mas podem ser sobrescritas por
imóvel (`InvestmentInputs`).

A **triagem automática** (`calculator/screening.py`, rodada pelo `cli.py
analisar`) usa premissas genéricas (reforma = 8% da avaliação, 8 meses de
posse) só para ranquear os imóveis raspados — sempre refaça a conta com
números reais (reforma orçada, condomínio/IPTU reais, matrícula analisada)
antes de decidir dar um lance.

### Valor de venda estimado: comparáveis reais ou haircut

Pra estimar por quanto o imóvel venderia depois de reformado, a triagem tenta
primeiro **comparáveis reais de mercado** (`calculator/mercado.py`): busca
imóveis à venda no QuintoAndar no mesmo bairro, tira a mediana de R$/m² e
multiplica pela área do imóvel arrematado. O QuintoAndar tem uma API pública
sem autenticação nem proteção anti-bot (`apigw.prod.quintoandar.com.br`) —
dois passos: resolve o bairro num slug (`/v1/search/location/slug/<slug>`)
pra pegar centro+viewport, depois busca os imóveis à venda naquela área
(`/v3/search/list`).

Isso só funciona quando (a) o imóvel tem área conhecida — Caixa e Zukerman
extraem isso da própria fonte, Sold e Banco do Brasil às vezes não têm — e
(b) o bairro é coberto pelo QuintoAndar (cidades grandes: SP, RJ, BH,
Campinas etc. — não cobre cidade pequena nem terreno). Quando falta uma das
duas coisas, cai de volta no **haircut de 5% sobre a avaliação do banco**
(o comportamento antigo). O campo `fonte_venda_estimada` em cada análise (visível no "ver
cálculo" do dashboard) mostra qual dos dois foi usado.

Os comparáveis também só entram na conta se forem do **mesmo grupo** do imóvel
do leilão (casa vs. apartamento — `calculator.mercado.grupo_tipo`): misturar
apartamento simples com casa de condomínio do lado (preço/m² bem diferente)
foi o primeiro bug real que apareceu aqui, inflando a venda estimada em 2x.
E tem uma trava de sanidade (`DESVIO_MAXIMO_VS_AVALIACAO = 1.5`): se o valor
por comparáveis passar de 1,5x a avaliação do banco, o código desconfia (bairro
oficial grande e heterogêneo — tipo "Butantã" em SP, que vai de área nobre a
região bem mais simples — faz o QuintoAndar "vazar" pra uma sub-região mais
cara) e cai no haircut em vez de confiar cegamente na mediana.

Pra não bater na API do QuintoAndar de novo a cada execução, o preço/m² por
região fica cacheado na tabela `comparaveis_cache` por 30 dias — o cache é
por (estado, cidade, bairro), não por imóvel, já que vários imóveis
costumam cair no mesmo bairro.

```bash
# roda análise só nos imóveis novos (default do dia a dia)
.venv/bin/python cli.py analisar

# reanalisa TUDO (inclusive já analisados) — útil depois de mudar a lógica
# de análise ou depois de popular área em imóveis que não tinham antes.
# Atenção: consulta o QuintoAndar pra cada região ainda não cacheada, então
# pode ser lento na primeira vez com o banco cheio (milhares de imóveis).
.venv/bin/python cli.py analisar --recalcular-tudo
```

## Ciclo de vida do imóvel: `status`

`novo` → `analisado` → (`arrematado` / `vendido`, manual) — e a qualquer momento
pode virar `descartado` (comercial, fora do foco) ou **`encerrado`**, que é o
que resolve leilão que já aconteceu:

- **Por ausência**: todo `cli.py atualizar` compara os imóveis vistos na leva
  atual de cada fonte com o que já está salvo — o que sumiu (arrematado,
  vendido, edital cancelado, etc.) vira `encerrado`. Isso cobre Caixa e Banco
  do Brasil, que não expõem data de leilão.
- **Por data vencida**: Zukerman e Sold trazem data de leilão — se já passou,
  `encerrado` direto, mesmo que o imóvel ainda apareça listado (`cli.py
  limpar-encerrados` roda isso isoladamente, sem precisar rescanear).

Imóveis `encerrado` (assim como `descartado`) somem do dashboard e do
`cli.py listar`, mas continuam no banco — não é um hard delete, só sai da
visão ativa. Se uma fonte falhar na busca (erro de rede, mudança de HTML), a
verificação por ausência **não roda pra ela** naquele ciclo — do contrário
tudo que essa fonte já tinha salvo seria erroneamente marcado como encerrado
só por não termos conseguido buscar de novo.

## As quatro fontes

| Fonte | Como funciona | Observações |
|---|---|---|
| **Sold** (`scraper/sold.py`) | API pública da Superbid (`offer-query.superbid.net`), JSON estruturado, sem proteção anti-bot. | Fonte mais rica: já vem com `referenceValue` (avaliação) e `currentMinBid` (lance mínimo real). |
| **Zukerman/Zuk** (`scraper/zukerman.py`) | HTML estático via `requests` + BeautifulSoup. | Não expõe "valor de avaliação" na listagem — preencher manualmente na análise fina. Pega só a primeira leva de resultados por estado (ver limitações). |
| **Caixa** (`scraper/caixa.py`) | Baixa o CSV oficial (`Lista_imoveis_<UF>.csv`) publicado pela Caixa. | **Precisa do Playwright com uma janela de navegador de verdade** (`headless=False`) — o domínio usa Radware Bot Manager e bloqueia `requests`/`curl` e até Chromium headless. |
| **Leilão Imóvel** (`scraper/leilaoimovel.py`) | HTML estático do agregador `meuarremateleiloes.com.br` (leilaoimovel.com.br), sem proteção anti-bot. | Cobre Banco do Brasil, Itaú, Bradesco e Santander (ver `BANCOS_SLUG`) — nenhum desses bancos tem portal próprio de leilão como a Caixa, usam leiloeiros terceirizados. Esse agregador parece ser do mesmo grupo da Priscila Perini/Smart Leilões (assets em `/img/perini/...`). Mistura "Venda Direta" (preço fixo, sem avaliação de banco pra comparar — só o BB expõe isso hoje nesse agregador) e "Leilão Extrajudicial" (com desconto vs. avaliação) — cada imóvel vem marcado com `modalidade`. |

## Limitações conhecidas (v1)

- **Caixa não roda headless** — só na sua máquina, com uma janela abrindo. E é
  **flaky mesmo assim**: o Chromium às vezes fecha sozinho no meio da
  navegação (`TargetClosedError`), sem uma causa 100% identificada — não é
  sempre reproduzível, então pode ser contenção de recursos do sistema mais do
  que um bug de código. `cli.py atualizar` já roda esse scraper como
  subprocesso isolado (`_buscar_caixa_isolado`, ver comentário no topo de
  `scraper/caixa.py`) e tenta de novo até 3x antes de desistir — na prática
  quase sempre resolve numa das tentativas.
- **Zukerman só pega a primeira leva de resultados por estado** (a listagem
  completa carrega mais via scroll infinito, que ainda não foi mapeado). Para
  cobertura maior de uma cidade específica, use `zukerman.buscar_cidade(uf,
  regiao, cidade_slug)`.
- **Avaliação de mercado nem sempre é confiável.** O valor de "avaliação" que
  vem dos sites é o valor que o banco/leiloeiro atribuiu, não necessariamente
  o valor real de mercado — vale sempre cruzar com anúncios comparáveis
  (Zap, QuintoAndar, OLX) antes de definir `valor_venda_estimado` na análise
  fina.
- **Sites de leilão mudam de estrutura sem aviso.** Se um scraper parar de
  retornar resultados, o mais provável é que o HTML/API mudou — não é um bug
  "escondido", é esperado nesse tipo de projeto e exige ajuste pontual.

## Próximos passos sugeridos

- Achar a venda direta de Itaú/Bradesco/Santander (hoje só o BB expõe isso no
  `meuarremateleiloes.com.br`) — os outros bancos têm venda direta em outro
  lugar do próprio site ou em outra fonte, ainda não mapeado.
- Itaú/Bradesco/Santander aparecem tanto via Zukerman (Zuk) quanto via
  `leilaoimovel.py` — tem sobreposição, vale checar duplicidade por endereço
  antes de crescer mais aqui.
- Adicionar mais sites (Resale, Mega Leilões, Superbid direto) seguindo o
  mesmo padrão: um módulo em `scraper/`, retornando `db.store.Listing`.
- Guardar o histórico de preço/lance por imóvel ao longo do tempo (hoje o
  `upsert_listing` sobrescreve os valores a cada atualização).
