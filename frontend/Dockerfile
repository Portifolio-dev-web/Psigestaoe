# frontend/Dockerfile
# ---------------------------------------------------------------------------
# Imagem do frontend em estágios (SEGURANÇA, varredura itens 12, 13 e 16).
#
# Antes havia um estágio só, que rodava o servidor de DESENVOLVIMENTO
# (yarn start) em qualquer ambiente, inclusive se fosse publicado. Agora:
#   dev        -> servidor de desenvolvimento, usado pelo docker-compose.yml;
#   build      -> gera os arquivos estáticos de produção (sem source maps);
#   production -> nginx sem root servindo SÓ esses arquivos (nginx.conf).
#
# Produção (rode na pasta frontend/):
#   docker build --target production \
#     --build-arg REACT_APP_BACKEND_URL=https://api.seudominio.com.br \
#     --build-arg REACT_APP_GOOGLE_CLIENT_ID=SEU_ID.apps.googleusercontent.com \
#     -t psigestao-web .
# A imagem escuta na porta 8080.
# ---------------------------------------------------------------------------

# --- Dependências (compartilhadas pelos estágios) ---
# SEGURANÇA ALTA: Node 20 saiu de suporte em abril de 2026 (sem correções de
# segurança desde então). Node 24 é a versão LTS ativa, com suporte até abril de 2028.
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json yarn.lock ./
# --frozen-lockfile: instala EXATAMENTE as versões do yarn.lock e falha se ele
# estiver desatualizado. Antes: `yarn install || npm install --legacy-peer-deps`,
# que podia instalar versões diferentes das revisadas (varredura, item 13).
RUN yarn install --frozen-lockfile --network-timeout 600000

# --- Desenvolvimento (docker-compose.yml: target dev) ---
FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["yarn", "start"]

# --- Build de produção ---
FROM deps AS build
COPY . .
# Valores PÚBLICOS embutidos no JavaScript (não são segredos: qualquer visitante
# os vê). O .env de desenvolvimento NÃO entra na imagem (.dockerignore).
ARG REACT_APP_BACKEND_URL
ARG REACT_APP_GOOGLE_CLIENT_ID
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL \
    REACT_APP_GOOGLE_CLIENT_ID=$REACT_APP_GOOGLE_CLIENT_ID \
    GENERATE_SOURCEMAP=false \
    INLINE_RUNTIME_CHUNK=false
RUN test -n "$REACT_APP_BACKEND_URL" || (echo "Informe --build-arg REACT_APP_BACKEND_URL (endereço da API)." && exit 1)
RUN yarn build
# Trava de segurança: o build falha se sobrar algum source map.
RUN if find build -name "*.map" | grep -q .; then echo "ERRO: source maps no build de produção." && exit 1; fi
# A CSP do nginx libera a página para falar SÓ com a API: a origem dela vem do
# mesmo REACT_APP_BACKEND_URL usado no JavaScript, então as duas nunca divergem.
RUN API_ORIGIN="$(node -e 'console.log(new URL(process.env.REACT_APP_BACKEND_URL).origin)')" \
    && sed "s|__API_ORIGIN__|${API_ORIGIN}|g" nginx.conf > /tmp/default.conf

# --- Produção ---
# nginx-unprivileged: roda como usuário comum (uid 101), não como root.
FROM nginxinc/nginx-unprivileged:1.30-alpine AS production
COPY --from=build /tmp/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
