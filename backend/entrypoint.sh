#!/usr/bin/env sh
#
# Container entrypoint: bring the schema and reference data up to date, then serve.
#
# `set -e` matters here — if migrate fails, the container must die so the
# platform's deploy fails visibly, rather than starting Gunicorn against a
# half-migrated database and serving 500s to staff.
set -e

# One Django process runs every boot step (migrate, then seed_initial_data only
# when RUN_SEED=true, then seed_psgc, then backfill_psgc --apply), in that
# order. They used to be four processes, each importing Django again on a 0.1
# CPU instance. The step names ("==> ...") and their elapsed seconds are
# printed by the command, so the deploy log's last "==>" line still says which
# step failed or was slow. Any failure exits non-zero and `set -e` stops the
# container before Gunicorn starts.
#   - seed_initial_data: first boot only, idempotent.
#   - seed_psgc: reference data, updated in place on the PSGC code.
#   - backfill_psgc: only records with no codes yet; a no-op once none remain.
python manage.py boot_tasks

# --timeout is generous on purpose: uploading a large report and writing it to
# object storage happens inside the request cycle.
echo "==> Starting Gunicorn on port ${PORT:-8000}"
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-3}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout "${GUNICORN_TIMEOUT:-180}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    --log-level "${GUNICORN_LOG_LEVEL:-info}"
