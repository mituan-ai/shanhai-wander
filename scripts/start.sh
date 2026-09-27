#!/bin/sh
set -eu
umask 077
python manage.py migrate --noinput
python manage.py createcachetable
python manage.py seed_routes
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-2}" --threads "${GUNICORN_THREADS:-4}" --timeout 120 --access-logfile - --error-logfile -
