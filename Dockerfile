FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SYSADMIN_NO_TRAY=1

WORKDIR /app

COPY requirements.docker.txt ./
RUN pip install -r requirements.docker.txt

COPY . .

CMD ["python", "app.py", "--no-tray"]
