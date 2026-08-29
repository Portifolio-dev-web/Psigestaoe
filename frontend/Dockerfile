FROM node:20-alpine

WORKDIR /app

# Copia arquivos de dependência
COPY package.json yarn.lock* package-lock.json* ./

# Instala dependências
RUN yarn install || npm install --legacy-peer-deps

# Copia o restante do código
COPY . .

EXPOSE 3000

CMD ["yarn", "start"]