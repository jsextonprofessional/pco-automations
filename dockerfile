# Fly.io deploy for the PCO MCP server (Stage 4).
FROM python:3.13-slim

WORKDIR /app

# Pull current OS security patches at build time — the base image tag
# is fixed at publish time and accumulates known vulnerabilities in
# system packages between then and whenever you build from it.
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x entrypoint.sh

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080

ENTRYPOINT ["./entrypoint.sh"]