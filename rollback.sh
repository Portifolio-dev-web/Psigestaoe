#!/bin/sh
# rollback.sh
# ---------------------------------------------------------------------------
# DEPLOY: reverte a produção para um commit anterior (simétrico ao deploy.sh).
# Roda no HOST (dentro da pasta do projeto na VPS), não em container.
#
# O que faz, em ordem:
#   1. dump de segurança (antes de tocar em qualquer coisa);
#   2. git checkout para o commit/tag pedido;
#   3. reconstrói as imagens dessa versão;
#   4. sobe de novo;
#   5. espera a API ficar saudável.
#
# NÃO restaura o banco automaticamente: se a migração da versão revertida
# alterou o schema de forma incompatível com a versão anterior, isso exige
# julgamento humano (qual dump restaurar, o que fazer com dados gravados
# nesse meio tempo) — ver docs/deploy-vps.md, seção 13, e o README.md para
# o comando de pg_restore.
#
# Uso: ./rollback.sh <commit-ou-tag-anterior>
#   Ex.: ./rollback.sh HEAD~1
#   Ex.: ./rollback.sh a1b2c3d
#   Veja commits recentes antes: git log --oneline -10
# ---------------------------------------------------------------------------
set -eu

if [ $# -ne 1 ]; then
  echo "Uso: ./rollback.sh <commit-ou-tag-anterior>" >&2
  echo "Veja commits recentes: git log --oneline -10" >&2
  exit 1
fi
TARGET="$1"

COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"
DUMP_DIR="./_deploy_dumps"
CURRENT=$(git rev-parse --short HEAD)

mkdir -p "$DUMP_DIR"
echo "rollback.sh: dump de segurança antes de reverter..."
DUMP_FILE="$DUMP_DIR/pre_rollback_$(date +%Y%m%d_%H%M%S).dump"
if $COMPOSE exec -T db pg_dump -U admin -d psigestao --format=custom > "$DUMP_FILE" 2>/dev/null; then
  echo "rollback.sh: dump salvo em $DUMP_FILE"
  ls -1t "$DUMP_DIR"/pre_rollback_*.dump 2>/dev/null | tail -n +6 | xargs -r rm --
else
  echo "rollback.sh: aviso: não consegui gerar o dump de segurança (banco fora do ar?)." >&2
  rm -f "$DUMP_FILE"
  DUMP_FILE=""
fi

echo "rollback.sh: revertendo de $CURRENT para $TARGET..."
git checkout "$TARGET"

echo "rollback.sh: construindo as imagens da versão anterior..."
$COMPOSE build

echo "rollback.sh: subindo..."
$COMPOSE up -d

echo "rollback.sh: esperando a API responder saudável..."
tries=0
until $COMPOSE exec -T api python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health/ready', timeout=4).status == 200 else 1)" 2>/dev/null; do
  tries=$((tries + 1))
  if [ "$tries" -ge 20 ]; then
    echo "rollback.sh: a API não respondeu saudável a tempo mesmo após o rollback. Confira os logs: $COMPOSE logs api" >&2
    if [ -n "$DUMP_FILE" ]; then
      echo "rollback.sh: para desfazer este rollback e restaurar o estado de antes dele:" >&2
      echo "  cat $DUMP_FILE | $COMPOSE exec -T db pg_restore -U admin -d psigestao --clean --if-exists" >&2
    fi
    exit 1
  fi
  sleep 3
done

echo "rollback.sh: concluído. HEAD agora em $(git rev-parse --short HEAD) (estava em $CURRENT)."
echo "rollback.sh: se a migração da versão revertida alterou o schema de forma"
echo "  incompatível com esta versão anterior, restaure manualmente o dump de"
echo "  ANTES do deploy problemático (não o que acabei de gerar, que já é da"
echo "  versão com problema) — ver docs/deploy-vps.md, seção 13."
echo "rollback.sh: para voltar a 'main' sem subir de novo: git checkout main"
$COMPOSE ps
