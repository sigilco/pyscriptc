"""A FastAPI app compiled by pyscriptc into one native binary.

FastAPI, Starlette, uvicorn and pydantic are dynamic Python, so they ride
the island floor: the binary links libpython and runs the framework inside
it. The deployable shape is the same as the Flask example: one binary, no
venv, no `pip install` on the target.

Serve it with:

    pyscriptc compile examples/web/fastapi_app.py --island --embed-python host --emit bin

The `--embed-python host` build links the interpreter that compiled it and
needs the framework importable at run time (`PYTHONPATH` pointing at the
site-packages that has FastAPI, or a venv with `VIRTUAL_ENV` set). The
`--embed-python bundle` build carries the framework with the binary, but
pydantic-core is a compiled extension, so that shape is refused for
FastAPI; see the guide page for the limits.
"""

from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def index() -> dict:
    return {"hello": "from fastapi"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)
