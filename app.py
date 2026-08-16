"""Simple dev entry point.

Usage (with the venv already activated):
    python app.py

Runs the Flask dev server in debug mode on the port set by FLASK_RUN_PORT
in .env (defaults to 5001 per .env.example, falls back to 5000 if unset).

For CLI commands (flask init-db, flask verify-vec), use wsgi.py instead:
    flask --app wsgi init-db
    flask --app wsgi verify-vec
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host=app.config["HOST"], port=app.config["PORT"])
