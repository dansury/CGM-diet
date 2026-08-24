#!/bin/sh
# Run migrations (with retries), then start the bot.
#
# Migrations fail open: an unreachable DATABASE_URL must not crash-loop the
# container. The bot still starts and /health reports the db probe failure.
set -u

attempt=1
max_attempts=3
delay=2
until python -m alembic upgrade head; do
    if [ "$attempt" -ge "$max_attempts" ]; then
        echo "WARNING: alembic upgrade failed after ${max_attempts} attempts;" \
             "starting anyway — check DATABASE_URL and /health" >&2
        break
    fi
    echo "alembic upgrade failed (attempt ${attempt}/${max_attempts}), retry in ${delay}s" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
done

exec python -m src.bot
