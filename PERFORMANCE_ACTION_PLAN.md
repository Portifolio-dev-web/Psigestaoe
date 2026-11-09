# Plano de Ação de Performance — PsiGestão

**Data:** 2026-11-07
**Ambiente:** local, `docker-compose.prod.yml` (Postgres 16 + Redis 7 + Gunicorn 2 workers Uvicorn), via `load_tests/script.k6.js`.
**Ferramenta:** k6 (`grafana/k6`, container na mesma rede Docker da API).

## Resumo executivo

O sistema aguenta bem uma carga moderada (40 VUs, ~64 req/s): p95 abaixo de 1s em
todas as rotas, 0,08% de falha, zero erro no Postgres ou no Redis. Sob carga mais
alta (240 VUs, ~92 req/s), **2,50% das requisições falharam com HTTP 500**, com
p95 de 7,1s em `/dashboard/stats` e 5,5s em `/patients` — e a causa é uma única
coisa, confirmada no log da API:

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 5 reached,
connection timed out, timeout 10.00
```

**O banco (Postgres) nunca chegou perto do limite real** (`max_connections=100`,
sem nenhum erro nos logs do `db`). O gargalo é o **pool de conexões do próprio
app**: 2 workers Gunicorn × (5 + 5) = só 20 conexões simultâneas possíveis. Com
240 VUs batendo em rotas que fazem várias queries cada (`/dashboard/stats` faz
7), a demanda passa longe dos 20 slots — e a conexão fica 10s em fila até dar
timeout e estourar um 500 não tratado (não os "503 na fila" que o comentário em
`database.py` descreve como intenção).

| Teste | VUs pico | RPS | p95 dashboard | p95 patients | Falhas | Causa |
|---|---|---|---|---|---|---|
| Carga moderada | 40 | 64/s | 492 ms | 279 ms | 0,08% | ruído de rede (status 0, sem erro na API) |
| Estresse | 240 | 92/s | 7,12 s | 5,49 s | 2,50% | **182× QueuePool timeout** |

---

## 1. Banco de Dados

**Diagnóstico:** não há índice faltando nem N+1 — é esgotamento do pool de
conexões do SQLAlchemy (`backend/database.py`), agravado por `/dashboard/stats`
manter a conexão aberta por 7 queries sequenciais (contagens + listagens +
decrypt) em vez de uma só.

**Correção 1 — pool maior, mas com teto conhecido.** Hoje: `2 workers × (5+5) = 20`
conexões. Com folga de produção (migrações, backups, admin), subir para
`pool_size=10, max_overflow=10` dá `2 × 20 = 40`, ainda com metade do
`max_connections=100` do Postgres livre:

```python
# backend/database.py — valores atuais via env, sem mudar código:
# docker-compose.prod.yml ou .env de produção
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=10
```

**Correção 2 — falhar rápido de verdade.** `DB_POOL_TIMEOUT=10` faz o usuário
esperar 10s por um 500. O comentário do próprio arquivo já diz que a intenção
era falhar rápido; baixar para 2–3s cumpre isso e libera a thread mais rápido
sob pico:

```python
DB_POOL_TIMEOUT=3
```

**Correção 3 — menos round-trips em `/dashboard/stats`.** As 4 contagens
(`stmt_pacientes`, `stmt_sessoes`, `stmt_records`, `stmt_upcoming`) podem virar
uma única query com `func.count` condicional, reduzindo de 4 idas ao banco
para 1 e encurtando o tempo que a conexão fica presa:

```python
stmt = select(
    func.count().filter(Patient... ).label("total_patients"),  # via subquery/CTE
    ...
)
```
*(peço aprovação antes de aplicar — toca `server.py`, mexe em query de
produção; o diff completo cabe num PR separado.)*

**Correção 4 (fora deste ambiente local) — PgBouncer.** O próprio comentário em
`database.py` já recomenda: acima de ~4 workers, por `database.py`, "para passar
disso, use um PgBouncer na frente do banco" — multiplexa conexões em vez de uma
pool fixa por processo.

---

## 2. Servidor / Workers (Gunicorn)

**Diagnóstico:** nenhum worker travou nem bateu `WORKER_TIMEOUT` (60s); os
"Worker exiting" no log são só o reciclo normal por `max_requests=2000`. O
problema não é o Gunicorn — é o `WEB_CONCURRENCY` não ter relação com a VPS
real.

**Achado da Fase 2 (vale registrar aqui):** sem `WEB_CONCURRENCY` explícito,
`gunicorn.conf.py` usa `multiprocessing.cpu_count()` da máquina onde builda —
24 workers nesta máquina de dev, não os 2 vCPU da VPS do `docker-compose.prod.yml`
(`cpus: "1.0"` só pros serviços db/redis hoje; `api` não tem limite).

**Correção:**
```bash
# .env de produção
WEB_CONCURRENCY=2   # fixo, não depender do host onde a imagem é construída
```
E lembrar sempre a conta ao mudar: `workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
< max_connections do Postgres`, com folga.

---

## 3. Cache (Redis)

**Diagnóstico:** Redis hoje só guarda contadores de rate limit
(`backend/rate_limit.py`); nenhuma rota cacheia dado de negócio. `/dashboard/stats`
é a maior vítima do pico (p95 7,1s) e é exatamente o tipo de rota que o
usuário abre e atualiza várias vezes sem o dado mudar — boa candidata a cache
curto.

**Correção proposta (não aplicada — mexe em `server.py`, peço aprovação):**

```python
import json
from redis.asyncio import Redis

redis = Redis.from_url(REDIS_URL, decode_responses=True)
DASHBOARD_CACHE_TTL = 20  # segundos: curto o bastante p/ não mostrar dado velho

@api_router.get("/dashboard/stats")
async def dashboard_stats(user: User = Depends(get_active_user), db: AsyncSession = Depends(get_db)):
    cache_key = f"dashboard:{user.user_id}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)
    result = await _compute_dashboard_stats(db, user)  # o corpo atual da função
    await redis.set(cache_key, json.dumps(result), ex=DASHBOARD_CACHE_TTL)
    return result
```

Invalidação explícita (ao criar paciente/sessão/evolução) é melhor que TTL
puro, mas um TTL de 15–20s já tira a maior parte da repetição sem arriscar
dado muito desatualizado — e é a versão mais simples que resolve.

---

## 4. Infraestrutura

**Diagnóstico:** o Postgres **não** foi o limite nesta rodada —
`max_connections=100` nunca foi alcançado (o app parou de pedir conexão bem
antes disso, no próprio pool). Ou seja: **antes de pensar em upgrade de VPS,
o ajuste de pool (item 1) e o cache (item 3) têm bem mais retorno.**

Dois pontos de infraestrutura que ficaram sem resposta no `docker-compose.prod.yml`
atual, e que valem revisão num PR próprio (toca arquivo de infra — aprovação
antes de aplicar):

1. **`api` sem `deploy.resources.limits`:** `db` e `redis` têm limite de
   CPU/RAM fixado para a VPS (2 vCPU/4GB); `api` não tem nenhum. Num pico, o
   Gunicorn pode disputar recursos com o Postgres sem controle.
2. **Métricas Prometheus já existem (`/metrics`, `child_exit` em
   `gunicorn.conf.py`) mas, até onde vi, não há Grafana/alerta configurado.**
   Um esgotamento de pool como este apareceria como alerta antes de virar
   500 para o usuário, se `pool_size`/`checked_out` do SQLAlchemy fosse
   exportado.

**Projeção:** com os itens 1–3 aplicados (pool maior + timeout menor + cache no
dashboard), a mesma carga de 240 VUs deve ficar bem mais perto de 0% de falha
sem precisar de mais CPU/RAM — o teto atual é de configuração, não de
hardware.

---

## Ambiente de teste (para reproduzir)

```bash
docker compose -f docker-compose.prod.yml -f load_tests/docker-compose.override.yml \
  --env-file .env up -d --build db redis api

docker run --rm -i --network psigestaoe_psigestao \
  -v "$(pwd)/load_tests:/scripts" \
  -e BASE_URL=http://api:5000 \
  -e TEST_EMAIL=loadtest@psigestao-k6.com -e TEST_PASSWORD="CargaK6Local!2026" \
  grafana/k6 run /scripts/script.k6.js
```

**Limpeza ao terminar:** remover de `backend/.env` as linhas marcadas
"load_tests" (`RATE_LIMIT_ENABLED`, `WEB_CONCURRENCY`, `CORS_ORIGINS`) e do
`.env` da raiz as linhas `REDIS_PASSWORD`/`DOMAIN`/`ACME_EMAIL`/
`REACT_APP_BACKEND_URL` adicionadas para este teste, e `docker compose -f
docker-compose.prod.yml down -v` para descartar o Postgres local (tem só dados
fictícios do k6).
