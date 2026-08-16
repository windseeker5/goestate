"""WSGI entry point — used by `flask --app wsgi <command>` (init-db,
verify-vec) and for production WSGI servers. For day-to-day dev, use
`python app.py` instead (see app.py)."""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host=app.config["HOST"], port=app.config["PORT"])
