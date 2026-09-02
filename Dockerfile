# Public demo image. Everything the visitor-facing paths need is pure
# simulation, so the LLM SDK is optional and the container runs without any key.
FROM python:3.12-slim

# pandapower pulls numpy/scipy wheels; no compiler needed on slim for these.
WORKDIR /app

COPY pyproject.toml README.md ./
COPY gridpilot ./gridpilot
COPY frontend ./frontend
COPY results ./results

RUN pip install --no-cache-dir . \
 && pip install --no-cache-dir anthropic

# Render, Railway and Fly all inject $PORT. Default to 8000 for local runs.
ENV PORT=8000 GRIDPILOT_COUNTS=/tmp/gridpilot_counts.json
EXPOSE 8000

CMD ["sh", "-c", "uvicorn gridpilot.server:app --host 0.0.0.0 --port ${PORT}"]
