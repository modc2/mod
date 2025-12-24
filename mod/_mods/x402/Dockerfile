FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY x402.py .
COPY whitelist_manager.py .
COPY server.py .
COPY whitelist.json .

EXPOSE 50149

CMD ["python", "server.py"]
