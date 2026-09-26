FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

COPY src ./src
COPY tests ./tests
COPY smoke.py ./

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz',timeout=2)" || exit 1

CMD ["python", "-m", "src.server"]
