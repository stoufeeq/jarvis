#!/bin/sh
set -e

case "${COMMAND:-api}" in
  worker)
    exec celery -A app.workers.celery_app worker --loglevel=info
    ;;
  beat)
    exec celery -A app.workers.celery_app beat --loglevel=info
    ;;
  *)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8002
    ;;
esac
