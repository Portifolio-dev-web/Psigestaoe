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

Testes do backend (na sua máquina):

```
pip install -r backend/requirements-dev.txt
cd backend && python -m pytest ../tests
```

## Dependências

**Backend.** `backend/requirements.in` lista só o que a API usa, com o motivo de cada
pacote. O `requirements.txt` é **gerado** a partir dele, com todas as versões travadas
e o hash de cada arquivo (o Dockerfile instala com `--require-hashes`). As ferramentas
de teste ficam em `requirements-dev.in` e não entram na imagem. Para adicionar ou
atualizar um pacote, edite o `.in` e gere os dois `.txt` (rode na pasta `backend/`):

```
docker run --rm -v "$PWD":/work -w /work python:3.11-slim sh -c "pip install -q uv && \
  uv pip compile requirements.in --universal --python-version 3.11 --generate-hashes -o requirements.txt && \
  uv pip compile requirements-dev.in --universal --python-version 3.11 --generate-hashes -o requirements-dev.txt"
```

Para subir um pacote específico, acrescente `--upgrade-package <nome>` ao primeiro comando.

**Frontend.** Pacotes com versão exata no `package.json`. Ao adicionar um, confira que
ele é realmente importado em `src/`: pacotes sem uso foram removidos na varredura de
segurança (item 13) e não devem voltar.

Antes de cada deploy, confira as vulnerabilidades conhecidas (por exemplo, em
[osv.dev](https://osv.dev) ou com `pip-audit` e `yarn audit`).

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
