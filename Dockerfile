FROM python:3.12-slim

WORKDIR /app

# Copia os arquivos de dependência primeiro (otimiza o cache do Docker)
# Se o seu arquivo se chamar system_deps.txt ou outro nome, altere abaixo
COPY requirements.txt . 
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Comando padrão para iniciar o Flask
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5000", "--reload"]