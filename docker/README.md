# Docker

A Debian-based Linux image for building and testing pyscriptc: clang, LLVM, uv, and pinned CPython oracle installs (3.10 to 3.14) for the compat2 probes.

## Build

```bash
docker build -t pyscriptc-dev docker/
```

## Run a compile

```bash
docker run --rm -v "$PWD:/work" -w /work pyscriptc-dev \
  bash -c 'uv sync && uv run pyscriptc compile hello.py -o hello && ./hello'
```

## Mount a source directory

Any directory can be mounted at `/work` (the image `WORKDIR`). To develop in a checkout of this repo:

```bash
docker run --rm -it -v "$HOME/src/pyscriptc:/work" -v pyscriptc-venv:/opt/venv -v pyscriptc-py:/opt/python pyscriptc-dev
```

The named volumes cache the uv venv and the uv-managed interpreters across runs; without them `uv sync` re-installs each time. `UV_PROJECT_ENVIRONMENT=/opt/venv` and `UV_PYTHON_INSTALL_DIR=/opt/python` are set in the image.

## Repo helper

From a checkout of this repo, `docker/run.sh` wraps `docker run` with the volume mounts above:

```bash
docker/run.sh uv run pytest -n 6 -m "not linux_container"
HEAVY=1 docker/run.sh uv run pytest -q tests/compat2
```

`HEAVY=1` takes a lock so heavy runs do not run concurrently, and mounts an empty tmpfs over the compat2 scorer holdout unless `PYSCRIPTC_EVALUATOR=1` (evaluator sessions only).

## Push to a registry

```bash
docker tag pyscriptc-dev ghcr.io/<org>/pyscriptc-dev:latest
docker push ghcr.io/<org>/pyscriptc-dev:latest
```

## Run on a cloud service

Any service that runs a Docker image with a persistent filesystem works. Example for AWS ECS/Fargate (a one-off compile task):

```bash
aws ecr create-repository --repository-name pyscriptc-dev
docker tag pyscriptc-dev <account>.dkr.ecr.<region>.amazonaws.com/pyscriptc-dev:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/pyscriptc-dev:latest
aws ecs run-task --task-definition pyscriptc-compile \
  --overrides '{"containerOverrides":[{"name":"pyscriptc","command":["bash","-c","uv sync && uv run pyscriptc compile src.py -o out"]}]}' \
  --launch-type FARGATE
```

On Cloud Run Jobs (GCP) or Azure Container Jobs, mount your source via the service's volume mechanism or bake it into a derived image (`FROM pyscriptc-dev` + `COPY src/ /work/src`). The image needs at least 2 GB of memory for a full build.
