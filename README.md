# PsiGestão

Gestão clínica e prontuários de psicologia.

- `backend/` — API FastAPI (SQLAlchemy + Alembic)
- `frontend/` — aplicação React (CRACO + Tailwind + shadcn/ui)
- `tests/` — testes das rotas do backend (`pytest`)

## Desenvolvimento

1. Copie `.env.example` para `.env` e `backend/.env.example` para `backend/.env`,
   e preencha. O `docker compose` se recusa a subir sem `POSTGRES_PASSWORD`.
2. Rode:

```
docker compose up
```

A API sobe com recarga automática. As migrações e o índice de busca são aplicados
sozinhos pelo `backend/prestart.py`. Banco, API e frontend ficam acessíveis só
nesta máquina (`127.0.0.1`), nunca pela rede.

Trocar a senha do banco: o Postgres só lê `POSTGRES_PASSWORD` ao criar o volume.
Num banco que já existe, troque a senha dentro dele (o comando pede a senha nova,
sem deixá-la no histórico do terminal), atualize `POSTGRES_PASSWORD` no `.env` e
recrie os containers:

```
docker compose exec db psql -U admin -d psigestao -c "\password admin"
docker compose up -d --force-recreate
```

## Produção (API)

A imagem do `backend/` sobe pelo `start.sh`:

1. `prestart.py` migra o banco uma única vez, com trava no Postgres, e indexa a busca;
2. `gunicorn` inicia vários workers (`gunicorn.conf.py`).

Variáveis obrigatórias: `ENVIRONMENT=production`, `DATABASE_URL`, `JWT_SECRET`,
`FERNET_KEY`, `SEARCH_INDEX_KEY`, `CORS_ORIGINS`, `PRIVACY_*`. Recomendadas: `REDIS_URL`
(obrigatória na prática com mais de um worker), `FORWARDED_ALLOW_IPS`, `METRICS_TOKEN`,
`LOG_FORMAT=json`. Todas estão descritas em `backend/.env.example`.

Monitoramento:

- `GET /api/health/live`: o processo está de pé (healthcheck do container);
- `GET /api/health/ready`: o banco responde (use no load balancer);
- `GET /api/metrics`: Prometheus, com `Authorization: Bearer <METRICS_TOKEN>`;
- logs em JSON com `request_id`, também devolvido no cabeçalho `X-Request-ID`.
