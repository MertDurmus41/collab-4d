# Build from the repository root:
#   docker build -f server/Dockerfile -t c4d-collab-server .
#   docker run -p 47800:47800 -e C4D_COLLAB_PASSWORD=secret c4d-collab-server
FROM python:3.12-slim

RUN useradd --create-home collab
WORKDIR /app
COPY plugin/c4d_collab/c4d_collab_lib/__init__.py lib/c4d_collab_lib/__init__.py
COPY plugin/c4d_collab/c4d_collab_lib/core lib/c4d_collab_lib/core
COPY server/run_server.py run_server.py

USER collab
EXPOSE 47800
ENV PYTHONUNBUFFERED=1
CMD ["python", "run_server.py"]
