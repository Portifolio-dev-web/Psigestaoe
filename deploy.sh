#!/bin/sh
# deploy.sh
# ---------------------------------------------------------------------------
# DEPLOY (passo a passo em docs/deploy-vps.md): atualiza a produção a partir
# do código já clonado nesta pasta. Roda no HOST (dentro da pasta do projeto
# na VPS), não em container.
#
# O que faz, em ordem:
#   1. dump do banco (segurança: se o deploy quebrar algo, dá para restaurar);
#   2. git pull (pega o código mais novo);
#   3. reconstrói as imagens que mudaram;
#   4. sobe de novo (prestart.py migra o banco sozinho, com trava);
#   5. espera a API ficar saudável;
#   6. mostra o status final.
#
# Uso: ./deploy.sh   (a partir da pasta com o docker-compose.prod.yml)
# Rollback: ver docs/deploy-vps.md, seção "Reverter um deploy".
# ---------------------------------------------------------------------------
set -eu

COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env"
DUMP_DIR="./_deploy_dumps"
mkdir -p "$DUMP_DIR"

echo "deploy.sh: dump de segurança antes de implantar..."
DUMP_FILE="$DUMP_DIR/pre_deploy_$(date +%Y%m%d_%H%M%S).dump"
if $COMPOSE exec -T db pg_dump -U admin -d psigestao --format=custom > "$DUMP_FILE" 2>/dev/null; then
  echo "deploy.sh: dump salvo em $DUMP_FILE"
  # Mantém só os 5 mais recentes (o backup de verdade é o backup.sh, fora do
  # servidor; isto aqui é só uma rede de segurança rápida para ESTE deploy).
  ls -1t "$DUMP_DIR"/pre_deploy_*.dump 2>/dev/null | tail -n +6 | xargs -r rm --
else
  echo "deploy.sh: aviso: não consegui gerar o dump de segurança (banco ainda não existe? primeiro deploy?)." >&2
fi

echo "deploy.sh: baixando o código mais novo..."
git pull --ff-only

echo "deploy.sh: construindo as imagens..."
$COMPOSE build

echo "deploy.sh: subindo (migração do banco roda sozinha, com trava)..."
$COMPOSE up -d

echo "deploy.sh: esperando a API responder saudável..."
tries=0
until $COMPOSE exec -T api python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health/ready', timeout=4).status == 200 else 1)" 2>/dev/null; do
  tries=$((tries + 1))
  if [ "$tries" -ge 20 ]; then
    echo "deploy.sh: a API não respondeu saudável a tempo. Confira os logs: $COMPOSE logs api" >&2
    exit 1
  fi
  sleep 3
done

echo "deploy.sh: concluído."
$COMPOSE ps
