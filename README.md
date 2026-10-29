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
# --ufs: ver 'Estados cobertos' abaixo (o script diário define a lista)
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

## As fontes

| Fonte | Como funciona | Observações |
|---|---|---|
| **Sold** (`scraper/sold.py`) | API pública da Superbid (`offer-query.superbid.net`), JSON estruturado, sem proteção anti-bot. | Fonte mais rica: já vem com `referenceValue` (avaliação) e `currentMinBid` (lance mínimo real). |
| **Zukerman/Zuk** (`scraper/zukerman.py`) | HTML estático via `requests` + BeautifulSoup. | Não expõe "valor de avaliação" na listagem — preencher manualmente na análise fina. Pega só a primeira leva de resultados por estado (ver limitações). |
| **Caixa** (`scraper/caixa.py`) | Baixa o CSV oficial (`Lista_imoveis_<UF>.csv`) publicado pela Caixa. | **Precisa do Playwright com uma janela de navegador de verdade** (`headless=False`) — o domínio usa Radware Bot Manager e bloqueia `requests`/`curl` e até Chromium headless. |
| **Leilão Imóvel** (`scraper/leilaoimovel.py`) | HTML estático do agregador `meuarremateleiloes.com.br` (leilaoimovel.com.br), sem proteção anti-bot. | Cobre Banco do Brasil, Itaú, Bradesco e Santander (ver `BANCOS_SLUG`) — nenhum desses bancos tem portal próprio de leilão como a Caixa, usam leiloeiros terceirizados. Esse agregador parece ser do mesmo grupo da Priscila Perini/Smart Leilões (assets em `/img/perini/...`). Mistura "Venda Direta" (preço fixo, sem avaliação de banco pra comparar — só o BB expõe isso hoje nesse agregador) e "Leilão Extrajudicial" (com desconto vs. avaliação) — cada imóvel vem marcado com `modalidade`. |
| **Smart Leilões Caixa** (`scraper/smartleiloes.py`) | API JSON não documentada (App Engine, achada inspecionando o bundle JS do site Angular em `smartleiloescaixa.com.br` — `POST /imovel/busca` com `{estados, max, offset}`). | **Não é uma fonte de imóveis novos** — revende os MESMOS imóveis da Caixa (`origemIntegracao: "CAIXA"`, `hdnImovel` = o mesmo `id_no_site` que `scraper/caixa.py` já usa). Por isso não entra no `--fontes` como um scraper comum: `cli.py atualizar` usa os registros pra **enriquecer** (`db.enriquecer_imovel`/`backfill_se_vazio`) os imóveis `caixa:<id>` já existentes com campos que a Caixa não publica (quartos, garagem, lat/lng, aceita FGTS/consórcio/financiamento/parcelamento, pendência de ação judicial, modalidade de venda original) e só insere como novo quando o `hdnImovel` realmente não existe ainda na nossa base (gap de cobertura, ~90% se sobrepõe ao `caixa.py` numa amostra SP/MG). **Cuidado**: os ids "achados" pelo Smart Leilões (sejam enriquecimento ou inserção nova) precisam entrar em `ids_vistos_por_fonte["caixa"]` antes da checagem de encerrados-por-ausência — já foi um bug real aqui (imóveis novos inseridos e fechados na mesma execução). `quartos`, `garagem`, `aceitaConsorcio`, `aceitaParcelamento` e `temAcaoJudicial` quase sempre vêm zerados/nulos na prática (dado pouco preenchido pela fonte, não é bug nosso). |

## Estados cobertos

A lista vive em `run_daily.sh` (`ESTADOS`). Medição de 28/10 (Caixa, residencial/terreno):
SP+MG 3.266 imóveis, país inteiro 22.289 (6,8x). **Brasil todo não cabe** no
formato atual: o banco passaria de ~150 MB (o GitHub rejeita arquivo > 100 MB) e
o dashboard, que renderiza todas as linhas no navegador, levaria ~5 s por filtro
com >2 GB de memória.

- **Bloco A (ativo desde 29/10):** todos menos GO, PE e RJ. Medido: base foi de
  3,9 mil pra 11,1 mil imóveis ativos (2,8x); `leiloes.db` 24,9 -> 35,4 MB
  (5,1 MB gzip); `dashboard.html` 8,8 -> 24,4 MB; no navegador a carga leva ~2,4 s,
  148 MB de memória, e o perfil mais pesado (Leilão, 5,8 mil linhas) abre em ~1,4 s.
  Análise inicial: 2.831 consultas de comparáveis em ~104 min ativos (2,2 s cada).
  Fora de SP/MG o valor de venda vem da avaliação da Caixa em ~84% dos imóveis.
- **Bloco B, GO (ativo desde 29/10):** base foi de 11,1 mil pra 15,4 mil imóveis
  ativos (GO sozinho: 3.683, o maior estado: 755 leilão, 1.061 venda online, 1.867
  venda direta); oportunidades 2.429 -> 4.309; `leiloes.db` 35,4 -> 44,9 MB;
  `dashboard.html` 24,4 -> 36,0 MB; carga no navegador ~2,7 s, 68 MB de memória,
  14,3 mil linhas na tabela. Ainda sem paginação.
- **Bloco B, PE (ativo desde 29/10):** base foi de 15,4 mil pra 16,7 mil imóveis
  ativos (PE: 257 leilão, 241 venda online, 676 venda direta); oportunidades
  4.309 -> 4.843; `leiloes.db` 44,9 -> 47,0 MB; `dashboard.html` 36,0 -> 38,9 MB;
  carga no navegador ~3,0 s, 74 MB de memória. Ainda sem paginação; o limite
  prático fica em ~20 mil linhas.
- **RJ: 100% fora, de propósito** (8.414 imóveis, 38% do país). Não adicione.

Comparáveis de mercado (QuintoAndar): ~3,4 s por região na primeira vez (cache de
30 dias). `resolver_localizacao` consulta a cidade uma vez por execução e pula os
bairros das que o QuintoAndar não conhece — sem isso, os estados novos custavam ~2h
de consultas inúteis (cobertura fora de SP/MG é baixa: 1 de 8 na amostra).

## Modalidades e o modelo de custo (campo `modalidade`)

A Caixa vende imóvel de 5 jeitos diferentes, com custos diferentes — o campo
`modalidade` (em `imoveis`) junta isso em 3 buckets, cada um com seu próprio
caminho de cálculo em `calculator/screening.py::analise_rapida`:

| `modalidade` | `praca` original (Caixa) | Leiloeiro? | Comissão | Referência de valor |
|---|---|---|---|---|
| `leilao` (default) | 1º Leilão, 2º Leilão, Licitação Aberta | Sim, credenciado | 5% sobre o lance | Avaliação do banco |
| `venda_online` | Venda Online (disputa com cronômetro no portal da Caixa) | Não | 0% | Avaliação do banco (mesma referência do leilão) |
| `venda_direta` | Venda Direta / Compra Direta (preço fixo, 1ª proposta válida vence) | Não | 0% | Comparável de mercado (QuintoAndar); sem comparável (ou implausível), **avaliação da Caixa** com haircut de 5% |

Pra imóveis da **Caixa** a modalidade vem da coluna **"Modalidade de venda" do CSV
oficial** (`scraper/caixa.py::MODALIDADE_CSV`: "Leilão SFI - Edital Único" e
"Licitação Aberta" -> `leilao`, "Venda Online" -> `venda_online`, "Venda Direta
Online" -> `venda_direta`) — coluna 100% preenchida, validada em 27/10 contra o
cabeçalho de 50 páginas de detalhe (Leilão SFI/Licitação Aberta têm leiloeiro;
Venda Online e Compra Direta não). Rótulo desconhecido não reclassifica nada
(`modalidade=""`, o upsert mantém a que já existe). **Não use o Smart Leilões
pra isso**: ele oscila ±20% por dia e tem defasagem — quando mandava na
modalidade, 5% da base da Caixa (180 imóveis) ficou no modelo de custo errado
(a Caixa promove Venda Online -> Compra Direta com o tempo). Smart Leilões
(`scraper/smartleiloes.py`) segue só enriquecendo e achando imóveis novos.
Leilão Imóvel (`scraper/leilaoimovel.py`) marca `venda_direta` direto pros
imóveis de Itaú/Bradesco/Santander/BB sem avaliação de banco.

**Referência de valor na venda direta:** 1) comparável de mercado (QuintoAndar);
2) se não houver comparável — ou se ele passar de `DESVIO_MAXIMO_VS_AVALIACAO`
(1,5x) a avaliação, o que indica bairro heterogêneo (sem essa trava, os 8 maiores
ROI da venda direta da Caixa eram artefatos, comparável a 2-3,5x a avaliação) —
a **avaliação da Caixa** com o mesmo haircut de 5% do leilão. Isso é legítimo
porque, por contrato, o engenheiro avaliador da Caixa precisa anexar pesquisa de
mercado que valide a avaliação. Sem comparável e sem avaliação (venda direta do
BB) não há referência: o imóvel fica sem ROI.

Quando a modalidade de um imóvel já existente muda (`upsert_listing` ->
`db.sincronizar_modalidade`, pra qualquer fonte),
a análise antiga é apagada e o imóvel volta pra `status='novo'` — ela foi
calculada com o modelo errado, então mantê-la seria mostrar um ROI incorreto
no dashboard até a próxima análise rodar. **Não dá pra confiar que o ROI de um
imóvel reflete a modalidade atual sem rodar `cli.py analisar` depois de
qualquer mudança de modalidade.**

## Limitações conhecidas (v1)

- **Caixa não roda headless** — só na sua máquina, com uma janela abrindo.
  `cli.py atualizar` roda o scraper como subprocesso isolado
  (`_buscar_caixa_isolado`) com até 3 tentativas e espera crescente entre elas.
  **Causa das falhas intermitentes (investigada em 27/10):** a página de busca
  dispara pixels de analytics de terceiros (Microsoft Clarity/Bing, DoubleClick)
  que às vezes ficam pendurados; o scraper esperava `networkidle` (zero
  requisições em andamento), que nunca chegava e estourava os 30s mesmo com a
  página pronta em ~1,6s. Reproduzido: 10 aberturas ok em ~3,3s e as 2
  seguintes estouraram. Agora espera só `load` (8/8 coletas completas no mesmo
  período em que `networkidle` falhava). O download de cada CSV também tem
  timeout próprio e **falha alto** se uma UF não baixar (antes devolvia só as
  UFs que deram certo como se fosse sucesso, e o resto era fechado como "sumiu
  da fonte"). Se a coleta da Caixa falhar mesmo assim, a checagem de
  encerrados dela é pulada (ver `cmd_atualizar`).
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
