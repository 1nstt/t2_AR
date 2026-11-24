# Imagen de la aplicación Python (pipeline geoespacial + cliente de PostGIS)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# El código y los datos se montan como volumen desde compose.yaml,
# así los GeoJSON y mapas generados quedan en la carpeta del proyecto.
CMD ["sh", "-c", "python scripts/main.py && python scripts/nodos_clave.py"]
