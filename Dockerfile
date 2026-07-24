FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing pyc files to disk and buffer stdout/stderr
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy dependency definition and source code
COPY pyproject.toml README.md ./
COPY app ./app

# Install project dependencies
RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "app.agent_runtime_app"]
