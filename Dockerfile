FROM python:3.12-slim

WORKDIR /app

# Install dependencies separately for layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir meshcore httpx pyyaml

# Copy source
COPY src/ src/

# Config is mounted at runtime, not baked into the image
CMD ["python", "-m", "meshcore_ha_bridge"]
