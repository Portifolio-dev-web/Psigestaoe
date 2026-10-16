# Deploy em VPS com Docker — passo a passo

> Guia completo para colocar o PsiGestão no ar numa VPS própria, com HTTPS
> automático, backup fora do servidor e monitoramento (inclusive pelo
> celular). Usa os arquivos `docker-compose.prod.yml`, `Caddyfile`,
> `backup.sh` e `deploy.sh` da raiz do repositório — todos já prontos.
>
> Antes de começar: revise `docs/plano-resposta-incidentes.md` e os textos
> legais (`backend/legal.py`) — ainda precisam de revisão de advogado antes
> de valer para clientes pagantes (ver README.md).

## Fornecedores recomendados

Uma empresa por serviço, critério de peso maior primeiro — nenhuma indicação
é exclusiva, mas as listadas aqui têm histórico que justifica a escolha.
Confirme preço e disponibilidade atual no site de cada uma antes de
contratar (muda com frequência).

| Serviço | Recomendado | Por quê | Alternativa |
|---|---|---|---|
| **VPS** (seção 1) | [Vultr](https://www.vultr.com/) — região São Paulo | Tem região Brasil (obrigatório, LGPD art. 33); suporte a IP fixo por serviço (precisa para o Caddy) | [DigitalOcean](https://www.digitalocean.com/) (sem região BR hoje — só use se abrir mão do critério Brasil) |
| **Domínio `.com.br`** (seção 4) | [Registro.br](https://registro.br/) | É o próprio órgão registrador (NIC.br) — comprar direto evita intermediário e repasse de preço | Revenda só se já usar um provedor de hospedagem que inclua registro (ex.: Hostinger) |
| **Armazenamento do backup** (seção 9) | [Backblaze B2](https://www.backblaze.com/b2) | Mais barato do mercado para este volume (poucos GB/mês); compatível com restic/S3 | [Wasabi](https://wasabi.com/) (preço mais previsível em volume maior, sem taxa de saída) |
| **E-mail transacional/SMTP** (seção 7) | [Resend](https://resend.com/) | Feito para desenvolvedor, API simples, 3.000 e-mails/mês grátis — compatível com o `mailer.py` do projeto | [Brevo](https://www.brevo.com/) (300/dia grátis, sem limite de validade) — bom se o volume crescer rápido |
| **Erros do backend e do navegador** (seção 10) | [Sentry](https://sentry.io/) (região UE) | Já integrado no código (`observability.py`, `lib/sentry.js`); plano grátis cobre o volume de um piloto | — (já é a única opção ligada ao código) |
| **Site e API no ar** (seção 10) | [UptimeRobot](https://uptimerobot.com/) | Plano grátis com até 50 monitores e checagem a cada 5 min, app Android com notificação push | [Healthchecks.io](https://healthchecks.io/) também monitora URLs, mas é mais focado em jobs agendados (ver linha abaixo) |
| **Backup rodou?** (seção 9) | [Healthchecks.io](https://healthchecks.io/) | Feito para "isto devia ter rodado" (cron, jobs) — exatamente o caso do `backup.sh`; grátis até 20 checks | — |
| **Acesso administrativo** (seção 5) | [Tailscale](https://tailscale.com/) | Rede privada entre seus aparelhos sem abrir porta nenhuma; grátis até 100 dispositivos, app Android maduro | [WireGuard](https://www.wireguard.com/) puro (mais trabalho manual de configurar, sem a camada de gerência do Tailscale) |
| **SSH pelo celular** (seção 11) | [Termius](https://termius.com/) | App Android estável, salva atalhos de comando, funciona bem com Tailscale | [JuiceSSH](https://juicessh.com/) (mais simples, só Android) |
| **Login social** | [Google Cloud Console](https://console.cloud.google.com/) | Não é uma escolha — é quem emite o `GOOGLE_CLIENT_ID` já usado no código | — |

**Sobre e-mail**: o mercado mudou nos últimos anos — SendGrid tirou o plano
grátis permanente, Mailgun também, depois de comprada pela Sinch. Resend e
Brevo são as opções atuais com plano grátis real. Se o volume de e-mails
crescer muito (milhares/dia), considere o Amazon SES (mais barato em escala,
mas exige configurar DKIM/SPF e lidar com bounces manualmente) ou o Postmark
(mais caro, mas o histórico de entrega mais consistente do mercado).

**Sobre o VPS fora do Brasil**: se algum dia topar abrir mão da região
Brasil (não recomendado para dado de saúde — LGPD art. 33), a Hetzner tem o
melhor custo-benefício do mercado europeu, mas não tem região Brasil hoje.

## Sumário

1. [Escolher e criar a VPS](#1-escolher-e-criar-a-vps)
2. [Primeiro acesso e segurança básica](#2-primeiro-acesso-e-segurança-básica)
3. [Instalar Docker](#3-instalar-docker)
4. [Domínio e DNS](#4-domínio-e-dns)
5. [Acesso administrativo seguro (Tailscale)](#5-acesso-administrativo-seguro-tailscale)
6. [Clonar o projeto e gerar os segredos](#6-clonar-o-projeto-e-gerar-os-segredos)
7. [Variáveis de ambiente — checklist completo](#7-variáveis-de-ambiente--checklist-completo)
8. [Primeira subida](#8-primeira-subida)
9. [Backup automatizado](#9-backup-automatizado)
10. [Monitoramento](#10-monitoramento)
11. [Monitorar e operar pelo celular Android](#11-monitorar-e-operar-pelo-celular-android)
12. [Deploy de novas versões](#12-deploy-de-novas-versões)
13. [Reverter um deploy](#13-reverter-um-deploy)
14. [Checklist final antes de divulgar o link](#14-checklist-final-antes-de-divulgar-o-link)

---

## 1. Escolher e criar a VPS

**Recomendado**: 2 vCPU / 4 GB RAM / ~60-80 GB SSD, região **São Paulo**.
Dados de saúde (prontuários) devem ficar no Brasil — LGPD art. 33, transferência
internacional. A Vultr tem região São Paulo nesse porte (confirme o preço
atual em vultr.com antes de contratar; provedores mudam preço com frequência).
DigitalOcean/AWS/Azure/Oracle também têm opções em SP — compare, mas mantenha
o critério "região Brasil" como obrigatório, não negociável.

Sistema operacional: **Ubuntu 24.04 LTS** (suporte até 2029, o que os guias
de Docker/Caddy assumem por padrão).

Ao criar a VPS:
- adicione sua **chave SSH pública** na criação (não defina senha de root);
- anote o **IP público** — vai precisar dele no DNS (passo 4);
- ligue **snapshots automáticos** do provedor, se oferecido (rede de segurança
  além do backup do passo 9, não substitui).

## 2. Primeiro acesso e segurança básica

```bash
ssh root@SEU_IP
```

Crie um usuário comum (nunca opere como root no dia a dia):

```bash
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
```

Em outro terminal, confirme que entra como `deploy` **antes** de desligar o
acesso root:

```bash
ssh deploy@SEU_IP
```

Desligue login root e por senha (só chave):

```bash
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

Firewall — só HTTP/HTTPS abertos (SSH fecha de verdade no passo 5, com
Tailscale; até lá, deixe a porta 22 liberada para não se trancar fora):

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Atualizações de segurança automáticas:

```bash
sudo apt update && sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 3. Instalar Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker deploy
```

Saia e entre de novo (`exit`, depois `ssh deploy@SEU_IP`) para o grupo
`docker` valer. Confirme:

```bash
docker compose version
```

## 4. Domínio e DNS

No painel do seu domínio, crie um registro **A** apontando para o IP da VPS:

```
app.seudominio.com.br   A   SEU_IP_DA_VPS
```

Espere propagar (minutos a poucas horas; confira com `dig app.seudominio.com.br`
ou `nslookup`). O Caddy (passo 8) só consegue emitir o certificado HTTPS
depois que o DNS já aponta para a VPS.

**Um domínio só** para frontend e API (a API fica em `/app.seudominio.com.br/api`,
não num subdomínio separado) — o cookie de sessão é `SameSite=Strict` e exige
o mesmo site (ver README.md, "Endereços do deploy"). Não crie `api.seudominio.com.br`
como domínio separado para isto.

## 5. Acesso administrativo seguro (Tailscale)

Em vez de deixar a porta SSH (22) aberta para a internet inteira, instale o
[Tailscale](https://tailscale.com/) (rede privada entre seus aparelhos,
gratuito até 100 dispositivos):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Abra o link que aparecer, entre com sua conta (Google/GitHub/e-mail). A VPS
ganha um IP privado tipo `100.x.y.z`, acessível só pelos seus próprios
aparelhos autenticados na mesma conta Tailscale.

Agora feche o SSH da internet pública de verdade:

```bash
sudo ufw delete allow 22/tcp
sudo ufw allow in on tailscale0 to any port 22
```

Dali em diante, acesse com `ssh deploy@100.x.y.z` (o IP do Tailscale), de
qualquer aparelho — inclusive o celular (passo 11).

## 6. Clonar o projeto e gerar os segredos

```bash
cd ~
git clone https://github.com/SEU_USUARIO/Psigestaoe.git
cd Psigestaoe
cp .env.example .env
cp backend/.env.example backend/.env
```

Gere cada segredo (um comando por linha; cole o resultado no `.env` certo):

```bash
# POSTGRES_PASSWORD, REDIS_PASSWORD (.env da raiz)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# JWT_SECRET (.env da raiz) — mínimo 32 caracteres
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# FERNET_KEY (.env da raiz) — formato específico, use este comando (não o de cima)
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# SEARCH_INDEX_KEY (.env da raiz) — mínimo 32 caracteres
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Sem Python na VPS ainda? `sudo apt install -y python3` (rápido, só para gerar
os segredos — o backend roda inteiro dentro do container, não precisa de
Python instalado no host).

Edite os dois arquivos com `nano .env` e `nano backend/.env`, usando a
tabela da próxima seção como checklist.

## 7. Variáveis de ambiente — checklist completo

Preencha no `.env` da raiz, salvo indicação contrária. Sem as marcadas
**obrigatória**, o servidor se recusa a subir (fail-closed, por design).

| Variável | Onde | Obrigatória? | O que é |
|---|---|---|---|
| `POSTGRES_PASSWORD` | raiz | **Sim** | Senha do Postgres — gerada no passo 6 |
| `REDIS_PASSWORD` | raiz | **Sim** | Senha do Redis — gerada no passo 6 |
| `JWT_SECRET` | raiz | **Sim** | Assina a sessão — gerada no passo 6 |
| `FERNET_KEY` | raiz | **Sim** | Cifra os prontuários — gerada no passo 6. **Guarde fora do servidor e do backup** (cofre de senhas + cópia offline); perdida, os dados não voltam |
| `SEARCH_INDEX_KEY` | raiz | **Sim** | Índice de busca cego — gerada no passo 6 |
| `DOMAIN` | raiz | **Sim** | `app.seudominio.com.br`, sem `https://` |
| `ACME_EMAIL` | raiz | **Sim** | Seu e-mail, para avisos do Let's Encrypt |
| `REACT_APP_BACKEND_URL` | raiz | **Sim** | `https://app.seudominio.com.br` (mesmo domínio, sem `/api`) |
| `CORS_ORIGINS` | raiz | **Sim** | `https://app.seudominio.com.br` (só `https://`) |
| `FRONTEND_URL` | raiz | Recomendada | Mesma URL acima — usada nos links de e-mail |
| `ENVIRONMENT` | — | — | **Apague esta linha** no `.env` de produção (sem ela = produção; é assim por segurança) |
| `PRIVACY_CONTROLLER_NAME` / `_DOCUMENT` | raiz | **Sim** | Dados reais de quem opera o sistema (LGPD art. 41) |
| `PRIVACY_DPO_NAME` / `_EMAIL` | raiz | **Sim** | Encarregado de dados real |
| `SUPPORT_EMAIL` | raiz | Recomendada | Aparece na página de Ajuda |
| `ADMIN_EMAILS` | raiz | Recomendada | Seu e-mail, para acessar `/administracao` |
| `METRICS_TOKEN` | raiz | Recomendada | Gere com o mesmo comando do JWT_SECRET; protege `/api/metrics` |
| `SENTRY_DSN` | raiz | Opcional | Projeto no sentry.io (erros do backend) |
| `GOOGLE_CLIENT_ID` | `backend/.env` | Opcional | Login com Google (backend) |
| `REACT_APP_GOOGLE_CLIENT_ID` | raiz | Opcional | Mesmo client ID, para o navegador |
| `REACT_APP_SENTRY_DSN` | — | Opcional | **Não existe no compose de produção ainda** — é uma variável de build do frontend; se for usar, acrescente ao `docker-compose.prod.yml` (serviço `web`, em `build.args`), igual a `REACT_APP_BACKEND_URL` |
| `SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` | `backend/.env` | **Sim** | Qualquer provedor (Resend, Brevo, SES, SendGrid...). Configure SPF/DKIM/DMARC no domínio do `SMTP_FROM`, senão os links de cadastro caem no spam |
| `BILLING_PROVIDER` | raiz | — | Deixe **vazio** até ter um meio de pagamento real; nunca `simulado` em produção (o servidor recusa) |
| `RESTIC_REPOSITORY` / `RESTIC_PASSWORD` | raiz | Para o backup | Ver passo 9 |
| `BACKUP_HEALTHCHECK_URL` | raiz | Para o backup | Ver passo 9/10 |

Depois de preencher, confira rapidamente se esqueceu algo óbvio:

```bash
grep -E "^(POSTGRES_PASSWORD|REDIS_PASSWORD|JWT_SECRET|FERNET_KEY|SEARCH_INDEX_KEY|DOMAIN|ACME_EMAIL)=" .env | grep -E "=$|troque-esta|gere-um"
```

Se esse comando imprimir alguma linha, ela ainda está com o valor de
exemplo — preencha antes de continuar.

## 8. Primeira subida

```bash
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
```

A primeira construção das imagens demora alguns minutos. O Caddy só consegue
emitir o certificado HTTPS depois que a porta 443 responde E o DNS já aponta
para a VPS (passo 4) — se o certificado falhar na primeira tentativa, o
Caddy tenta de novo sozinho.

Acompanhe:

```bash
docker compose -f docker-compose.prod.yml logs -f --tail=50
```

Quando `api`, `web`, `db`, `redis` e `caddy` aparecerem como `healthy` em:

```bash
docker compose -f docker-compose.prod.yml ps
```

...abra `https://app.seudominio.com.br` no navegador. Cadastre-se pelo fluxo
normal (não entre direto por nenhum atalho) para confirmar que e-mail,
criação de senha e login funcionam de ponta a ponta.

**Primeiro administrador**: depois de criar sua conta normalmente e confirmar
o e-mail, confira se `ADMIN_EMAILS` no `.env` tem o seu e-mail, reinicie a API
(`docker compose -f docker-compose.prod.yml restart api`) e ative a
verificação em duas etapas na sua conta (`/administracao` exige isso).

## 9. Backup automatizado

Crie uma conta num serviço de armazenamento compatível com S3, **fora da
VPS** — [Backblaze B2](https://www.backblaze.com/b2) é o mais barato para
este volume (poucos GB de dump do Postgres). Crie um bucket privado e uma
chave de aplicação.

No `.env` da raiz:

```bash
RESTIC_REPOSITORY=b2:nome-do-seu-bucket:psigestao
RESTIC_PASSWORD=   # gere com o mesmo comando do JWT_SECRET — guarde junto da FERNET_KEY, nunca no backup
```

As credenciais do B2 em si (`B2_ACCOUNT_ID`, `B2_ACCOUNT_KEY`) são lidas pelo
`restic`/`rclone` por fora deste projeto — exporte-as no ambiente de quem
agenda o cron (próximo passo), não no `.env` do app.

Instale o restic e teste manualmente uma vez:

```bash
sudo apt install -y restic
cd ~/Psigestaoe
set -a; source .env; set +a
export B2_ACCOUNT_ID=sua_key_id
export B2_ACCOUNT_KEY=sua_application_key
./backup.sh
```

Crie uma conta grátis no [Healthchecks.io](https://healthchecks.io/), crie um
check novo e copie a URL de ping para `BACKUP_HEALTHCHECK_URL` no `.env`.

Agende no cron (todo dia às 3h):

```bash
crontab -e
```

```
0 3 * * * cd /home/deploy/Psigestaoe && set -a && . ./.env && set +a && export B2_ACCOUNT_ID=sua_key_id B2_ACCOUNT_KEY=sua_application_key && ./backup.sh >> /home/deploy/backup.log 2>&1
```

**Teste a restauração a cada 3 meses** (README.md tem o comando de
`pg_restore`) — backup que nunca foi restaurado não é backup.

## 10. Monitoramento

| O quê | Como |
|---|---|
| Erros do backend | Crie um projeto no [sentry.io](https://sentry.io) (região UE), cole o DSN em `SENTRY_DSN` no `.env`, reinicie a API |
| Erros do navegador | Crie um 2º projeto Sentry (tipo "React"); o DSN vai como `build.args` do serviço `web` no `docker-compose.prod.yml` (acrescente `REACT_APP_SENTRY_DSN: ${REACT_APP_SENTRY_DSN}`, igual ao `REACT_APP_BACKEND_URL` já existente) |
| Site e API no ar | Conta grátis no [UptimeRobot](https://uptimerobot.com/): um monitor HTTPS em `https://app.seudominio.com.br` e outro em `https://app.seudominio.com.br/api/health/ready` |
| Backup rodou | Já coberto pelo Healthchecks.io do passo 9 |
| Métricas (Prometheus) | `https://app.seudominio.com.br/api/metrics`, com `Authorization: Bearer SEU_METRICS_TOKEN` — opcional, só se for usar Grafana/Prometheus de verdade |

Depois de configurar o Sentry/UptimeRobot, liste-os como terceiros na
Política de Privacidade (`backend/legal.py`) — eles recebem metadados
técnicos (nunca dado de paciente, mas ainda assim são terceiros que tratam
dado do profissional).

## 11. Monitorar e operar pelo celular Android

Com o acesso pelo Tailscale (passo 5), dá para resolver quase tudo sem
computador:

| App (Play Store) | Para quê |
|---|---|
| **Tailscale** | Liga o celular à mesma rede privada da VPS — pré-requisito dos dois abaixo |
| **Termius** (ou JuiceSSH) | Terminal SSH. Conecte em `deploy@100.x.y.z` (IP do Tailscale). Salve atalhos: `docker compose -f docker-compose.prod.yml logs -f --tail=100 api`, `docker compose -f docker-compose.prod.yml restart api`, `docker compose -f docker-compose.prod.yml ps` |
| **UptimeRobot** | Notificação push se o site ou a API caírem |
| **Healthchecks.io** | Não tem app próprio: abra o site pelo navegador do celular (ou ative alertas por Telegram/e-mail na conta) para saber se o backup falhou |
| **Sentry** | App oficial (ou o navegador) — notificação de erro novo com `request_id`, para cruzar com os logs no Termius |
| **GitHub** | App oficial — acompanha pull requests e Actions (CI) do repositório |
| **Telegram** (opcional) | Centraliza os alertas: Healthchecks.io, UptimeRobot e Sentry sabem mandar aviso por bot do Telegram, se preferir tudo num lugar só em vez de vários apps |

**Fluxo de um alerta recebido no celular:**
1. notificação chega (UptimeRobot/Sentry/Healthchecks.io);
2. abra o Termius, `docker compose -f docker-compose.prod.yml logs --tail=100 <serviço>` para entender;
3. conter: `docker compose -f docker-compose.prod.yml restart <serviço>` (ou, se o deploy mais recente é o culpado, o rollback do passo 13);
4. se envolveu dado pessoal, siga `docs/plano-resposta-incidentes.md`.

## 12. Deploy de novas versões

Da sua máquina, suba o código normalmente (`git push`). Na VPS:

```bash
cd ~/Psigestaoe
./deploy.sh
```

O script (raiz do projeto) faz: dump de segurança → `git pull` → reconstrói
as imagens → sobe de novo (a migração do banco roda sozinha, com trava) →
espera a API responder saudável.

## 13. Reverter um deploy

Se um deploy quebrar algo:

```bash
cd ~/Psigestaoe
git log --oneline -5          # ache o commit anterior, o que funcionava
git checkout <hash-do-commit-anterior>
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
git checkout main             # volta a HEAD depois (sem subir de novo)
```

Se a migração do banco do deploy quebrado já alterou o schema de um jeito
incompatível com a versão anterior, restaure o dump de segurança mais
recente de `_deploy_dumps/` (comando de restauração no README.md).

## 14. Checklist final antes de divulgar o link

- [ ] Cadastro, confirmação por e-mail, login e 2FA testados de ponta a ponta
- [ ] Login com Google testado (se configurado) — tela de consentimento OAuth
      fora do modo de teste no Google Cloud Console
- [ ] `ADMIN_EMAILS` com o seu e-mail; 2FA ativada na sua conta;
      `/administracao` acessível
- [ ] Backup rodou pelo menos uma vez com sucesso (ping verde no Healthchecks.io)
- [ ] Restauração do backup testada pelo menos uma vez
- [ ] UptimeRobot monitorando o site e `/api/health/ready`
- [ ] Sentry recebendo eventos de teste (backend e frontend, se configurados)
- [ ] Política de Privacidade e Termos de Uso revisados por advogado
      (README.md já sinaliza isso — textos atuais são modelos)
- [ ] `docs/plano-resposta-incidentes.md` com os nomes/contatos reais
      preenchidos (hoje tem placeholders)
- [ ] `FERNET_KEY` guardada no cofre de segredos + cópia offline, fora da VPS
      e fora do backup
