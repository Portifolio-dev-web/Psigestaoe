# CLAUDE.md — Diretrizes Arquiteturais e de Segurança (Psigestaoe)

Este arquivo define o contexto operacional, a arquitetura e as regras de segurança inegociáveis do repositório **Psigestaoe** (sistema de gestão clínica e prontuários para psicólogas, atualmente dimensionado para operação Beta segura e em conformidade com a LGPD e resoluções do CFP).

---

## 1. Visão Geral da Arquitetura

* **Domínio Único (`Caddyfile`)**: O Caddy atua como único ponto de entrada público (portas `80` e `443`), gerenciando TLS automático e roteando `/api/*` para `api:5000` (Gunicorn) e as demais rotas para `web:8080` (Nginx servindo o build estático do React). Isso permite cookies de sessão com `SameSite=Strict`.
* **Isolamento de Rede (`docker-compose.prod.yml`)**: Todos os containers operam na bridge interna `psigestao` (`172.28.0.0/24`). Os serviços `db` (PostgreSQL 16) e `redis` (Redis 7) **nunca** publicam portas no host.
* **Backend (`backend/`)**: Python com Gunicorn, migrações via Alembic (`backend/prestart.py` executa migrações com trava antes de abrir workers), rate limiting apoiado no Redis (`backend/rate_limit.py`) e cabeçalhos de segurança (`backend/http_security.py`).
* **Frontend (`frontend/`)**: React + TailwindCSS + CRACO, compilado em estágio multi-stage e servido via Nginx (`frontend/nginx.conf`) com healthcheck em `/healthz`.

---

## 2. Invariantes de Segurança (NUNCA VIOLAR)

1. **Criptografia de Prontuários e Dados Pessoais (`backend/crypto.py`)**:
   * Campos clínicos e cadastrais sensíveis (CPF, RG, endereço, conteúdo de sessão e diagnóstico) são cifrados em repouso com `MultiFernet` (AES-128-CBC + HMAC-SHA256).
   * **Proibido** remover o comportamento *fail-fast* (`sys.exit(1)`) quando `FERNET_KEY` ou `SEARCH_INDEX_KEY` (mínimo 32 caracteres) estiverem ausentes em `ENVIRONMENT=production`.
   * A rotação de chaves deve sempre usar `FERNET_OLD_KEYS` para leitura e `backend/rotate_encryption_key.py` para recriptografia sem perda de dados.

2. **Índice Cego / Blind Index (`backend/crypto.py` e `backend/rebuild_search_index.py`)**:
   * Buscas por nome, telefone e CPF nunca consultam texto em claro no banco; utilizam tokens HMAC-SHA256 truncados (128 bits) combinados com o `owner_id` na tabela `patient_search_tokens`.
   * Qualquer alteração na lógica de tokenização exige incrementar `SEARCH_INDEX_VERSION` e rodar `backend/rebuild_search_index.py`.

3. **Confiança de Proxy e IP Real (`docker-compose.prod.yml` e `backend/proxy_config.py`)**:
   * O container `caddy` possui IP estático `172.28.0.10` na rede `psigestao`.
   * A variável `FORWARDED_ALLOW_IPS` do serviço `api` deve ser exatamente `172.28.0.10`. Alterar um sem o outro compromete o rate limit e os logs de auditoria por IP.

4. **Proteção de Segredos e Logs (`backend/observability.py` e `.gitleaks.toml`)**:
   * **Nunca** ler, imprimir ou expor no terminal o conteúdo de `.env` ou `backend/.env` (consulte apenas `.env.example`, `backend/.env.example` e `frontend/.env.example`).
   * **Nunca** registrar conteúdo de prontuários, CPFs, tokens ou chaves em logs de aplicação ou eventos do Sentry (`frontend/src/lib/sentry.js`).

5. **Backups e Guarda Legal (`backup.sh` e `deploy.sh`)**:
   * O `backup.sh` opera em modo *fail-closed*: exige `RESTIC_REPOSITORY` e `RESTIC_PASSWORD`, gera o dump em diretório temporário (`mktemp -d`) removido via `trap EXIT` e aplica retenção compatível com a guarda mínima de 5 anos do CFP.

---

## 3. Comandos Principais do Repositório

* **Testes Automatizados (Backend)**:
  ```bash
  pytest
  ```
  *(Configurado via `backend/pytest.ini`, executando a suíte em `tests/test_backend_routes.py`).*

* **Build e Auditoria (Frontend)**:
  ```bash
  cd frontend && yarn build
  python3 .github/scripts/yarn_audit_gate.py
  ```

* **Operação de Produção (VPS)**:
  * Deploy seguro com dump prévio e checagem de `/api/health/ready`: `./deploy.sh`
  * Backup externo cifrado via Restic: `./backup.sh`
  * Subida manual de produção: `docker compose -f docker-compose.prod.yml --env-file .env up -d --build`

* **Manutenção e Scripts Clínicos/Administrativos**:
  * Rotação de chave Fernet: `python backend/rotate_encryption_key.py`
  * Reconstrução do índice de busca: `python backend/rebuild_search_index.py`
  * Gestão de assinatura/trial beta: `python backend/manage_billing.py`
  * Rotinas de limpeza e ciclo de vida de conta: `python backend/maintenance.py`

---

## 4. Regras de Conduta para o Claude Code

* **Edição Supervisionada**: Antes de modificar qualquer arquivo de infraestrutura (`docker-compose*.yml`, `Caddyfile`, `deploy.sh`, `backup.sh`), segurança (`backend/crypto.py`, `backend/http_security.py`, `backend/totp.py`) ou migrações (`backend/alembic/versions/*.py`), apresente a explicação técnica e o *diff* para aprovação humana.
* **Preservação de Fluxo Beta**: Garanta que alterações em `backend/billing.py`, `backend/legal.py` ou no frontend (`TermsGate.js`, `BetaInterestDialog.js`, `WriteAccessContext.js`) nunca bloqueiem a escrita ou leitura de prontuários de contas em período de avaliação ativa.