#!/bin/sh
# backup.sh
# ---------------------------------------------------------------------------
# DEPLOY (varredura, item B1): dump do banco + envio CIFRADO para fora do
# servidor, com alerta se falhar. Antes só existia o comando manual do README
# ("Backup e a chave de criptografia"): dependia de alguém lembrar de rodar
# e testar. Este script automatiza esse mesmo comando e soma retenção +
# alerta; falta só AGENDAR (cron ou systemd timer, exemplos no README) e
# preencher RESTIC_REPOSITORY/RESTIC_PASSWORD no ambiente de quem agenda.
#
# Roda no HOST (onde `docker compose` e o `restic` estão instalados), nunca
# dentro de um container: é o mesmo lugar de onde hoje se roda o dump manual.
# A chave FERNET_KEY não entra aqui e não deve: ela cifra os CAMPOS no banco;
# a senha do restic cifra o ARQUIVO do backup. As duas são guardadas
# separadas, no cofre de segredos (nunca junto do próprio backup).
#
# Variáveis obrigatórias (fail-closed: sem elas, o script para antes de gerar
# qualquer dump — melhor isso do que um backup sem destino cifrado):
#   RESTIC_REPOSITORY     repositório restic de destino, por exemplo:
#                           s3:https://s3.sa-east-1.amazonaws.com/meu-bucket
#                           b2:meu-bucket:psigestao
#                         (bucket na MESMA região do servidor; ver lgpd.md item 11)
#   RESTIC_PASSWORD (ou RESTIC_PASSWORD_FILE)
#                         senha que cifra o backup ANTES de ele saber do servidor.
#                         Credenciais do próprio provedor (ex.: AWS_ACCESS_KEY_ID)
#                         seguem as variáveis de ambiente que o restic já aceita.
#
# Variáveis opcionais:
#   BACKUP_HEALTHCHECK_URL   ping de sucesso/falha (healthchecks.io ou
#                            equivalente); sem rodar ou com falha, vira alerta.
#   BACKUP_KEEP_WEEKLY / BACKUP_KEEP_MONTHLY / BACKUP_KEEP_YEARLY
#                            retenção (padrão 8/12/5 — cobre a guarda legal
#                            de 5 anos do CFP com folga; ajuste se o prazo
#                            de guarda dos seus dados for diferente).
#   POSTGRES_USER / POSTGRES_DB   só se diferentes do padrão do docker-compose.yml.
#
# Uso (a partir da pasta com o docker-compose.yml):
#   ./backup.sh
#
# Exemplo de agendamento (cron, todo dia às 3h, com log):
#   0 3 * * * cd /caminho/do/psigestao && RESTIC_REPOSITORY=... RESTIC_PASSWORD=... \
#     BACKUP_HEALTHCHECK_URL=https://hc-ping.com/seu-id ./backup.sh >> /var/log/psigestao-backup.log 2>&1
# ---------------------------------------------------------------------------
set -eu

if [ -z "${RESTIC_REPOSITORY:-}" ]; then
  echo "backup.sh: defina RESTIC_REPOSITORY (repositório restic de destino). Nada foi feito." >&2
  exit 1
fi
if [ -z "${RESTIC_PASSWORD:-}" ] && [ -z "${RESTIC_PASSWORD_FILE:-}" ]; then
  echo "backup.sh: defina RESTIC_PASSWORD ou RESTIC_PASSWORD_FILE (senha que cifra o backup). Nada foi feito." >&2
  exit 1
fi
export RESTIC_REPOSITORY

KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-8}"
KEEP_MONTHLY="${BACKUP_KEEP_MONTHLY:-12}"
KEEP_YEARLY="${BACKUP_KEEP_YEARLY:-5}"
DB_USER="${POSTGRES_USER:-admin}"
DB_NAME="${POSTGRES_DB:-psigestao}"

# O dump (prontuários dentro) fica num diretório temporário só deste script e
# é apagado ao sair, com sucesso ou erro — nunca sobra em disco.
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
DUMP_FILE="$TMP_DIR/psigestao_$(date +%Y%m%d_%H%M%S).dump"

# $1 = "" (sucesso) ou "/fail" (Healthchecks.io e compatíveis usam este sufixo).
ping_healthcheck() {
  [ -n "${BACKUP_HEALTHCHECK_URL:-}" ] || return 0
  curl -fsS -m 10 --retry 3 "${BACKUP_HEALTHCHECK_URL}${1}" -o /dev/null || true
}

echo "backup.sh: gerando o dump do banco (docker compose exec db pg_dump)..."
if ! docker compose exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom > "$DUMP_FILE"; then
  echo "backup.sh: falha ao gerar o dump do banco." >&2
  ping_healthcheck "/fail"
  exit 1
fi

echo "backup.sh: enviando para o repositório restic (cifrado antes de saber do servidor)..."
if ! restic backup "$DUMP_FILE" --tag psigestao-db --host psigestao; then
  echo "backup.sh: falha ao enviar o backup ao repositório restic." >&2
  ping_healthcheck "/fail"
  exit 1
fi

# Retenção: manter mais versões do que o necessário não ajuda (LGPD arts.
# 15-16, minimização também vale para backup) nem cobre menos que a guarda
# legal. Falha aqui não é tratada como falha do backup de hoje (ele já foi
# salvo); só avisa, para alguém checar o espaço/permissões do repositório.
if ! restic forget --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY" \
    --keep-yearly "$KEEP_YEARLY" --prune; then
  echo "backup.sh: aviso: o backup de hoje foi salvo, mas a limpeza de versões antigas (forget/prune) falhou." >&2
fi

echo "backup.sh: concluído."
ping_healthcheck ""
