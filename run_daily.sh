#!/bin/bash
# Roda a atualização diária do projeto leiloes-imoveis: busca (zukerman, sold, caixa),
# filtra comerciais, analisa, gera o dashboard e sobe pro GitHub (mantém o
# GitHub Pages atualizado). Pensado pra rodar via launchd (ver com.luizfelipe.leiloes-imoveis.plist).
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:$PATH"

DIR="/Users/luizfelipe/leiloes-imoveis"
cd "$DIR"

LOG="$DIR/logs/run_$(date +%Y-%m-%d_%H-%M-%S).log"
mkdir -p "$DIR/logs"

{
  echo "=== Início: $(date) ==="

  # Blocos A e B: todos os estados MENOS RJ (que fica
  # fora de propósito: ~8,4 mil imóveis da Caixa, 38% do país, não cabe no
  # dashboard/banco atuais — ver README, "Estados cobertos").
  ESTADOS="SP MG AC AL AM AP BA CE DF ES GO MA MS MT PA PB PE PI PR RN RO RR RS SC SE TO"
  "$DIR/.venv/bin/python" cli.py atualizar --fontes zukerman sold caixa leilaoimovel smartleiloes --ufs $ESTADOS
  "$DIR/.venv/bin/python" cli.py analisar
  "$DIR/.venv/bin/python" dashboard/build_dashboard.py

  if [ -n "$(git status --short data/leiloes.db dashboard.html)" ]; then
    git add data/leiloes.db dashboard.html
    git commit -m "Atualização automática de oportunidades ($(date +%Y-%m-%d))"
    git push
    echo "Commit e push feitos."
  else
    echo "Nada mudou, sem commit."
  fi

  echo "=== Fim: $(date) ==="
} >> "$LOG" 2>&1

# mantém só os últimos 30 logs (xargs -r não existe no BSD/macOS, por isso o loop)
ls -t "$DIR"/logs/run_*.log 2>/dev/null | tail -n +31 | while IFS= read -r f; do rm -f "$f"; done
