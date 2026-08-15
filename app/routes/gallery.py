from flask import render_template


def register(app):
    @app.route("/ui/")
    def gallery_index():
        return render_template("gallery/index.html")

    @app.route("/ui/components")
    def gallery_components():
        return render_template("gallery/components.html")

    @app.route("/ui/blocks")
    def gallery_blocks():
        return render_template("gallery/blocks.html")
