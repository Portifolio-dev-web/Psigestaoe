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

E-mails do desenvolvimento (confirmação de cadastro, "Esqueci minha senha", avisos
de segurança) não saem para a internet: caem no **Mailpit**, em
<http://localhost:8025>. Para criar uma conta, cadastre-se em
<http://localhost:3000/register> e abra o link que aparece no Mailpit.

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

As vulnerabilidades conhecidas são conferidas pelo CI a cada push (ver abaixo).

## CI (GitHub Actions)

`.github/workflows/ci.yml` roda a cada push na `main` e a cada pull request:

| Etapa | O que faz | Falha quando |
|---|---|---|
| Backend | os testes (`pytest`) | algum teste falha |
| Dependências | `pip-audit` (API) e `yarn audit` (frontend) | há vulnerabilidade conhecida na API, ou em pacote do frontend que vai para o navegador, ou qualquer crítica |
| Frontend | `yarn build` com `CI=true` | o build falha ou o lint avisa algo |
| Segredos | `gitleaks` nos arquivos e nos commits novos | aparece um segredo (senha, token, chave) |
| Imagens Docker | monta as imagens de produção | um Dockerfile quebra |

No frontend, as vulnerabilidades dentro do `react-scripts` (ferramenta de build do
Create React App) são só **relatadas**: não chegam à produção, onde o nginx serve
os arquivos já compilados, e somem com a migração para o Vite (backlog). O filtro
fica em `.github/scripts/yarn_audit_gate.py`.

O `gitleaks` olha só os commits **novos**: o histórico antigo tem segredos já
vazados (e já trocados), que só saem reescrevendo o histórico. As senhas falsas dos
testes estão liberadas em `.gitleaks.toml`, e só para a regra genérica.

## Produção (API)

A imagem do `backend/` sobe pelo `start.sh`:

1. `prestart.py` migra o banco uma única vez, com trava no Postgres, e indexa a busca;
2. `gunicorn` inicia vários workers (`gunicorn.conf.py`).

Variáveis obrigatórias: `ENVIRONMENT=production`, `DATABASE_URL`, `JWT_SECRET`,
`FERNET_KEY`, `SEARCH_INDEX_KEY`, `CORS_ORIGINS` (só `https://`), `PRIVACY_*`, `REDIS_URL`,
`FORWARDED_ALLOW_IPS` e o envio de e-mail (`SMTP_HOST`, `SMTP_FROM`, `SMTP_USERNAME`,
`SMTP_PASSWORD`). Sem `REDIS_URL`, `FORWARDED_ALLOW_IPS` ou o SMTP, o servidor não sobe.
Recomendadas: `METRICS_TOKEN`, `LOG_FORMAT=json`, `FRONTEND_URL`. Todas estão descritas em
`backend/.env.example`.

Conexões criptografadas (TLS), exigidas em produção:

- banco: termine o `DATABASE_URL` com `?ssl=require` (ou `?ssl=verify-full`, que também
  confere o certificado; o certificado da autoridade vai em `PGSSLROOTCERT`);
- Redis: use `rediss://` (com dois "s") no `REDIS_URL`;
- e-mail: `SMTP_SECURITY=starttls` (porta 587) ou `ssl` (porta 465).

Só com o banco ou o Redis na **mesma máquina** ou numa rede privada isolada (ex.: o
serviço do mesmo docker compose), dá para dispensar o TLS de forma explícita:
`DATABASE_TLS_DISABLED=true` / `REDIS_TLS_DISABLED=true`.

E-mail: qualquer provedor SMTP serve (Amazon SES, SendGrid, Brevo, Mailgun, Resend,
Google Workspace...). Configure SPF, DKIM e DMARC no domínio do `SMTP_FROM`, senão os
links de cadastro e de senha caem no spam. Os links levam o token depois de `#`: ele
não aparece nos logs do servidor nem de proxies.

Manutenção automática: a API roda sozinha, a cada `MAINTENANCE_INTERVAL_HOURS` (padrão 6),
a rotina que exclui o que sobrou das contas encerradas no fim da guarda legal e avisa
por e-mail quem tem o token do Google Forms perto de vencer. Para rodar por um
agendador externo: `docker compose run --rm api python maintenance.py` (uma trava no
banco impede duas execuções ao mesmo tempo).

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

## Contas: verificação em duas etapas e encerramento

- **Cadastro com senha:** a conta só nasce quando a pessoa abre o link enviado ao
  e-mail e cria a senha. O cadastro responde sempre "enviamos um link", sem revelar
  quais e-mails já têm conta. Cadastro e login com Google continuam diretos.
- **Esqueci minha senha:** link de uso único, válido por 1 hora. Usar o link encerra as
  sessões de todos os aparelhos.
- **Verificação em duas etapas (opcional):** Configurações > Minha conta. Ao ativar,
  o sistema mostra 10 códigos de recuperação **uma única vez**. Quem perder o celular
  e os códigos precisa de um administrador do banco para desligar
  (`UPDATE users SET mfa_enabled_at = NULL, mfa_secret = NULL, mfa_recovery_codes = NULL,
  mfa_last_step = NULL WHERE email = '...'`),
  depois de confirmar a identidade da pessoa por outro meio.
- **Encerramento de conta:** Configurações > Minha conta. Antes de encerrar, a pessoa
  pode baixar tudo (ZIP com o PDF de cada paciente e um JSON completo). O que não
  está sob guarda é apagado na hora; prontuários sob guarda (5 anos, Resolução CFP
  nº 001/2009) ficam criptografados, sem acesso de ninguém, e a rotina de manutenção
  os apaga no fim do prazo, junto com a trilha de auditoria da conta.

## Backup e a chave de criptografia

Os prontuários ficam criptografados no banco com a `FERNET_KEY`. **Um backup do banco
sem a chave não serve para nada; perder a chave é perder todos os prontuários.**

Guarda da `FERNET_KEY` (e da `SEARCH_INDEX_KEY`):

- num cofre de segredos (o gerenciador de segredos da hospedagem, ou um gerenciador de
  senhas com acesso de duas pessoas de confiança), **nunca** no mesmo lugar do backup
  do banco: quem levar os dois juntos lê tudo;
- com uma cópia offline (ex.: impressa ou num pendrive cifrado, em local físico seguro);
- ao trocar a chave (`rotate_encryption_key.py`), guarde a nova **antes** de rodar a
  troca, e mantenha a antiga até conferir que tudo abre com a nova.

Backup do banco (dump lógico, além dos backups automáticos do provedor):

```
docker compose exec -T db pg_dump -U admin -d psigestao --format=custom > psigestao_$(date +%Y%m%d).dump
```

Restauração (num banco novo ou de teste):

```
docker compose exec -T db pg_restore -U admin -d psigestao --clean --if-exists < psigestao_AAAAMMDD.dump
```

Recomendações:

- guarde os dumps fora do servidor, cifrados, por pelo menos o prazo de guarda
  (5 anos);
- **teste a restauração** a cada 3 meses: suba o dump num banco de teste com a
  `FERNET_KEY` guardada e abra um prontuário. Backup que nunca foi restaurado não
  é backup;
- o `JWT_SECRET` pode ser trocado a qualquer momento (só obriga todos a entrar de
  novo); a `SEARCH_INDEX_KEY` pode ser recriada (`python rebuild_search_index.py --all`);
  a `FERNET_KEY`, não.

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
