# willthesystemcollapse.com

[![Live](https://img.shields.io/badge/live-willthesystemcollapse.com-d9b45a)](https://willthesystemcollapse.com)
[![Code: MIT](https://img.shields.io/badge/code-MIT-blue)](LICENSE)
[![Data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-lightgrey)](https://creativecommons.org/licenses/by/4.0/)

[![The live gauge](public/og.png)](https://willthesystemcollapse.com)

**A live 0–100 financial stress score, backtested since 1973.** It reads 230+ public series (Federal Reserve, FRED, US
Treasury, OFR, ECB, IMF, World Bank) every 15 minutes and answers one question: will the system collapse?

- **Backtested, not guessed:** five pillars, a recession model (out-of-sample AUC ≈ 0.90), and the historical odds of a crisis after months like this one.
- **Fair by rule:** the formula changes only when a change beats chance on data it wasn't fitted to; every change and every rejected idea is on record.
- **Open data:** everything on the page is one JSON file, [`/live/collapse.json`](https://willthesystemcollapse.com/live/collapse.json) (CC BY 4.0).
- **No black box, no ads, no financial advice:** stdlib Python and a static Astro page.

If it is useful, a ⭐ helps others find it.

A live, backtested gauge of how close the financial system is to breaking. A stdlib-Python collector pulls 230+ public
series into SQLite every 15 minutes, scores them (five pillars, backtested since 1973) and writes one JSON file that the
static Astro page polls. It also writes the live answer, score and summary into the built page, so the first paint and
crawlers see them before the script runs.

```
collector/      collect.py (fetch, write JSON, write the reading into the page), score.py (method, backtest),
                forecast.py (recession model), test_collapse.py (self-check), cron.sh (the server's cron entry)
src/pages/      index.astro (the whole monitor), about.astro, 404.astro; src/components/ header and footer
deploy/         deploy.sh (runs on the server), nginx.ssl.conf_live (copy of the server's nginx include)
public/live/    collector output, gitignored: collapse.json (this site) and why-gold.json (gold history)
```

## Local
```bash
npm ci
npm test                                   # scoring self-check
SSL_CERT_FILE=<system roots .pem> npm run collect   # refresh public/live/ from collector/data/collapse.db (gitignored)
npm run dev
```
python.org Python on macOS needs `SSL_CERT_FILE` pointing at the system roots for some government sites
(`security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > roots.pem`).
Deleting the database triggers a full backfill (~2 min).

## Production
A small Linux server runs the collector on a 15-minute cron job and serves the built site. Server access details are kept out
of the repo (`.secrets`, gitignored); `npm run deploy` reads them from there.

```bash
git push && npm run deploy   # builds origin/main into a fresh release, syncs it into the web root,
                             # then writes the latest reading into the new page
```

Other sites read `/live/*.json` cross-origin, so keep their field names stable.

## Licence
Code: MIT (`LICENSE`). Our scores and derived data are [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); each underlying series stays
under its publisher's terms.
