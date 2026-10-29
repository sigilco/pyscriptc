"""A Flask app compiled by pyscriptc into one native binary.

The whole Flask tree (Flask, Werkzeug, Jinja2, click, itsdangerous) is
dynamic Python, so it rides the island floor: the binary links libpython
and runs the framework inside it. What pyscriptc adds is the deployable
shape: one file, no venv, no `pip install` on the target.

Serve it with:

    pyscriptc compile examples/web/flask_app.py --island --embed-python host --emit bin

The `--embed-python host` build links the interpreter that compiled it and
needs the framework importable at run time (`PYTHONPATH` pointing at the
site-packages that has Flask, or a venv with `VIRTUAL_ENV` set). The
`--embed-python bundle` build carries the framework with the binary; see
the guide page for the limits of that shape.
"""

from flask import Flask

app = Flask(__name__)


@app.route("/")
def index() -> str:
    return "hello from flask\n"


@app.route("/health")
def health() -> str:
    return "ok\n"


if __name__ == "__main__":
    app.run(port=8080)
