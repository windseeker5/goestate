from flask import redirect, render_template, request, session, url_for

from app.auth import SESSION_KEY, check_credentials, is_logged_in


def register(app):
    @app.route("/")
    def index():
        if is_logged_in():
            return redirect(url_for("dashboard"))
        return render_template("blocks/landing_page.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "")
            password = request.form.get("password", "")
            user = check_credentials(email, password)
            if user is not None:
                session.clear()
                session[SESSION_KEY] = user["id"]
                return redirect(url_for("dashboard"))
            return render_template(
                "blocks/login_page.html", error="Incorrect email or password.", email=email
            ), 401
        return render_template("blocks/login_page.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))
