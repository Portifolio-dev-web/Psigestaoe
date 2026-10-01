# PsiGestão

Gestão clínica e prontuários de psicologia.

- `backend/` — API FastAPI (SQLAlchemy + Alembic)
- `frontend/` — aplicação React (CRACO + Tailwind + shadcn/ui)
- `tests/` — testes das rotas do backend (`pytest`)

## Desenvolvimento

```
docker compose up
```

A API sobe com recarga automática. As migrações e o índice de busca são aplicados
sozinhos pelo `backend/prestart.py`.

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
