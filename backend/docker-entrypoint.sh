#!/bin/sh
set -e

case "${COMMAND:-api}" in
  worker)
    exec celery -A app.workers.celery_app worker --loglevel=info --pool=solo -Q celery,market_data,signals,default
    ;;
  beat)
    exec celery -A app.workers.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule
    ;;
  *)
    echo "Running Alembic migrations..."
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8002 --log-level warning
    ;;
esac
