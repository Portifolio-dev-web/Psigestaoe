
# Relatório de Prontidão para Deploy + Recomendação de Hospedagem — PsiGestão

**Data da auditoria:** 2026-11-09 · **Método:** leitura do código (somente leitura, nenhum arquivo alterado, nenhum deploy, nenhum recurso pago criado), execução dos comandos de build/teste/auditoria, e pesquisa de preços em páginas oficiais dos provedores (link + data em cada um).

---

## 0. Veredito

## 🟡 PRONTO COM RESSALVAS

Não há **nenhum bloqueador**. A base de código já está em um nível de maturidade de segurança/LGPD alto para o estágio do produto (criptografia de campo, índice cego, autorização por `owner_id`, rate limiting, auditoria, retenção/exclusão de dados, CI com testes+pip-audit+yarn audit+gitleaks+build Docker). Os problemas reais encontrados são pontuais, a maioria é configuração (variável de ambiente) ou tem correção já pronta no próprio repositório — não exigem reescrever lógica de negócio.

Dois pontos pedem decisão sua antes de ir ao ar com clientes reais:
1. **Orçamento vs. custo real de infraestrutura** — o menor plano de VPS viável no Brasil fica ~70-85% acima do teto de R$100/mês que você definiu (detalhes na Etapa 4/5). Preciso que você decida como proceder.
2. **Segredos antigos no histórico do git** (já avisado durante a auditoria): rotacionar por precaução, mesmo o repositório sempre tendo sido privado.

---

## 1. Alerta de segurança já reportado durante a auditoria (recapitulando)

Durante a Etapa 1 encontrei commits (`3fbc49e` até `8604976`) que continham arquivos reais `.env`, `backend/.env` e `frontend/.env` com valores reais — não apenas os `.env.example`. Eles foram removidos do HEAD atual no commit `8604976` (2026-09-30), mas continuam recuperáveis do histórico do git. Você confirmou que o repositório (`github.com/Portifolio-dev-web/Psigestaoe`) sempre foi **privado**, o que reduz bastante o risco, mas **qualquer segredo que tenha existido nesses três arquivos** (chave Fernet, `SEARCH_INDEX_KEY`, `JWT_SECRET`, senha de banco, credenciais de SMTP/billing — o que estiver neles) deve ser tratado como **a ser rotacionado por precaução** antes de usar em produção real com dados de pacientes, já que esteve em texto puro no histórico. Não copiei nenhum valor em nenhum momento. **Classificação: Importante** (não bloqueador, porque o repo sempre foi privado).

---

## 2. Etapa 1 — Mapeamento do projeto

- **Backend** (`backend/`): Python 3.11 + FastAPI + Gunicorn/Uvicorn workers, SQLAlchemy async (asyncpg) + Alembic para migrações, Redis para rate limiting, criptografia de campo com `MultiFernet` (`backend/crypto.py`), índice cego HMAC para busca (`backend/rebuild_search_index.py`).
- **Frontend** (`frontend/`): React 19 + CRACO + TailwindCSS + Radix/shadcn, build estático servido por Nginx não-root.
- **Proxy único**: Caddy (`Caddyfile`), HTTPS automático, roteia `/api/*` → Gunicorn, demais rotas → Nginx. IP fixo do Caddy na rede Docker é o único IP confiável para `X-Forwarded-For` (`docker-compose.prod.yml:107-110,160-163`).
- **Banco/cache**: Postgres 16 e Redis 7, nunca publicam porta no host (`docker-compose.prod.yml:45-57,69-77`).
- **Serviços externos**: SMTP (qualquer provedor — Resend/Brevo recomendados na doc), Sentry (opcional, condicionado a DSN), Google OAuth (opcional), billing/pagamento (desligado por padrão — `BILLING_PROVIDER` vazio até ter meio de pagamento real).
- **Testes**: `tests/test_backend_routes.py` via pytest.
- **CI**: `.github/workflows/ci.yml` já roda a cada push/PR: pytest, pip-audit, yarn audit (com filtro próprio), build de produção do frontend, gitleaks (arquivos + commits novos), build das 2 imagens Docker.
- **Já existiam no repo, antes desta auditoria**: `PERFORMANCE_ACTION_PLAN.md` (teste de carga k6, achado de esgotamento de pool de conexões), `docs/deploy-vps.md` (passo a passo completo de deploy em VPS, já escolhendo Vultr São Paulo), `docs/plano-resposta-incidentes.md` (rascunho, pendente de revisão jurídica), `AUDITORIA-UX.md` e `UX_A11Y_AUDIT_REPORT.md` (fora do escopo desta auditoria, focam em UX/acessibilidade).

---

## 3. Etapa 2 — O que foi efetivamente executado

| Comando | Onde | Resultado |
|---|---|---|
| `ENVIRONMENT=development python -m pytest ../tests -q` | `backend/` (Python 3.13, deps via `requirements-dev.txt`) | ✅ **148 passed, 0 failed** (54,4s). 1 warning de depreciação do `reportlab` (não é do projeto). |
| `yarn install --frozen-lockfile` + `CI=true yarn build` | `frontend/` (yarn 1.22.22 pinado) | ✅ **Build OK.** Bundle principal: 438 KB bruto / **136,85 KB gzip**. Total `build/`: 2,5 MB. Code-splitting por rota já em uso (`frontend/src/App.js:19-40`, `React.lazy`). |
| `pip-audit --disable-pip --require-hashes -r requirements.txt` | `backend/` | ⚠️ **47 avisos em 5 pacotes, todos transitivos** (nenhuma dependência direta do projeto está vulnerável): `pillow` (via `reportlab`), `certifi`, `dnspython` (via `email-validator`), `opentelemetry-api` (via `sentry-sdk`), `pyasn1` (via `google-auth`). Ver item 4.3. |
| `yarn audit --groups dependencies --json` + `.github/scripts/yarn_audit_gate.py` | `frontend/` | ❌ **Gate do CI está FALHANDO agora**: 3 vulnerabilidades críticas (`proxy-addr`, `shell-quote` ×2), todas dentro da cadeia do `react-scripts` (ferramenta de build/dev, nunca embarca no bundle de produção servido pelo Nginx). Ver item 4.3. |
| `docker build -t psigestao-api ./backend` | raiz | ✅ Build OK (imagem de produção, usuário não-root). |
| `docker build --target production --build-arg REACT_APP_BACKEND_URL=... -t psigestao-web ./frontend` | raiz | ✅ Build OK. Checagem própria do Dockerfile confirmou **nenhum source map** no build de produção (`frontend/Dockerfile`, etapa que falha o build se achar `*.map`). |
| gitleaks | — | Não executado nesta auditoria (binário indisponível no ambiente da sessão) — não é crítico porque o achado de segredo histórico já estava confirmado e tratado no item 1, e uma varredura manual por padrões de chave no código atual não encontrou nada. |

---

## 4. Etapa 3 — Checklist de prontidão

Legenda de esforço: **P** = pequeno (config/env var, minutos-horas) · **M** = médio (toca lógica, precisa teste) · **G** = grande (reescrita/infra nova).

### 4.1 Segurança

| Item | Status | Evidência | Risco / Correção | Esforço |
|---|---|---|---|---|
| Segredos fora do código e do git (estado atual) | OK | `.gitignore:34-35` (`.env`, `.env.*`); só `.env.example` versionado | — | — |
| Segredos no histórico do git (estado passado) | **Importante** | commits `3fbc49e`…`8604976` (ver item 1) | Rotacionar por precaução antes de produção real | P |
| Hash de senha forte (bcrypt) | OK | `backend/server.py:23,387-391` | — | — |
| Cookie httpOnly/secure/sameSite | OK | `backend/server.py:148-151,162-188` (`__Host-` em produção, `samesite="strict"`) | — | — |
| Expiração de sessão | OK | `backend/models.py:196-209` | — | — |
| Rotas admin protegidas (+MFA) | OK | `backend/admin_access.py`; `backend/server.py:4546-4552,4578-4610` | — | — |
| Autorização / IDOR | OK | Todo acesso a Patient/Record/Session/AuditLog filtra por `owner_id` (ex. `backend/server.py:3175,3426,3540,3596,5332,5394`) | — | — |
| Validação de entrada (Pydantic) | OK | 88 usos de `max_length=` em `backend/server.py` | — | — |
| SQL injection | OK | Todo SQL cru via `text()` parametrizado (`backend/server.py:5181,5704,5709,5870,5881,5914`) | — | — |
| XSS (e-mails/PDF) | OK | `backend/mailer.py:162-166,190,204` (`html.escape`); `backend/server.py:3819,3826-3864` (`pdf_text`) | — | — |
| CORS restrito a HTTPS | OK | `backend/server.py:257-272` (fail-closed) | — | — |
| Headers de segurança (API) | OK | `backend/http_security.py:102-122,153-154` (CSP, HSTS, X-Frame-Options, no-store) | Falta `upgrade-insecure-requests`/`preload` | P |
| Headers de segurança (frontend) | OK | `frontend/nginx.conf:58-69` | mesma observação acima | P |
| Rate limit — login/cadastro/2FA | OK | `backend/server.py:1734,1825,1849,1879,2050,2251,2864,2913,2940,2968` | — | — |
| Rate limit — formulário público (link paciente) | OK | `backend/server.py:4210,4322,4348,4372,4383,4400,4422` | — | — |
| **Rate limit — `/dashboard/stats` e `/patients`** | **Importante — FALTA** | `backend/server.py:4992-4993` e `3384-3396` só têm `Depends(get_active_user)`, sem `rate_limit` | Usuário autenticado pode martelar os agregados mais caros do sistema. Acrescentar `Depends(rate_limit(...))` igual já feito em `/patients/search` (`server.py:3411`) | P |
| Rate limit — webhook de billing | OK | `backend/server.py:2686` | — | — |
| Upload de arquivo | NÃO SE APLICA | Nenhum `UploadFile`/multipart no backend | — | — |
| Webhook billing — assinatura + idempotência | OK | `backend/billing.py:296-318`; `backend/models.py:516-531` | — | — |
| Webhook Google Forms | NÃO SE APLICA | Removido do produto (`backend/models.py:71-75`), substituído pelo link de cadastro próprio | — | — |
| LGPD — dados pessoais e criptografia | OK | `backend/models.py:265-286,435-436,492-498` (campos marcados `[CRIPTOGRAFADO]`); `backend/crypto.py` | — | — |
| LGPD — política de privacidade | OK (texto é modelo) | `backend/legal.py` | Revisão jurídica ainda pendente antes de clientes reais (já sinalizado no `README.md:97-103` e no `docs/plano-resposta-incidentes.md`) | — (jurídico, fora do código) |
| LGPD — exclusão de dados a pedido do titular | OK | `backend/server.py:5627,5715,5870-5881` (retenção de 5 anos CFP respeitada) | — | — |
| LGPD — consentimento com evidência | OK | `backend/models.py:291-299` (snapshot do texto aceito) | — | — |
| **Dependências backend — 5 transitivas com CVE conhecida** | **Melhoria** | `pillow`, `certifi`, `dnspython`, `opentelemetry-api`, `pyasn1` (via `reportlab`/`email-validator`/`sentry-sdk`/`google-auth` em `backend/requirements.in`) | Nenhuma é exploração prática óbvia hoje (sem upload de imagem de usuário), mas regenerar `requirements.txt` fixando versões corrigidas | P |
| **Dependências frontend — 3 críticas no gate do CI** | **Importante** | `proxy-addr` e `shell-quote` (×2) via `react-scripts` → `webpack-dev-server`/`react-dev-utils` — só na ferramenta de **dev**, nunca no bundle servido pelo Nginx em produção. **O gate do CI está vermelho agora** | Risco de produção baixo, mas CI vermelho tende a ser ignorado com o tempo. Forçar versão corrigida via `resolutions` no `frontend/package.json`, ou migrar para Vite (já citado como backlog em `.github/scripts/yarn_audit_gate.py:10`) | M |

### 4.2 Confiabilidade e dados

| Item | Status | Evidência | Risco / Correção | Esforço |
|---|---|---|---|---|
| Migrações versionadas (Alembic) + trava antes dos workers | OK | `backend/alembic/versions/*.py` (16 arquivos); `backend/prestart.py:56-87`; `backend/start.sh:12,22` | — | — |
| Sem sync direta de schema em produção | OK | `backend/server.py:209-211,223-226` (`AUTO_CREATE_TABLES` default `false` em produção) | — | — |
| Backup automático (script) | OK | `backup.sh` inteiro: fail-closed, dump + envio criptografado via Restic + retenção + ping Healthchecks.io | — | — |
| **Backup — agendamento** | **Importante — FALTA** | Nenhum `cron`/`systemd timer` versionado no repo — depende de configurar manualmente na VPS (`docs/deploy-vps.md:320-328`) | Sem agendamento, o backup nunca roda sozinho em produção | P |
| **Teste de restauração de backup** | **Crítico — FALTA** | `docs/deploy-vps.md:330-331,406` só recomenda "a cada 3 meses", sem script nem evidência de execução | "Backup que nunca foi restaurado não é backup" (texto do próprio doc) | M |
| Disco efêmero (Postgres em volume nomeado) | OK | `docker-compose.prod.yml:35-37,50-51` | — | — |
| Redis sem volume nomeado | Melhoria (impacto baixo) | `docker-compose.prod.yml:66-82` (`--appendonly yes` sem `volumes:`) | Só perde contadores de rate-limit (janela curta); nada de sessão/prontuário | P |
| Nenhum dado crítico fora de volume | OK | Sem rotas de upload; frontend só serve estático | — | — |
| `/api/health/live` e `/api/health/ready` | OK | `backend/server.py:5901-5919`; usados em `backend/Dockerfile:39-40`, `docker-compose.prod.yml:116-121`, `deploy.sh:49` | — | — |
| Erros sem vazar stack trace | OK (implícito no framework) | `backend/server.py:240,357,360` (sem `debug=True`; handlers para `RequestValidationError`/`IntegrityError`) | Comportamento correto, mas não há handler genérico explícito — risco de alguém ligar `debug=True` no futuro | P |
| Configuração via variáveis de ambiente | OK | `backend/database.py:24-32`; `backend/environment.py:34-42`; `backend/server.py:258-269` | — | — |
| **Estratégia de rollback** | **Importante — FALTA parcial** | `docs/deploy-vps.md:382-396` (manual); `deploy.sh:48-60` não reverte automaticamente se o health check falhar | Rollback depende de operador lembrar comandos manuais sob pressão, nunca testado em simulação | M |

### 4.3 Escalabilidade

| Item | Status | Evidência | Risco / Correção | Esforço |
|---|---|---|---|---|
| App stateless (sessão) | OK | `backend/server.py:493-515` (JWT em cookie, `sid` aponta para `user_sessions` no Postgres) | — | — |
| Cache/contadores fora da instância | OK | `backend/rate_limit.py:70-133` (Redis compartilhado, obrigatório em produção) | — | — |
| **Pool de conexões do banco** | **Crítico — FALTA (já conhecido, não corrigido)** | `backend/database.py:86-90` (`pool_size=5`, `max_overflow=5`, `pool_timeout=10`, padrões inalterados); `backend/gunicorn.conf.py:46` (workers = CPUs do host de build, não fixo) | **Causa raiz confirmada** do teste de carga em `PERFORMANCE_ACTION_PLAN.md`: 2,5% de erro HTTP 500 sob 240 VUs. Ainda presente, sem mitigação. Fix é só env var: `DB_POOL_SIZE=10`, `DB_MAX_OVERFLOW=10`, `DB_POOL_TIMEOUT=3`, `WEB_CONCURRENCY=2` fixo | P |
| N+1 em listagens | OK | `backend/server.py:5105-5110,3314-3325` (queries batched) | — | — |
| Paginação `/patients` (cursor por data) | OK | `backend/server.py:3355-3363` | — | — |
| **Paginação `/patients` (ordenar por nome/última consulta)** | **Importante — FALTA** | `backend/server.py:3364-3373` carrega **todos** os pacientes do filtro para memória e ordena em Python antes de paginar | Degrada silenciosamente com o crescimento da base (não é N+1, é "tudo de uma vez"). Mover ordenação para o banco ou paginar com OFFSET real | M |
| Paginação `/sessions` | OK | `backend/server.py:5254-5276` (janela temporal + limite, índice dedicado) | — | — |
| Índices nas tabelas mais usadas | OK | `backend/models.py:321,343,456-458,509,564` | — | — |
| Cache HTTP de respostas da API | NÃO SE APLICA (decisão deliberada) | `backend/http_security.py:116-118,151-152` — `no-store` em toda resposta `/api/` (dado de saúde não pode cachear) | — | — |
| **Cache Redis para leitura (`/dashboard/stats`)** | **Importante — FALTA** | Rota com 9 queries sequenciais (`backend/server.py:4992-5077`), sem TTL/cache | Agrava o item do pool acima. TTL curto (15-20s) via `redis.asyncio` | M |
| Cache/CDN de estáticos do frontend | Parcial | `frontend/nginx.conf:81-84` tem `expires 1y` mas falta `immutable` | Ganho marginal | P |
| Tarefas pesadas fora do ciclo de requisição | Parcial | `backend/server.py:233,5862-5892` roda `_maintenance_loop` dentro do próprio processo da API (com trava `pg_advisory_lock`), embora `backend/maintenance.py` já suporte rodar via cron externo | Compete pelo pool de conexões já apertado (ver item crítico acima); considerar `MAINTENANCE_ENABLED=false` + cron externo em produção com tráfego real | P |

### 4.4 Monitoramento

| Item | Status | Evidência |
|---|---|---|
| Logs estruturados, sem dado sensível | OK | `backend/observability.py:49-68,84-87,125-180` (JSON, sem corpo/querystring, `uvicorn.access` desligado) |
| Captura de erros (Sentry) condicionada a DSN | OK | `backend/observability.py:214-230` (scrub de PII, body nunca enviado); `frontend/src/lib/sentry.js:42,48-49` |
| Monitor de uptime | NÃO SE APLICA ao código | Tarefa operacional pós-deploy (`docs/deploy-vps.md:333-346`) — UptimeRobot recomendado, ainda não configurado (normal, não há deploy ainda) |
| Métricas Prometheus com token | OK | `backend/server.py:5922-5938` (`METRICS_TOKEN`, comparação resistente a timing attack, 404 se sem token) |

### 4.5 Entrega

| Item | Status | Evidência |
|---|---|---|
| Dockerfile backend — usuário não-root | OK | `backend/Dockerfile:28-32` |
| Dockerfile backend — multi-stage | Não é multi-stage por estágios distintos, mas single-stage já sem ferramentas de dev na imagem final (Melhoria opcional) | `backend/Dockerfile:12-17` |
| Dockerfile frontend — multi-stage + não-root | OK | `frontend/Dockerfile` (estágios `deps`/`dev`/`build`/`production`; `nginx-unprivileged`) |
| CI cobrindo build+test+deps+secrets+docker a cada push/PR | OK | `.github/workflows/ci.yml` completo |
| **Lint dedicado do backend Python no CI** | Melhoria — FALTA | Sem `ruff`/`flake8` em `backend/requirements-dev.txt`; cobertura de lint depende só do eslint do frontend | Adicionar job `ruff check backend/` |
| Estratégia de rollback | Já listado em 4.2 | — |

---

## 5. Resumo dos problemas por severidade

**Bloqueador:** nenhum.

**Crítico** (corrigir antes de abrir para clientes reais):
1. Pool de conexões SQLAlchemy inadequado sob carga (`backend/database.py:86-90`) — fix é só variável de ambiente (P).
2. Falta de teste de restauração de backup documentado/automatizado — M.

**Importante** (corrigir na 1ª semana de produção real):
3. Segredos que estiveram no `.env` histórico — rotacionar por precaução (P).
4. Gate de `yarn audit` do CI está falhando agora (3 críticas via `react-scripts`, risco de produção baixo mas CI vermelho) (M).
5. Rate limit ausente em `/dashboard/stats` e `/patients` (P).
6. Backup sem agendamento automatizado versionado (P).
7. Rollback 100% manual, nunca testado (M).
8. Paginação "tudo em memória" ao ordenar `/patients` por nome (M).
9. Cache Redis ausente em `/dashboard/stats` (agrava o item 1) (M).

**Melhoria** (quando houver tempo):
10. 5 dependências transitivas do backend com CVE conhecida — regenerar `requirements.txt` (P).
11. CSP sem `upgrade-insecure-requests`, HSTS sem `preload` (P).
12. Redis sem volume nomeado para o AOF (P).
13. Handler de exceção genérico explícito ausente (P).
14. Backend sem linter dedicado no CI (P).
15. `nginx.conf` sem `immutable` no cache de estáticos (P).
16. Rotina de manutenção roda dentro do processo da API (P).

---

## 6. Etapa 4 — Comparação de arquiteturas de deploy

**Nota sobre os preços:** cada valor abaixo tem fonte e data. Onde não consegui confirmar na página oficial (bloqueio de bot ou conteúdo renderizado por JavaScript), marquei "não verificado oficialmente" e usei o número mais citado por múltiplas fontes de terceiros — confirme antes de contratar.

- **Cotação usada:** USD/BRL ≈ R$5,49 (fechamento de 05/11/2026, fonte: notícia de mercado — não é PTAX oficial). Câmbio varia; confira no dia do pagamento.
- **IOF sobre compra internacional no cartão:** 3,5% (Decreto 12.499/2025, vigente desde 17/07/2025) — citado por múltiplas fontes financeiras, **não verificado em fonte primária (Receita Federal/BACEN)** nesta sessão.

### Opção A — PaaS gerenciado (Vercel + Render)

Você já tem conta nos dois. Mas a arquitetura atual do PsiGestão foi desenhada para **um domínio único por trás do Caddy** (cookie `SameSite=Strict`, `FORWARDED_ALLOW_IPS` fixo para rate limit por IP real — `CLAUDE.md` seção 1 e 3). Separar frontend (Vercel) e backend (Render) em domínios diferentes exige **reengenharia real**, não só reconfiguração: revisar cookie cross-site, CORS, confiança de proxy, e habilitar TLS em banco/Redis (hoje desligado de propósito porque tudo roda na mesma rede Docker privada — `README.md:117-126`).

| | Valor | Fonte |
|---|---|---|
| Vercel Hobby (frontend) | **Grátis, mas só para uso pessoal/não-comercial** — PsiGestão é um produto comercial (mesmo em beta) | [vercel.com/docs/plans/hobby-plan](https://vercel.com/docs/plans/hobby-plan), consultado 2026-11-09 |
| Vercel Pro (necessário p/ uso comercial) | US$20/mês por assento | [vercel.com/pricing](https://vercel.com/pricing), consultado 2026-11-09 |
| Render — web service free | Dorme após 15 min sem tráfego; 750h grátis/mês/workspace | [render.com/docs/free](https://render.com/docs/free), consultado 2026-11-09 |
| Render — Postgres free | **1 GB, expira 30 dias após criação, 14 dias de carência e depois é DELETADO (sem backup)** | [render.com/docs/free](https://render.com/docs/free), consultado 2026-11-09 |
| Render — web service pago (Starter) | ~US$7/mês (0,5 CPU / 512 MB) — não confirmado na página oficial (bloqueada por JS); citado por múltiplas fontes de terceiros | Não verificado oficialmente |
| Render — Postgres pago (menor plano) | Não verificado — página oficial bloqueada, nenhuma fonte confiável achada | Não verificado |

- **Custo agora (estimado, assumindo plano mínimo sem expiração/sem dormir):** ~US$20 (Vercel Pro) + ~US$7 (Render web) + Postgres pago (não verificado, estimo US$7-20) + Redis/KV pago (não verificado) → **US$35-55/mês ≈ R$200-320/mês com IOF**, **pior que a Opção B**, e ainda exige reescrever a parte de cookie/CORS/proxy.
- **Com 10x tráfego:** sobe rápido (Vercel cobra banda/execuções acima do incluso; Render cobra por instância maior).
- **O que o grátis não cobre:** cold start (Render), banco que se autodestrói em 44 dias (Render free Postgres), uso comercial (Vercel Hobby).
- **Latência Brasil:** Vercel tem edge no Brasil (bom); Render **não tem região no Brasil** — maior latência para psicólogas brasileiras, e dado de saúde fica fora do país (LGPD art. 33 permite transferência internacional sob condições, mas não é o ideal para prontuário clínico).
- **Monitoramento incluído:** básico (logs), nada de verdade pronto.
- **Horas de manutenção:** baixas depois de configurado, mas o custo de configurar a migração de arquitetura é alto agora.
- **Migrar depois:** médio — dá para sair de volta para Docker/VPS reaproveitando os mesmos Dockerfiles que já existem.

### Opção B — VPS única com Docker Compose (o que o projeto já tem pronto)

**Esta non é uma proposta nova — é o que `docker-compose.prod.yml`, `Caddyfile`, `backup.sh`, `deploy.sh` e `docs/deploy-vps.md` (414 linhas, já escrito) já implementam.** Não exige nenhuma mudança de arquitetura, só executar o passo a passo que já está no repositório.

| | Valor | Fonte |
|---|---|---|
| VPS Vultr `vc2-2c-4gb` (2 vCPU/4GB/80GB), região São Paulo | **US$30/mês** | API oficial da Vultr ([api.vultr.com/v2/plans](https://api.vultr.com/v2/plans)), consultado 2026-11-09 |
| Alternativa "alta frequência" `vhf-2c-4gb`, São Paulo | US$36/mês | mesma fonte |
| Domínio `.com.br` (Registro.br) | ~R$40/ano | **Não verificado oficialmente** (página renderiza por JS) — valor consistentemente citado por múltiplas fontes de hospedagem brasileiras, consultado 2026-11-09 |
| Backup externo — Backblaze B2 | US$6,95/TB/mês (primeiros 10GB grátis; egress grátis até 3x o armazenamento) | [backblaze.com/cloud-storage/pricing](https://www.backblaze.com/cloud-storage/pricing), consultado 2026-11-09 (nota: rodapé da página mostra © 2024 — confirmar atualidade) |
| SMTP — Resend | Grátis até 3.000 e-mails/mês (100/dia) | Múltiplas fontes de terceiros, **não verificado oficialmente nesta sessão** |
| Erros — Sentry Developer | Plano grátis existe; limites de cota variam entre fontes (5k-50k erros/mês) | **Não verificado oficialmente** — confirmar em sentry.io/pricing |
| Uptime — UptimeRobot | Grátis, 50 monitores, 5 min | Múltiplas fontes, não verificado oficialmente |
| Backup rodou — Healthchecks.io | Grátis, ~20 checks | Múltiplas fontes, não verificado oficialmente |
| Acesso admin — Tailscale | Grátis até 6 usuários, **mas o FAQ oficial do Tailscale diz que o plano Personal é só para uso não-comercial** | [Tailscale docs](https://tailscale.com/docs/account/manage-plans/free-plans-discounts) (via busca, não lido na íntegra) — ponto de atenção para quando o produto tiver clientes pagantes (impacto baixo: é só acesso administrativo, não o produto) |

- **Custo mensal recorrente (infra paga):** US$30 (VPS) × R$5,49 = R$164,70, + IOF 3,5% (R$5,76) = **≈ R$170/mês**, mais spread do seu cartão (variável, 1-4% adicional, não verificável) → **estimativa realista: R$170-185/mês**. Domínio é custo anual (R$40/ano ≈ R$3,33/mês). Tudo o resto (B2 nos primeiros meses, SMTP, Sentry, UptimeRobot, Healthchecks, Tailscale) fica em R$0 nos planos grátis.
- **Com 10x tráfego (100 psicólogas):** provavelmente ainda cabe no mesmo VPS (o teste de carga em `PERFORMANCE_ACTION_PLAN.md` usou 240 VUs simulados, muito acima do que 100 usuárias reais gerariam) — mas corrija primeiro o item crítico do pool de conexões (Etapa 3).
- **O que não é grátis:** nada aqui depende de tier gratuito instável — é só o VPS que é pago desde o primeiro dia.
- **Latência Brasil:** melhor possível (região São Paulo).
- **Segurança incluída:** HTTPS automático (Caddy/Let's Encrypt), sem proteção DDoS de borda dedicada (Vultr não inclui; mitigação é o rate limiting já implementado na aplicação). Firewall de SO configurado manualmente (`docs/deploy-vps.md` seção 2).
- **Monitoramento incluído:** nenhum por padrão — você configura Sentry/UptimeRobot/Healthchecks.io (grátis, documentado passo a passo).
- **Horas de manutenção:** as mais altas das três opções (você é o sysadmin), mas o guia já documenta tudo, inclusive operação pelo celular.
- **Migrar depois:** fácil para outro VPS/provedor (é só Docker Compose); mais trabalho para migrar para um PaaS (exigiria a reengenharia da Opção A).

### Opção C — Híbrido (menor peça em cada camada)

Ex.: VPS menor só para a API + Cloudflare Pages (frontend) + Neon (Postgres serverless) + Cloudflare R2 (storage) + Upstash (Redis serverless).

| | Valor | Fonte |
|---|---|---|
| Neon Postgres free | 100 CU-hours/projeto/mês, 1GB armazenamento, autosuspend 5 min (reativa em milissegundos), **permanente, não é trial, sem exclusão por inatividade** | [neon.com/pricing](https://neon.com/pricing) (atualizado 2026-11-04), consultado 2026-11-09 |
| Cloudflare R2 | US$0,015/GB-mês, egress grátis, 10GB grátis/mês | Múltiplas fontes de terceiros, não verificado oficialmente nesta sessão |
| Cloudflare Pages | Grátis, generoso para estático | Não pesquisado em detalhe (não é o gargalo de custo) |

- Essa opção **exige a mesma reengenharia de domínio/cookie/CORS da Opção A**, só que espalhada em mais provedores (mais complexidade operacional, não menos). O ganho de custo sobre a Opção B é pequeno (talvez R$20-40/mês a menos), mas o custo de engenharia e de manutenção contínua (mais contas, mais pontos de falha, mais coisa para o seu Tailscale/SSH não alcançar em caso de incidente) é maior. **Não recomendo para uma pessoa sozinha com 2h/semana de manutenção disponível.**
- Vantagem real: dá para rodar a API num VPS menor (só a API, sem banco/Redis locais), talvez ~US$12-15/mês — mas o Postgres gerenciado fora do Brasil (Neon não tem região São Paulo confirmada) tira o ponto principal da Opção B (dado de saúde no Brasil) sem ganho de custo suficiente para compensar.

---

## 7. Etapa 5 — Recomendação

> **Atualização pós-relatório (2026-11-09, depois de conversa com o usuário):** ao tentar configurar o Vultr na prática, a região São Paulo não apareceu disponível na conta/console (apesar de a API pública listar `location_cost` para "sao" nos planos `vc2-2c-4gb`/`vhf-2c-4gb` — pode ser uma limitação de conta nova ou de capacidade). O usuário decidiu usar a **Hostinger** (ou outro provedor nacional) em vez do Vultr. Pesquisei os preços oficiais da Hostinger e atualizei a recomendação abaixo — o resultado é **melhor** para o orçamento do que o Vultr teria sido.
>
> **Hostinger VPS — planos oficiais (hostinger.com/br/vps, consultado 2026-11-09), cobrados em R$ (sem IOF de câmbio, pois o preço já é em reais):**
>
> | Plano | vCPU | RAM | NVMe | Banda | Preço promocional (24 meses, pago adiantado) | **Preço de renovação** |
> |---|---|---|---|---|---|---|
> | KVM 1 | 1 | 4 GB | 50 GB | 4 TB | R$29,99/mês (R$719,76 à vista por 24 meses) | **R$59,99/mês** |
> | KVM 2 | 2 | 8 GB | 100 GB | 8 TB | R$42,99/mês (R$1.031,76 à vista por 24 meses) | **R$77,99/mês** |
>
> Os dois incluem **backup semanal grátis** e **filtro DDoS (Wanguard)** — a Opção B com Vultr não tinha nenhum dos dois por padrão. **Ambos cabem dentro do teto de R$100/mês mesmo no preço de renovação**, sem precisar do desconto de 24 meses.
>
> ⚠️ **Dois pontos de atenção antes de contratar:**
> 1. **Confirmar a região São Paulo/Brasil no fluxo de contratação.** As páginas oficiais que consultei não citaram "São Paulo" ou "Brasil" na lista atual de datacenters (listavam Alemanha, Índia, EUA, Reino Unido, Lituânia, Malásia, França, Indonésia) — isso contradiz o anúncio de 2024 da própria Hostinger sobre um datacenter no Brasil. Pode ter sido descontinuado, consolidado, ou só não aparecer nessa página específica. **Confirme a região exata na tela de checkout antes de pagar** — se não houver Brasil, meça a latência de outra região (Lituânia/EUA) antes de decidir, ou considere outro provedor nacional (Hostgator BR, KingHost, Locaweb — não pesquisados em detalhe nesta auditoria).
> 2. **O preço promocional exige pagamento adiantado de 24 meses de uma vez** (R$719,76 ou R$1.031,76 à vista). Isso conflita com o seu plano de "o mais perto de zero possível nos 2 primeiros meses sem receita" — committar ~R$720-1.030 de uma vez antes de validar o produto com as 10 psicólogas é um risco de caixa, não só de infraestrutura. **Procure a opção de cobrança mensal (sem prepago de 24 meses)** no checkout — geralmente existe, custa mais por mês, mas preserva a opção de cancelar sem perda se o piloto não validar. Recomendo KVM 1 ou KVM 2 no plano **mensal** (não o de 24 meses) para a Fase 0, e só migrar para o prepago quando tiver certeza de que vai continuar.
> 3. Recomendo **KVM 2** (2 vCPU/8 GB) em vez do KVM 1 (1 vCPU/4 GB): o `docker-compose.prod.yml:59` e o teste de carga em `PERFORMANCE_ACTION_PLAN.md` assumiram 2 vCPU como piso — ir para 1 vCPU é uma combinação não testada. O KVM 2 ainda cabe no orçamento mesmo na renovação (R$77,99/mês).
>
> A tabela e os cálculos de custo abaixo foram escritos antes dessa conversa (com Vultr); a conclusão qualitativa (Opção B vence A e C) continua valendo — só troque "Vultr US$30/mês" por "**Hostinger KVM 2, R$42,99-77,99/mês**" mentalmente ao ler o resto desta seção. Vou ajustar os números de custo total na próxima revisão deste relatório, se quiser.

### Escolha: **Opção B — VPS num provedor nacional (Hostinger ou equivalente) com o Docker Compose que já existe no repositório.**

Por quê, em poucas linhas: a arquitetura de segurança do projeto (cookie `SameSite=Strict`, proxy confiável fixo, TLS interno dispensado por estar na mesma rede privada) **já foi desenhada em torno de "um domínio, um host"**. As opções A e C quebram essa premissa e exigem reengenharia real (não just reconfiguração) para algo que, no fim, custa **mais**, não menos, e tira o dado de saúde do Brasil. A Opção B também é a única das três onde **tudo já está escrito, documentado e testado** (inclusive com teste de carga) — você só precisa executar `docs/deploy-vps.md`, que já existe.

### ⚠️ Conflito de orçamento — preciso da sua decisão

Você definiu um teto de **R$100/mês**. O VPS mínimo viável (2 vCPU/4GB, o piso que o próprio `docker-compose.prod.yml:59` já assume — comentário `# ponytail: limites fixos p/ VPS 2 vCPU / 4 GB`) custa **≈ R$170-185/mês** depois de IOF. Isso é **70-85% acima do teto**, mesmo sendo a opção mais barata das três. Não encontrei nenhuma forma de rodar esse stack (Postgres + Redis + Gunicorn + Nginx + Caddy, criptografia e rate limit incluídos) de forma confiável, no Brasil, dentro de R$100/mês — nem o Oracle Cloud Always Free resolve isso de forma segura: o free tier foi cortado pela metade em 2026 (hoje 2 OCPU/12GB, não 4/24), há relatos de indisponibilidade de capacidade especificamente na região São Paulo, e a Oracle **deleta a instância em 30 dias** se decidir que você excedeu a cota grátis — risco inaceitável para um sistema com dado de prontuário.

**Preciso que você escolha uma destas saídas** (pergunta ao final da minha mensagem de resumo):
- (a) aceitar ~R$170-185/mês de infraestrutura mesmo na Fase 0 (ainda é a opção mais barata e mais segura das três avaliadas);
- (b) topar um VPS menor (2 vCPU/2GB, ~US$22/mês ≈ R$125/mês) temporariamente, aceitando risco maior de falta de memória sob pico — o `docker-compose.prod.yml` não foi testado nesse tamanho;
- (c) revisar o teto de R$100/mês para refletir o custo real de uma infraestrutura segura no Brasil para dado de saúde.

### Plano em fases

**Fase 0 — agora, sem receita (0-2 meses):**
- Corrigir os 2 itens **Crítico** e os 7 **Importante** da Etapa 3 (nenhum deles exige mais que algumas horas — a maioria é variável de ambiente).
- Rotacionar os segredos que estiveram no `.env` histórico.
- Subir o VPS seguindo `docs/deploy-vps.md` (já existe, passo a passo completo).
- Configurar Sentry, UptimeRobot, Healthchecks.io, backup com Restic+B2 (todos grátis nesta escala) — seção 9/10 do guia.
- **Gatilho para Fase 1:** primeira psicóloga paga a assinatura (ou você decide abrir para as 10 beta testers).

**Fase 1 — primeiro cliente pagante:**
- Ativar `BILLING_PROVIDER` de verdade (hoje vazio/simulado de propósito).
- Revisão jurídica da Política de Privacidade, Termos de Uso e `docs/plano-resposta-incidentes.md` (hoje são modelos com placeholders — `README.md:97-103` já sinaliza isso).
- Testar a restauração do backup pelo menos uma vez antes do primeiro dado real de paciente pagante.
- **Gatilho para Fase 2:** >20-30 psicólogas ativas, ou qualquer reclamação de lentidão, ou uso de banco/CPU consistentemente >70% por mais de uma semana.

**Fase 2 — crescimento:**
- Aplicar a correção do pool de conexões e o cache do `/dashboard/stats` se ainda não tiver feito (deveriam já estar feitos na Fase 0, mas se o tráfego crescer rápido, priorize antes de qualquer outra coisa).
- Considerar subir o plano do VPS (o próprio compose já tem o comentário de que os limites de recurso devem subir junto: `docker-compose.prod.yml:59`).
- Reavaliar se vale a pena separar backend/frontend em provedores diferentes (Opção A) — só nesse ponto o volume pode justificar o custo de engenharia.

### Custo total estimado — 2 primeiros meses (Fase 0, cenário (a) acima)

| Item | Mês 1 | Mês 2 |
|---|---|---|
| VPS Vultr São Paulo (2vCPU/4GB) | ≈ R$170-185 | ≈ R$170-185 |
| Domínio `.com.br` (1 ano, pago uma vez) | ≈ R$40 | R$0 |
| Backblaze B2 (backup, dentro dos 10GB grátis) | R$0 | R$0 |
| Resend/SMTP (dentro do free tier) | R$0 | R$0 |
| Sentry, UptimeRobot, Healthchecks.io, Tailscale (free tiers) | R$0 | R$0 |
| **Total** | **≈ R$210-225** | **≈ R$170-185** |

Dois meses: **≈ R$380-410** (contra um orçamento de R$200 nos moldes do teto original de R$100/mês — veja a decisão pendente acima).

### Alertas de cobrança a configurar

- **Vultr**: ativar alerta de billing por e-mail no painel (cobrança inesperada por upgrade acidental de plano).
- **Backblaze B2**: configurar "Cap & Alert" no bucket para não passar dos 10GB grátis sem aviso.
- **Sentry/UptimeRobot/Healthchecks.io/Tailscale**: todos ficam nos planos grátis nesta escala — não há cobrança a temer, mas vale configurar notificação de e-mail de cada um para saber se bater no limite do plano grátis (isso indicaria crescimento inesperado de uso, não necessariamente cobrança).
- **Cartão de crédito usado para pagar em dólar**: verificar com o emissor se há alerta de transação internacional e o spread cambial exato cobrado (o IOF de 3,5% é fixo por decreto, mas o spread do banco varia e não é visível antes da fatura).

---

## 8. Passo a passo do deploy recomendado

O passo a passo completo **já está escrito** em [`docs/deploy-vps.md`](docs/deploy-vps.md) (414 linhas) — não precisa ser recriado. Resumo das 14 seções:

1. Criar VPS Vultr 2vCPU/4GB em São Paulo, Ubuntu 24.04 LTS, com chave SSH.
2. Primeiro acesso: criar usuário não-root, desligar login root/senha por SSH.
3. Instalar Docker.
4. Apontar DNS do domínio para o IP da VPS.
5. Instalar Tailscale e fechar a porta SSH pública.
6. Clonar o repositório e gerar os segredos (comandos já no guia).
7. Preencher `.env` e `backend/.env` seguindo a tabela de variáveis (abaixo).
8. Primeira subida: `docker compose -f docker-compose.prod.yml --env-file .env up -d --build`.
9. Configurar backup automático (Restic + Backblaze B2 + cron + Healthchecks.io).
10. Configurar monitoramento (Sentry, UptimeRobot, Healthchecks.io).
11. (Opcional) configurar acesso/monitoramento pelo celular Android.
12. Deploy de novas versões: `./deploy.sh`.
13. Reverter um deploy: processo manual documentado (ver item Importante #7 acima — considere automatizar com um `rollback.sh` antes de ter clientes reais).
14. Checklist final antes de divulgar o link (já no guia, 10 itens).

### Variáveis de ambiente necessárias (só nomes, nenhum valor)

**Raiz (`.env`):**
`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `JWT_SECRET`, `FERNET_KEY`, `FERNET_OLD_KEYS`, `SEARCH_INDEX_KEY`, `DOMAIN`, `ACME_EMAIL`, `REACT_APP_BACKEND_URL`, `REACT_APP_GOOGLE_CLIENT_ID`, `CORS_ORIGINS`, `FRONTEND_URL`, `PRIVACY_CONTROLLER_NAME`, `PRIVACY_CONTROLLER_DOCUMENT`, `PRIVACY_DPO_NAME`, `PRIVACY_DPO_EMAIL`, `SUPPORT_EMAIL`, `ADMIN_EMAILS`, `METRICS_TOKEN`, `SENTRY_DSN`, `BILLING_PROVIDER`, `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, `BACKUP_HEALTHCHECK_URL`, `FORWARDED_ALLOW_IPS`, `DATABASE_TLS_DISABLED`, `REDIS_TLS_DISABLED`, `DB_POOL_SIZE` (recomendo adicionar — ver item Crítico #1), `DB_MAX_OVERFLOW` (idem), `DB_POOL_TIMEOUT` (idem), `WEB_CONCURRENCY` (idem).

**Backend (`backend/.env`):** `GOOGLE_CLIENT_ID`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_SECURITY`.

---

## 9. Resumo para você (≤100 linhas) e próximos passos

Já entregado como mensagem de chat separada, abaixo do relatório. Nenhuma correção foi aplicada — aguardando sua aprovação.


