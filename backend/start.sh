#!/bin/sh
# start.sh: ponto de entrada de PRODUÇÃO do container da API (ESCALA: workers).
#
# 1. prestart.py migra o banco UMA vez (com trava), antes de qualquer worker.
# 2. Prepara a pasta de métricas compartilhada entre os workers (Prometheus).
# 3. Sobe o gunicorn com vários workers (gunicorn.conf.py).
#
# O desenvolvimento não usa este arquivo: o docker-compose.yml troca o
# comando por `uvicorn --reload`.
set -e

python prestart.py

# Cada worker grava as métricas nesta pasta e o /api/metrics soma tudo.
# Tem que começar VAZIA a cada início, senão somaria valores de execuções antigas.
export PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus-metrics}"
rm -rf "$PROMETHEUS_MULTIPROC_DIR"
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"

# exec: o gunicorn vira o processo principal e recebe direto os sinais de
# parada do Docker (desligamento gracioso).
exec gunicorn -c gunicorn.conf.py server:app
