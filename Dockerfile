FROM python:3.12-slim

WORKDIR /app

# build-essential: some transitive deps of docling/fastembed compile native
# extensions on install even when a wheel isn't available for this platform.
# libxcb1/libgl1/libglib2.0-0: docling's OCR (rapidocr) pulls in the non-headless
# opencv-python wheel, which dlopens these at import time — without them every
# upload fails with "libxcb.so.1: cannot open shared object file".
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libxcb1 \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Avoids Docling needing an MSVC/native compiler for TorchDynamo (see
# scripts/poc/README.md) — harmless on Linux too, kept for parity with dev.
ENV TORCHDYNAMO_DISABLE=1

EXPOSE 8000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "--bind", "0.0.0.0:8000", "wsgi:app"]
