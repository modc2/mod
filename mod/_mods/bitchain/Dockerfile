FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir python-bitcoinrpc requests

COPY . /app

CMD ["python", "bitchain.py"]