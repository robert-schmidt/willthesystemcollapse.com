#!/bin/sh
# Hestia cron job of the willthesystemcollapse user, every 15 minutes: refresh the monitor.
# flock: a slow run (first backfill, a hanging source) is never overlapped by the next one. Errors → ~/app/data/collect.log,
# kept under ~1 MB. The database and JSON are plain files, so restarts lose nothing; deleting the DB triggers a full backfill.
D="$HOME/app/data"
mkdir -p "$D"
[ -f "$D/collect.log" ] && [ "$(wc -c < "$D/collect.log")" -gt 1000000 ] && mv "$D/collect.log" "$D/collect.log.1"
cd "$HOME/app/repo" && exec flock -n "$D/.collect.lock" python3 collector/collect.py --db "$D/collapse.db" \
  --out "$HOME/web/willthesystemcollapse.com/public_html/live" 2>>"$D/collect.log"
