#!/usr/bin/env bash
# Runs on the server as the willthesystemcollapse user (`npm run deploy` from a dev machine; see README.md).
# Builds origin/main into a new release folder, then syncs its dist/ into the Hestia web root.
# A failed build never goes live: nothing is published until the build succeeds.
set -euo pipefail
APP=$HOME/app REPO=$HOME/app/repo RELEASES=$HOME/app/releases
WEB=$HOME/web/willthesystemcollapse.com/public_html
export npm_config_cache=$APP/.npm # Hestia keeps $HOME root-owned, so everything writable lives under ~/app

SHA=$(git -C "$REPO" rev-parse --short origin/main)
REL=$RELEASES/$(date +%Y%m%d-%H%M%S)-$SHA

echo "→ release $SHA"
mkdir -p "$REL"
git -C "$REPO" archive origin/main | tar -x -C "$REL"
cd "$REL"

echo "→ install + build"
npm ci --no-audit --no-fund --loglevel=error
npm run build

echo "→ publish"
# .well-known stays: Hestia's Let's Encrypt renewals write challenges there; live/ is the collector's output (cron)
rsync -a --delete --exclude '.well-known' --exclude '/live/' dist/ "$WEB/"
# put the latest reading into the fresh page now rather than at the next collector run
python3 collector/collect.py --snapshot --out "$WEB/live"
rm -rf node_modules

# Keep the last 3 releases (each keeps its dist/ for a manual rollback: rsync -a --delete <release>/dist/ $WEB/).
ls -1dt "$RELEASES"/*/ | tail -n +4 | xargs -r rm -rf
echo "✓ deployed $SHA"
