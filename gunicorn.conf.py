# Scanned PDFs may require OCR and document-layout analysis before the request
# can complete. Gunicorn's default 30-second timeout is too short for that work.
workers = 5
timeout = 600
graceful_timeout = 60
