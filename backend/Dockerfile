FROM python:3.11-slim

# Logs saem na hora (sem buffer), o que importa para coleta de logs em produção.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copia os arquivos de dependência primeiro (otimiza o cache do Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

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
