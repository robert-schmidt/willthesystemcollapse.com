# willthesystemcollapse.com

The live financial-stress monitor. See README.md for layout, local commands, the server and deploys, and
CLAUDE.local.md (gitignored) for private notes, including the sister site.

## Rules
- **Fair, not alarming, not pleasing.** The owner's words: "I dont want you to please me, I dont want it to be alarming,
  I want it to be fair." Change weights, bands or inputs only on out-of-sample backtest evidence, and record each change
  in the page's "How the formula evolved" list (and bump `VERSION` in score.py). Rejected so far: model-fitted pillar
  weights, an oil-shock (Hamilton NOPI) input, Fed funds/hikes/real rate/10y−2y in the recession model, claims, permits,
  lending standards, Sahm and inflation in the recession model, RMS/top-half averaging.
- **Data licences.** Keyless public sources only. FRED series tagged "pre-approval" (e.g. SP500, high-yield spreads) are
  not shown; SGE (Shanghai) is licence-only; Stooq is bot-walled. No scrapers that need X/Reddit logins or break ToS.
- **Server.** Shared HestiaCP VPS: touch only this site's `willthesystemcollapse` account (and the sister
  site's, see CLAUDE.local.md). Deploy with `git push && npm run deploy` (no GitHub Actions). Cron stdout must stay quiet
  (cron mails it).
- **Secrets** live in `.secrets` (gitignored, mode 600): never print or commit them. The repo is public: server access
  details (host, IP, SSH port and user) stay in `.secrets` too, never in tracked files or commit messages.
- The owner posts on social media; never post on their behalf. Nothing here is financial advice: keep that line.
- `why-gold.json` and `collapse.json` (written by collect.py) are read cross-origin by the sister site: don't drop them
  or rename fields without updating it.

## Method in one paragraph
Each component becomes a percentile vs its own monthly history (expanding window, ≥ 60 months, each input
forward-filled ≤ 6 months so history matches the live reading). Pillars are theme-balanced means: stress, warning (the
walk-forward recession model's percentile), money, fiscal. Score = 100·(2·stress + warning + money + fiscal)/5; bands
0/40/50/60/70/80 were set where historical crisis odds change. The recession model (forecast.py) is a ridge logistic
regression on the 10y−3m spread, its 18-month low, NFCI, NFCI 6-month change and the excess bond premium, refitted every
January since 1979 (out-of-sample AUC ≈ 0.90).
