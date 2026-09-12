# Leilões de Imóveis

Ferramenta para buscar oportunidades de leilão de imóveis (Caixa, Zukerman/Zuk,
Sold), calcular o retorno esperado de cada uma e acompanhar tudo num dashboard.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium   # só necessário para o scraper da Caixa
```

## Uso

```bash
# Busca imóveis nas fontes configuradas e salva no banco (data/leiloes.db)
.venv/bin/python cli.py atualizar --fontes zukerman sold --ufs SP

# A Caixa precisa de uma janela de navegador real (não funciona headless nem
# em servidor sem display — veja "Limitações" abaixo)
.venv/bin/python cli.py atualizar --fontes caixa --ufs SP

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
analisar`) usa premissas genéricas (reforma = 8% da avaliação, haircut de 5%
na venda, 8 meses de posse) só para ranquear os imóveis raspados — sempre
refaça a conta com números reais (reforma orçada, condomínio/IPTU reais,
matrícula analisada) antes de decidir dar um lance.

## As três fontes

| Fonte | Como funciona | Observações |
|---|---|---|
| **Sold** (`scraper/sold.py`) | API pública da Superbid (`offer-query.superbid.net`), JSON estruturado, sem proteção anti-bot. | Fonte mais rica: já vem com `referenceValue` (avaliação) e `currentMinBid` (lance mínimo real). |
| **Zukerman/Zuk** (`scraper/zukerman.py`) | HTML estático via `requests` + BeautifulSoup. | Não expõe "valor de avaliação" na listagem — preencher manualmente na análise fina. Pega só a primeira leva de resultados por estado (ver limitações). |
| **Caixa** (`scraper/caixa.py`) | Baixa o CSV oficial (`Lista_imoveis_<UF>.csv`) publicado pela Caixa. | **Precisa do Playwright com uma janela de navegador de verdade** (`headless=False`) — o domínio usa Radware Bot Manager e bloqueia `requests`/`curl` e até Chromium headless. |

## Limitações conhecidas (v1)

- **Caixa não roda headless.** Isso significa que não dá pra rodar esse
  scraper específico num cron/servidor sem display gráfico — só na sua
  máquina, com uma janela abrindo. Se isso virar um problema, dá pra investigar
  automação com perfil de browser persistente ou aceitar rodar manualmente de
  vez em quando.
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

- Agendar `atualizar` + `analisar` + `build_dashboard` como uma tarefa
  recorrente (ex: diária) para acompanhar novas oportunidades automaticamente.
- Adicionar mais sites (Resale, Mega Leilões, Superbid direto) seguindo o
  mesmo padrão: um módulo em `scraper/`, retornando `db.store.Listing`.
- Guardar o histórico de preço/lance por imóvel ao longo do tempo (hoje o
  `upsert_listing` sobrescreve os valores a cada atualização).
