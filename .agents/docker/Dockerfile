FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends clang llvm git ca-certificates curl build-essential && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy
WORKDIR /work
