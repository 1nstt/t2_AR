# Visor web dinámico (FastAPI + Leaflet) sobre la base PostGIS del proyecto
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY web/requirements.txt /tmp/requirements-web.txt
RUN pip install --no-cache-dir -r /tmp/requirements-web.txt

# El proyecto se monta como volumen (solo lectura) desde compose.yaml
CMD ["uvicorn", "web.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "web"]
