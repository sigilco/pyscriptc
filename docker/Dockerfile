FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends clang llvm git ca-certificates curl build-essential && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy
WORKDIR /work
# Oracle interpreters for compat2 probes. Kept outside UV_PYTHON_INSTALL_DIR because /opt/python is a named volume that hides image content. Patch versions are pinned so oracle values reproduce.
ENV PYSC_ORACLE_DIR=/opt/oracles
RUN UV_PYTHON_INSTALL_DIR=/opt/oracles uv python install 3.10.22 3.11.17 3.12.15 3.13.16 3.14.9
