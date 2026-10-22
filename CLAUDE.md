# willthesystemcollapse.com

The live financial-stress monitor. See README.md for layout, local commands, the server and deploys, and
CLAUDE.local.md (gitignored) for private notes, including the sister site.

## Rules
- **Fair, not alarming, not pleasing.** The owner's words: "I dont want you to please me, I dont want it to be alarming,
  I want it to be fair." Change weights, bands or inputs only on out-of-sample backtest evidence, and record each change
  in the page's "How the formula evolved" list (and bump `VERSION` in score.py). Rejected so far: model-fitted pillar
  weights, an oil-shock (Hamilton NOPI) input, Fed funds/hikes/real rate/10y−2y in the recession model, claims, permits,
  lending standards, Sahm and inflation in the recession model, RMS/top-half averaging; in v6 research: more weight on
  valuations (MV/GDP, CAPE, household equity share: regime markers, not timing), the GHSS R-zone, sudden-move breadth,
  credit growth or a spread × credit term in the recession model, investors' public warnings (not backtestable);
  after v6: delinquencies (cards, consumer, all loans, mortgages, NY Fed auto), ACM term premium (level, change), the
  expectations spread or Engstrom–Sharpe forward spread in the recession model, Italy−Germany 10y, real 10y yield, its
  12-month change, "vigilantes" (10y up while the Fed cuts).
  The acceptance rule (rule C, adopted 2026-10-22; the baseline is always the live version). Stage 1, information: among months
  the score already rates alike (its quintiles), the candidate's stratified AUC on its pillar's own job (stress: a crisis
  under way; build-up, fiscal, money: a crisis starting within 24 months) beats the 99th percentile of random autocorrelated
  series (0.677 and 0.707 on today's data; recalibrate when the score changes) and is ≥ 0.5 in 1973–99 and in 2000+.
  Stage 2, no harm: added at standard theme weight, no target (crisis under way, crisis 12/24, recession 12, severe 12, 20%
  fall 12 months ahead) down more than 0.02, and ≥ 70 still ≥ 90% near a crisis. Calibrated on placebos and synthetic
  signals only: 1–2.5% false alarms; accepts a strong new signal (own AUC 0.77–0.83) 13–19 times in 20. The old rule
  (crisis ahead-24 +0.03, CI > 0, gains in both eras) accepted 0 of 300 random series but also 0 of 20 such signals: a new
  theme carries ~1/24 of the score, so a whole-score AUC test only measures dilution. Run every candidate once, in the
  pillar its meaning says, and report the expected chance passes (2% × tries). Scripts: that session's calib*.py, retest.py.
  v7 exception (the owner's call, 2026-10-20): policy uncertainty and geopolitical risk went in under a no-harm rule set before
  the test (crisis ahead-24 down ≤ 0.01 with its CI including 0; recession/severe ahead-12 down ≤ 0.02; ≥ 70 band ≥ 90%; no
  era down > 0.02). EPU's theme weight 2 is the most at which crisis ahead-24 stayed ≥ v6 (3 lost ground): don't raise it
  without a new test, and don't treat the exception as precedent. Rejected in v7 research: EPU/GPR in the recession model,
  GPR level as stress (during-crisis AUC 0.56), global EPU, the World Uncertainty Index, IMF social unrest (its one pass came
  from 2019 → Covid and the Arab Spring → 2011, and IMF event dates use hindsight), ParlGov vote shares (stop in June 2023).
  Rejected after v7 (2026-10-20, baseline v7): partisan conflict (Richmond Fed), migration fear (US/UK/DE; France stops 2019),
  trade policy uncertainty, SF Fed news sentiment (stress, build-up calm, recession model: AUC 0.89 → 0.81–0.85), EU consumer
  confidence, EMV, categorical EPU (sovereign debt, monetary); the best, EMV + debt EPU, was +0.011 with CI incl. 0, best 2 of
  15. All shown in the society panel, outside the score.
  Rejected after v7, round 3 (2026-10-22, baseline v7): household measures in every pillar and in the recession model (house
  price ÷ pay or family income, mortgage payment ÷ pay, price ÷ rent, real house prices, household credit and debt service,
  misery index, real wages, UMich sentiment, saving rate, labour share, productivity–pay gap, CEO pay, Gini, top 1% wealth)
  and their strain composite (crisis ahead-24 0.57 alone). Best +0.021 (household credit 3y, debt service, as stress themes),
  but random slow series added as a stress theme gain up to +0.019 and pass the CI check (why rule C replaced the old rule).
  Under rule C, 10 of 61 rebuilt candidates since v6 passed (1.2 expected by chance): news tone, all-loan and mortgage
  delinquencies, EU confidence, unrest coverage (stress); household credit 3y, low saving 12m, calm news (build-up);
  household credit, debt service (fiscal). Not yet in the score: waiting on the owner's call on the two-question split.
  Reverse test: most money and fiscal themes add no crisis timing beyond the rest (stratified AUC 0.37–0.67). Unrest study (IMF RSUI × OECD, 46 countries, 1991–2025): strain didn't come before flare-ups (AUC 0.42–0.53);
  unrest came before weaker jobs and stocks only before 2008. All shown in the households panel, outside the score.
- **Data licences.** Keyless public sources only. FRED series tagged "pre-approval" (e.g. SP500, high-yield spreads) are
  not shown; SGE (Shanghai) is licence-only; Stooq is bot-walled. No scrapers that need X/Reddit logins or break ToS.
  EPU (policyuncertainty.com) and GPR/TPU (matteoiacoviello.com) are CC BY 4.0; the IMF unrest index, Richmond Fed partisan
  conflict, SF Fed news sentiment and Eurostat need attribution: keep their credits in the society panel and the feed labels.
  EPI (CEO pay, productivity–pay gap) allows citing with credit to its State of Working America Data Library; Freddie Mac
  mortgage rates and UMich sentiment are "citation required" on FRED. WID income shares state no licence: not used.
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
forward-filled ≤ 6 months so history matches the live reading; quarterly/annual series placed at their publication month,
`LAGS` in score.py). Pillars are theme-balanced means (`THEME_WEIGHTS` for exceptions): stress (incl. CP − bill and
VIX/VIX3M dislocations, and US + Europe policy uncertainty in the news at double weight since v7), warning (the
walk-forward recession model's percentile), money, fiscal, build-up (BIS 3y Δ private credit/GDP, Baa−10y from 24 months
earlier, geopolitical calm: minus the 12-month mean of Caldara–Iacoviello GPRH, as calm years came before crises).
Score = 100·(2·stress + warning + money + fiscal + buildup)/6; bands 0/40/50/60/70/75 were set where historical
crisis odds change (75+ = 1973–74, 1980, 2008–09 only). `unusual()` and `pairs()` raise awareness (records, sudden weekly
moves, markets that should agree, incl. the SHFE gold premium) without entering the score. `outlook()` (also outside the
score) is the expected 10-year real stock return: walk-forward OLS on log CAPE and the excess CAPE yield (Shiller's
ie_data.xls via `xls_min.py`), averaged; ratios beyond any known 10-year outcome are shown, never modelled. Shiller's
data: publish derived numbers only, never the series. The recession model (forecast.py) is a ridge logistic
regression on the 10y−3m spread, its 18-month low, NFCI, NFCI 6-month change and the excess bond premium, refitted every
January since 1979 (out-of-sample AUC ≈ 0.90).
