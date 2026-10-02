FROM python:3.11-slim

# Logs saem na hora (sem buffer), o que importa para coleta de logs em produção.
# PYTHONDONTWRITEBYTECODE: o Python não tenta gravar .pyc na pasta do app, que
# agora é somente leitura para o processo (ver USER abaixo).
ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copia os arquivos de dependência primeiro (otimiza o cache do Docker)
COPY requirements.txt .
# DEPENDÊNCIAS (varredura, item 13): o requirements.txt agora é gerado a partir
# do requirements.in, com todas as versões travadas e o hash SHA-256 de cada
# arquivo. --require-hashes faz o pip recusar qualquer pacote cujo conteúdo
# não bata (pacote adulterado no repositório ou no caminho) e qualquer pacote
# que não esteja na lista. Ferramentas de teste NÃO entram na imagem
# (ficam em requirements-dev.txt).
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# SEGURANÇA ALTA (varredura de UI/segurança): o processo NÃO roda mais como root.
# Antes, uma falha explorável na API daria ao invasor o root do container: ler
# e alterar qualquer arquivo, instalar ferramentas e partir dali para o resto
# do servidor. Agora o app roda com um usuário sem privilégios (uid 10001) que:
#   - só LÊ o código (os arquivos abaixo continuam de root): uma invasão não
#     consegue alterar o próprio app para se manter instalada;
#   - só escreve em /tmp (métricas do Prometheus, ver start.sh);
#   - escuta na porta 5000 (acima de 1024, não precisa de privilégio).
RUN groupadd --system --gid 10001 app     && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY . .

USER app

EXPOSE 5000

# MONITORAMENTO: o Docker consulta a "vivacidade" do processo e reinicia o
# container se ela falhar. Usa python porque a imagem slim não tem curl.
# (O load balancer deve usar /api/health/ready, que também testa o banco.)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health/live', timeout=4).status == 200 else 1)"

# ESCALA: antes o comando padrão era `uvicorn --reload` (um processo, modo de
# desenvolvimento) também em produção. Agora a imagem sobe pelo start.sh:
# migra o banco uma vez e inicia o gunicorn com vários workers.
# Em desenvolvimento, o docker-compose.yml substitui por `uvicorn --reload`.
CMD ["sh", "start.sh"]
