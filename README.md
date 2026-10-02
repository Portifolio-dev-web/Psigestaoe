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
`FERNET_KEY`, `SEARCH_INDEX_KEY`, `CORS_ORIGINS` (só `https://`), `PRIVACY_*`, `REDIS_URL` e
`FORWARDED_ALLOW_IPS`; sem as duas últimas o servidor não sobe. Recomendadas: `METRICS_TOKEN`,
`LOG_FORMAT=json`. Todas estão descritas em `backend/.env.example`.

Monitoramento:

- `GET /api/health/live`: o processo está de pé (healthcheck do container);
- `GET /api/health/ready`: o banco responde (use no load balancer);
- `GET /api/metrics`: Prometheus, com `Authorization: Bearer <METRICS_TOKEN>`;
- logs em JSON com `request_id`, também devolvido no cabeçalho `X-Request-ID`.

## Produção (frontend)

O `frontend/Dockerfile` tem estágios: o `dev` (usado pelo `docker compose`) e o
`production`, um nginx sem root que serve só os arquivos do `yarn build`
(`frontend/nginx.conf`). Rode na pasta `frontend/`:

```
docker build --target production \
  --build-arg REACT_APP_BACKEND_URL=https://api.seudominio.com.br \
  --build-arg REACT_APP_GOOGLE_CLIENT_ID=SEU_ID.apps.googleusercontent.com \
  -t psigestao-web .
```

A imagem escuta na porta `8080` (healthcheck em `/healthz`). O build:

- não gera source maps (o código-fonte não fica legível no navegador);
- não embute a URL do túnel de desenvolvimento: o webhook do Google Forms é
  `REACT_APP_BACKEND_URL` + `/api/webhook/google-forms`;
- envia cabeçalhos de segurança, inclusive uma CSP que só libera a API e o login
  do Google. Ao incluir um serviço externo novo (outro script, fonte ou API),
  acrescente o domínio dele na CSP do `nginx.conf`, senão o navegador o bloqueia.

A API precisa listar o endereço do frontend em `CORS_ORIGINS`.

## Endereços do deploy (frontend e API)

O login usa um cookie `__Host-access_token` com `SameSite=Strict`: o navegador
só o envia quando a requisição parte do **mesmo site** da API. Por isso o
frontend e a API precisam ficar sob o **mesmo domínio registrado** e com HTTPS.

| Frontend | API | Funciona? |
|---|---|---|
| `https://app.seudominio.com.br` | `https://api.seudominio.com.br` | Sim (mesmo site) |
| `https://seudominio.com.br` | `https://api.seudominio.com.br` | Sim (mesmo site) |
| `https://psigestao.vercel.app` | `https://psigestao-api.onrender.com` | **Não**: o login parece funcionar, mas o cookie nunca é enviado e a pessoa volta para a tela de login |
| `https://web.onrender.com` | `https://api.onrender.com` | **Não**: subdomínios de plataformas (`vercel.app`, `onrender.com`, `herokuapp.com`, `netlify.app`...) contam como sites diferentes |

Use um domínio próprio e aponte os dois subdomínios para os serviços
(registros DNS do tipo CNAME). Alternativa ainda mais simples: servir a API no
mesmo endereço do frontend, em `/api`, por um proxy reverso; aí nem o CORS entra
em jogo.

Variáveis que precisam bater entre si:

- frontend (build): `REACT_APP_BACKEND_URL=https://api.seudominio.com.br`;
- API: `CORS_ORIGINS=https://app.seudominio.com.br` (só `https://`; a API recusa subir com `http://` em produção);
- API: `FORWARDED_ALLOW_IPS` com o IP do proxy da plataforma (ver `backend/.env.example`).

Para conferir depois do deploy: entre no sistema e, nas ferramentas do navegador
(Aplicativo > Cookies), veja o cookie `__Host-access_token` no endereço da API;
recarregar a página deve manter a pessoa logada.
