"""System stress score for willthesystemcollapse.com (method, data and backtest are explained on the page).

Five pillars, each 0–1 (higher = more stress); score = 100 × (2·stress + warning + money + fiscal + build-up) / 6.
  stress    — is a crisis happening? Stress indices (NFCI, OFR, St. Louis Fed, ECB CISS), VIX and the stock drawdown,
              credit spreads, unemployment, and dislocations (commercial paper over T-bills, VIX over VIX3M).
  warning   — is a recession coming within 12 months? Percentile of the forecast.py probability (yield curve, deepest recent
              inversion, financial conditions and their change, excess bond premium).
  money     — is money losing value? Official and alternative inflation, expectations, money growth, the dollar, gold.
  fiscal    — how much could break? Debt, deficits, the interest bill, r − g, foreign debt, G7 debt, bond sell-offs,
              corporate leverage, stock valuations and the inflation-adjusted gold price.
  buildup   — are excesses building? Private credit growth over 3 years (BIS) and how cheap credit was two years ago.
History uses expanding-window percentiles and a walk-forward model: what the gauge would have said at the time.
"""
import bisect, math, sys
from itertools import groupby
import forecast
from datetime import datetime, timezone, date

VERSION = 6  # v1 market stress; v2 + walk-forward recession model; v3 + inflation/money and fiscal/external pillars;
# v4 + stock valuations and corporate leverage, forward-looking r − g, debt growth after inflation, history = live inputs;
# v5 theme-balanced pillars, bands calibrated on historical odds; v6 + build-up pillar (credit boom, credit sentiment),
# funding dislocations in stress, history placed at each series' publication date
WEIGHTS = {'stress': 2, 'warning': 1, 'money': 1, 'fiscal': 1, 'buildup': 1}
# Quarterly and annual data enter the history when it was published, not at the quarter it describes (months after the
# observation date): Z.1 financial accounts ~10 weeks after quarter end, GDP's advance estimate a month after, BIS ~6 months.
LAGS = {'EQUITY_MV': 5, 'CORP_DEBT': 5, 'HH_DEBT': 5, 'NIIP': 5, 'GDP': 3, 'DEBT_GDP': 3, 'INTEREST': 4, 'RECEIPTS': 4,
        'FED_DEBT_LEVEL': 3, 'DEFICIT_GDP': 9, 'BIS_CREDIT': 8}
# Bands calibrated on historical odds (v6 backtest, 1973–2026): below 40, a crisis was under way or began within 12 months
# in 9% of months; 40–50, 41% (below the 45% average); 50–60, 64% (above it); 60–70, 86%; 70+, all of them. 75+ only in
# 1974, 1980 and 2008–09, the episodes that needed system-wide rescues. The page shows the odds next to the label.
BANDS = [  # (from, key, answer, label)
    (0, 'calm', 'NO', 'Calm'),
    (40, 'normal', 'NO', 'Normal'),
    (50, 'elevated', 'NOT YET', 'Elevated'),
    (60, 'high', 'NOT YET', 'High stress'),
    (70, 'crisis', 'WOBBLING', 'Crisis'),
    (75, 'systemic', 'YES', 'Systemic breakdown'),
]
# Inputs that measure the same thing are averaged together first, so one theme with many variants (8 consumer-inflation
# measures, 4 financial-stress indices) can't outvote the others: pillar = mean of its themes.
THEMES = {
    'stress': {'stress indices': ['nfci', 'ofr', 'stlfsi', 'ciss'], 'equity fear': ['vix', 'drawdown'], 'credit': ['credit', 'ebp'],
               'jobs': ['unemp'], 'dislocations': ['cp_bill', 'vix_curve']},
    'money': {'consumer inflation': ['cpi', 'core_cpi', 'pce', 'sticky', 'median', 'trim_cpi', 'trim_pce', 'mct'], 'pipeline': ['ppi'],
              'momentum': ['accel'], 'expectations': ['exp10', 'exp5y5y'], 'money growth': ['m2'], 'dollar': ['usd_fall'],
              'gold': ['gold_mom']},
    'fiscal': {'government debt': ['debt_gdp', 'g7_debt'], 'deficit': ['deficit'], 'interest': ['interest', 'r_g'],
               'debt growth': ['debt_growth'], 'external': ['niip'], 'private leverage': ['corp_debt'], 'valuation': ['valuation'],
               'trust in paper': ['real_gold'], 'bond market': ['bond_selloff']},
    'buildup': {'credit boom': ['boom'], 'credit sentiment': ['sentiment']},
}
SEVERE = ['1973-11', '1979-10', '1981-08', '2007-08', '2020-02']  # crisis starts that needed system-wide rescues
CRISES = [
    ('1973-11', '1975-03', 'Oil shock and the 1973–74 crash'),
    ('1979-10', '1980-06', 'Volcker shock, gold peaks'),
    ('1981-08', '1982-10', 'Double-dip recession, Latin American debt crisis'),
    ('1987-10', '1987-12', 'Black Monday'),
    ('1990-07', '1991-03', 'Savings & loan crisis, Gulf War recession'),
    ('1998-08', '1998-10', 'Russian default, LTCM'),
    ('2000-10', '2002-10', 'Dot-com bust'),
    ('2007-08', '2009-06', 'Global financial crisis'),
    ('2010-05', '2010-07', 'Euro crisis (Greece)'),
    ('2011-07', '2012-07', 'Euro crisis, US downgrade'),
    ('2020-02', '2020-05', 'Covid crash'),
    ('2022-03', '2022-10', 'Inflation shock, gilt crisis'),
    ('2023-03', '2023-05', 'SVB and regional banks'),
]
MIN_HIST = 60  # months of history before a component counts in the backtest
OZ_T = 31.1034768  # tonnes per million troy ounces (and grams per ounce)


# ---- series helpers ----

def rows(db, key):
    return db.execute('SELECT d, v FROM obs WHERE series=? ORDER BY d', (key,)).fetchall()


def monthly(db, key, how='last'):
    out = {}
    for d, v in rows(db, key):
        if how == 'last':
            out[d[:7]] = v
        else:
            out.setdefault(d[:7], []).append(v)
    return out if how == 'last' else {m: sum(v) / len(v) for m, v in out.items()}


def latest(db, key):
    return db.execute('SELECT d, v FROM obs WHERE series=? ORDER BY d DESC LIMIT 1', (key,)).fetchone()


def value_at(db, key, d):
    """Last value on or before date d."""
    r = db.execute('SELECT v FROM obs WHERE series=? AND d<=? ORDER BY d DESC LIMIT 1', (key, d)).fetchone()
    return r[0] if r else None


def days_before(d, n):
    return str(date.fromordinal(date.fromisoformat(d[:10]).toordinal() - n))


def change(db, key, days):
    d, v = latest(db, key)
    past = value_at(db, key, days_before(d, days))
    return round(v / past - 1, 4) if past else None


def shift(m, k):
    y, mo = map(int, m.split('-'))
    mo += k
    return f'{y + (mo - 1) // 12:04d}-{(mo - 1) % 12 + 1:02d}'


def span(a, b):
    out = []
    while a <= b:
        out.append(a)
        a = shift(a, 1)
    return out


def ffill(s, months, limit):
    out, last, age = {}, None, 99
    for m in months:
        last, age = (s[m], 0) if m in s else (last, age + 1)
        if last is not None and age <= limit:
            out[m] = last
    return out


def pct(sorted_vals, v):
    return (bisect.bisect_left(sorted_vals, v) + bisect.bisect_right(sorted_vals, v)) / 2 / len(sorted_vals)


# ---- components: monthly history (higher = more stress) plus the freshest reading ----

def components(db):
    today = date.today().strftime('%Y-%m')
    months = span('1950-01', today)
    M = lambda k, how='last': {shift(m, LAGS.get(k, 0)): v for m, v in monthly(db, k, how).items()}
    baa, gs10, tb3, cpi, ff, unr = M('BAA'), M('GS10'), M('TB3MS'), M('CPI'), M('FEDFUNDS'), M('UNRATE')
    gold_m, eq, gdp = M('GOLD_M'), M('EQ_US'), ffill(M('GDP'), months, 4)
    yoy = lambda s, k=12: {m: s[m] / s[shift(m, -k)] - 1 for m in s if shift(m, -k) in s and s[shift(m, -k)]}
    infl = yoy(cpi)
    real_gold = {m: gold_m[m] / cpi[m] for m in gold_m if m in cpi}
    interest, receipts = ffill(M('INTEREST'), months, 4), ffill(M('RECEIPTS'), months, 4)
    burden = {m: interest[m] / receipts[m] for m in interest if m in receipts}
    debt_ratio = ffill(M('DEBT_GDP'), months, 4)
    debt_level = {m: debt_ratio[m] / 100 * gdp[m] for m in debt_ratio if m in gdp}
    eff_r = {m: 100 * interest[m] / debt_level[m] for m in interest if m in debt_level}
    g_nom = {m: 100 * (gdp[m] / gdp[shift(m, -12)] - 1) for m in gdp if shift(m, -12) in gdp}
    dd = {m: 1 - eq[m] / max(eq[x] for x in months[max(0, i - 12):i + 1] if x in eq) for i, m in enumerate(months) if m in eq}
    # rise vs the 12-month low; tolerates missing months (BLS published no October 2025 data during the shutdown)
    unemp = {}
    for m in unr:
        win = [unr[shift(m, -k)] for k in range(12) if shift(m, -k) in unr]
        if len(win) >= 9:
            unemp[m] = unr[m] - min(win)
    median = M('MEDIAN_CPI')
    median12 = {m: sum(median[shift(m, -k)] for k in range(12)) / 1200 for m in median if all(shift(m, -k) in median for k in range(12))}
    usd_old, usd_new = M('USD_OLD', 'mean'), M('USD', 'mean')
    usd_chg = {**yoy(usd_old), **yoy(usd_new)}  # major-currency index to 2006, broad index after (only % changes are used)
    g7 = {}
    for c in ('USA', 'JPN', 'ITA', 'FRA', 'GBR', 'CAN', 'DEU'):
        for m, v in ffill({d[:7]: v for d, v in rows(db, f'GOVDEBT_{c}') if d[:4] < today[:4]}, months, 14).items():
            g7.setdefault(m, []).append(v)
    g7 = {m: sum(v) / len(v) for m, v in g7.items() if len(v) >= 5}
    niip = ffill(M('NIIP'), months, 4)

    gold_d, gold = latest(db, 'GOLD')
    cpi_d, cpi_now = latest(db, 'CPI')
    last = lambda key: tuple(latest(db, key))
    tail = lambda h: (max(h) + '-01', h[max(h)])  # newest monthly value
    C = {}

    def add(key, label, pillar, hist, now, series, unit):
        C[key] = dict(label=label, pillar=pillar, hist=hist, now=now, series=series, unit=unit)

    # stress: is a crisis happening?
    add('nfci', 'Financial conditions (Chicago Fed NFCI)', 'stress', M('NFCI', 'mean'), last('NFCI'), 'NFCI', 'idx')
    add('ofr', 'Financial stress (OFR)', 'stress', M('OFR_FSI', 'mean'), last('OFR_FSI'), 'OFR_FSI', 'idx')
    add('stlfsi', 'Financial stress (St. Louis Fed)', 'stress', M('STLFSI4', 'mean'), last('STLFSI4'), 'STLFSI4', 'idx')
    add('ciss', 'Euro-area systemic stress (ECB CISS)', 'stress', M('CISS_EA', 'mean'), last('CISS_EA'), 'CISS_EA', 'idx3')
    add('vix', 'Stock-market fear gauge (VIX)', 'stress', M('VIX', 'mean'), last('VIX'), 'VIX', 'pts')
    add('credit', 'Corporate credit spread, Baa over 10y', 'stress', {m: baa[m] - gs10[m] for m in baa if m in gs10},
        last('BAA10Y'), 'BAA10Y', 'pp')
    add('ebp', 'Excess bond premium (Fed)', 'stress', M('EBP'), last('EBP'), 'EBP', 'pp')
    add('drawdown', 'US stocks below their 12-month high', 'stress', dd, tail(dd), 'EQ_US', 'ratio')
    add('unemp', 'Unemployment rise vs its 12-month low', 'stress', unemp, tail(unemp), 'UNRATE', 'pp')
    # money: is money losing value?
    add('cpi', 'Inflation, official CPI', 'money', infl, tail(infl), 'CPI', 'ratio')
    add('core_cpi', 'Core inflation (ex food & energy)', 'money', yoy(M('CORE_CPI')), tail(yoy(M('CORE_CPI'))), 'CORE_CPI', 'ratio')
    sticky = {m: v / 100 for m, v in M('STICKY_CPI').items()}
    add('sticky', 'Sticky-price inflation (Atlanta Fed)', 'money', sticky, tail(sticky), 'STICKY_CPI', 'ratio')
    add('median', 'Median CPI, 12 months (Cleveland Fed)', 'money', median12, tail(median12), 'MEDIAN_CPI', 'ratio')
    add('pce', 'PCE inflation (the Fed’s target)', 'money', yoy(M('PCE')), tail(yoy(M('PCE'))), 'PCE', 'ratio')
    add('ppi', 'Producer prices, 12 months', 'money', yoy(M('PPI')), tail(yoy(M('PPI'))), 'PPI', 'ratio')
    accel = {m: infl[m] - infl[shift(m, -12)] for m in infl if shift(m, -12) in infl}
    add('accel', 'Inflation now vs a year ago', 'money', accel, tail(accel), 'CPI', 'ratio_pp')
    add('exp5y5y', 'Expected inflation, 5–10 years ahead (markets)', 'money', M('INFL_5Y5Y', 'mean'), last('INFL_5Y5Y'),
        'INFL_5Y5Y', 'pct')
    mct = {m: v / 100 for m, v in M('MCT').items()}
    add('mct', 'Core trend inflation (NY Fed)', 'money', mct, tail(mct), 'MCT', 'ratio')
    tc = M('TRIM_CPI')
    trim_cpi = {m: sum(tc[shift(m, -k)] for k in range(12)) / 1200 for m in tc if all(shift(m, -k) in tc for k in range(12))}
    add('trim_cpi', 'Trimmed-mean CPI, 12 months (Cleveland Fed)', 'money', trim_cpi, tail(trim_cpi), 'TRIM_CPI', 'ratio')
    trim_pce = {m: v / 100 for m, v in M('TRIM_PCE').items()}
    add('trim_pce', 'Trimmed-mean PCE (Dallas Fed)', 'money', trim_pce, tail(trim_pce), 'TRIM_PCE', 'ratio')
    add('exp10', 'Expected inflation, 10 years (Cleveland Fed)', 'money', M('EXP_INF10'), last('EXP_INF10'), 'EXP_INF10', 'pct')
    add('m2', 'Money supply (M2) growth, 12 months', 'money', yoy(M('M2')), tail(yoy(M('M2'))), 'M2', 'ratio')
    gold_yago = value_at(db, 'GOLD', days_before(gold_d, 365))
    cpi_yago = value_at(db, 'CPI', shift(cpi_d[:7], -12) + '-01')
    add('gold_mom', 'Gold vs inflation, 12-month change', 'money',
        {m: math.log(real_gold[m] / real_gold[shift(m, -12)]) for m in real_gold if shift(m, -12) in real_gold},
        (gold_d, math.log((gold / cpi_now) / (gold_yago / cpi_yago))), 'GOLD', 'log')
    usd_d, usd_now = latest(db, 'USD')
    add('usd_fall', 'Dollar index, 12-month change', 'money', {m: -v for m, v in usd_chg.items()},
        (usd_d, -(usd_now / value_at(db, 'USD', days_before(usd_d, 365)) - 1)), 'USD', 'neg_ratio')
    # fiscal: how much could break?
    add('debt_gdp', 'US federal debt, % of GDP', 'fiscal', debt_ratio, last('DEBT_GDP'), 'DEBT_GDP', 'pct')
    add('interest', 'Interest bill, share of federal revenue', 'fiscal', burden, tail(burden), 'INTEREST', 'ratio')
    # r − g with the marginal rate (what new borrowing costs: the 10-year yield) against 5-year nominal GDP growth
    g5 = {m: 100 * ((gdp[m] / gdp[shift(m, -60)]) ** 0.2 - 1) for m in gdp if shift(m, -60) in gdp}
    rg = {m: gs10[m] - g5[m] for m in gs10 if m in g5}
    y_d0, y10_0 = last('DGS10')
    add('r_g', 'Borrowing cost minus growth (10y yield − 5y GDP growth)', 'fiscal', rg, (y_d0, y10_0 - g5[max(g5)]), 'GDP', 'pp')
    deficit = {m: -v for m, v in ffill(M('DEFICIT_GDP'), months, 14).items()}
    add('deficit', 'Federal deficit, % of GDP', 'fiscal', deficit, tail(deficit), 'DEFICIT_GDP', 'pct')
    # federal debt growth after inflation, per year over 5 years (nominal growth flatters the high-inflation 1970s–80s)
    fdebt = ffill(M('FED_DEBT_LEVEL'), months, 4)
    infl5 = {m: (cpi[m] / cpi[shift(m, -60)]) ** 0.2 - 1 for m in cpi if shift(m, -60) in cpi}
    growth = {m: (fdebt[m] / fdebt[shift(m, -60)]) ** 0.2 - 1 - infl5[m] for m in fdebt if shift(m, -60) in fdebt and m in infl5}
    add('debt_growth', 'Federal debt growth after inflation, per year (5y)', 'fiscal', growth, tail(growth), 'FED_DEBT_LEVEL', 'ratio')
    # how stretched private balance sheets and stock prices are
    eqv = {m: v / 1000 / gdp[m] for m, v in ffill(M('EQUITY_MV'), months, 4).items() if m in gdp}
    add('valuation', 'US company shares, listed and private, % of GDP', 'fiscal', eqv, tail(eqv), 'EQUITY_MV', 'ratio')
    corp = {m: v / 1000 / gdp[m] for m, v in ffill(M('CORP_DEBT'), months, 4).items() if m in gdp}
    add('corp_debt', 'Corporate debt, % of GDP', 'fiscal', corp, tail(corp), 'CORP_DEBT', 'ratio')
    ext = {m: -niip[m] / 1000 / gdp[m] for m in niip if m in gdp}
    add('niip', 'Net foreign debt, % of GDP (NIIP)', 'fiscal', ext, tail(ext), 'NIIP', 'ratio')
    add('g7_debt', 'G7 average government debt, % of GDP', 'fiscal', g7, tail(g7), 'GOVDEBT_USA', 'pct')
    add('real_gold', 'Gold price adjusted for inflation', 'fiscal', real_gold, (gold_d, gold / cpi_now), 'GOLD', 'real_gold')
    y_d, y10 = last('DGS10')
    sell = {m: gs10[m] - gs10[shift(m, -3)] for m in gs10 if shift(m, -3) in gs10}
    add('bond_selloff', '10-year yield, 3-month change', 'fiscal', sell, (y_d, y10 - value_at(db, 'DGS10', days_before(y_d, 91))),
        'DGS10', 'pp')
    # funding dislocations (monthly worst day): banks' 3-month borrowing cost over T-bills, and near-term fear over 3-month
    S = lambda k: rows(db, k)
    mmax = lambda pts: {m: max(v for d, v in g) for m, g in groupby(pts, key=lambda p: p[0][:7])}
    bill = dict(S('TBILL_3M'))
    week_bill = lambda d: [bill[x] for x in (days_before(d, k) for k in range(7)) if x in bill]
    old = [(d, v - sum(b) / len(b)) for d, v in S('CP_OLD') if (b := week_bill(d))]  # weekly averages, 1971–97
    new = [(d, v - bill[d]) for d, v in S('CP_FIN_3M') if d in bill]  # AA financial paper, daily from 1997
    if old and new:  # (a source not fetched yet leaves its input out rather than failing the whole report)
        lap = [v for d, v in old if d >= new[0][0]], [v for d, v in new if d <= old[-1][0]]
        adj = sum(lap[0]) / len(lap[0]) - sum(lap[1]) / len(lap[1])  # the two series overlap in 1997; level the older one
        cp = mmax([(d, v - adj) for d, v in old if d < new[0][0]] + new)
        add('cp_bill', 'Commercial paper over T-bills, worst day this month', 'stress', cp, (new[-1][0], cp[max(cp)]), 'CP_FIN_3M', 'pp')
    vix3m = dict(S('VIX3M'))
    vr = mmax([(d, v / vix3m[d]) for d, v in S('VIX') if d in vix3m])
    add('vix_curve', 'VIX over VIX3M (above 1: fear is front-loaded), worst day this month', 'stress', vr,
        (max(vix3m), vr[max(vr)]), 'VIX3M', 'x')
    # build-up: how fast private debt outgrew the economy over 3 years, and how cheap credit was two years ago
    bis = ffill(M('BIS_CREDIT'), months, 4)
    boom = {m: bis[m] - bis[shift(m, -36)] for m in bis if shift(m, -36) in bis}
    if boom:
        add('boom', 'Private credit growth, 3 years (pp of GDP, BIS)', 'buildup', boom, tail(boom), 'BIS_CREDIT', 'pp_gdp')
    cheap = {shift(m, 24): -v for m, v in ffill(C['credit']['hist'], months, 6).items() if shift(m, 24) <= today}  # low then = risk now
    add('sentiment', 'Baa credit spread two years ago (low = complacency)', 'buildup', cheap, tail(cheap), 'BAA', 'neg_pp')

    for c in C.values():  # carry each input's latest value forward (≤ 6 months), as the live reading does
        c['hist'] = ffill(c['hist'], months, 6)

    # warning-pillar inputs (forecast.py): 10y−3m spread, constant-maturity daily data from 1982, bills before
    t10y3m = M('T10Y3M', 'mean')
    spread = {m: t10y3m.get(m, gs10[m] - tb3[m] if m in tb3 else None) for m in gs10}
    spread = {m: v for m, v in spread.items() if v is not None}
    spread[today] = latest(db, 'T10Y3M')[1]  # today's curve, not the month-to-date average
    W = dict(spread=spread, nfci=M('NFCI', 'mean'), ebp=M('EBP'), usrec=M('USREC'))
    W['nfci'][today] = latest(db, 'NFCI')[1]
    X = dict(infl=infl, unemp=unemp, ff=ff)  # for the stagflation target
    return C, W, X, months


def balanced(pillar, pcts):
    """Theme-balanced pillar value from {component: percentile}; None if fewer than two themes have data (one for the
    two-theme build-up pillar)."""
    themes = [[pcts[k] for k in keys if k in pcts] for keys in THEMES[pillar].values()]
    themes = [sum(t) / len(t) for t in themes if t]
    return sum(themes) / len(themes) if len(themes) >= min(2, len(THEMES[pillar]) - 1) else None


def expanding(hist, months):
    seen, out = [], {}
    for m in months:
        if m in hist:
            bisect.insort(seen, hist[m])
            if len(seen) >= MIN_HIST:
                out[m] = pct(seen, hist[m])
    return out


def history(C, warn, months):
    """Monthly pillar and score history since 1973 from expanding-window percentiles (no look-ahead)."""
    exp = {k: expanding(c['hist'], months) for k, c in C.items()}
    P = {p: {} for p in WEIGHTS}
    for p in [p for p in WEIGHTS if p != 'warning']:
        keys = [k for k, c in C.items() if c['pillar'] == p]
        for m in months:
            v = balanced(p, {k: exp[k][m] for k in keys if m in exp[k]})
            if v is not None:
                P[p][m] = v
    P['warning'] = warn
    ok = lambda m: m >= '1973-01' and all(m in P[p] for p in WEIGHTS)
    H = {m: 100 * sum(P[p][m] * w for p, w in WEIGHTS.items()) / sum(WEIGHTS.values()) for m in months if ok(m)}
    return H, P


def views(P, H):
    """The two headline readings that make up the score, plus the stagflation index."""
    A = {m: 100 * (2 * P['stress'][m] + P['warning'][m]) / 3 for m in H}
    S = {m: 100 * (P['money'][m] + P['fiscal'][m] + P['buildup'][m]) / 3 for m in H}
    SI = {m: 100 * (P['money'][m] + P['warning'][m]) / 2 for m in H}
    return A, S, SI


def warning_pillar(W, F, months):
    """Expanding percentile of the walk-forward recession probability. Until the model has five years of out-of-sample
    history (1984), an equal-weight blend of the same inputs' percentiles stands in."""
    out = expanding(F['history'], months)
    sp, nf, eb = W['spread'], W['nfci'], W['ebp']
    inv18 = {m: min(sp[shift(m, -k)] for k in range(18)) for m in sp if all(shift(m, -k) in sp for k in range(18))}
    parts = [expanding({m: -v for m, v in sp.items()}, months), expanding({m: -v for m, v in inv18.items()}, months),
             expanding(nf, months), expanding(eb, months)]
    model_from = min(out)
    for m in months:
        if m < model_from:
            v = [p[m] for p in parts if m in p]
            if len(v) >= 2:
                out[m] = sum(v) / len(v)
    return out


# Worked examples for the page: what the gauge said at the time (numbers filled in from the backtest)
CASES = [
    ('1973-10', 'Oil embargo', 'Seven months after the world gave up fixed exchange rates, Arab producers cut oil supplies.'),
    ('1974-11', 'The deepest reading on record', 'Stocks are down by nearly half, inflation is in double digits and banks are failing.'),
    ('1980-01', 'Gold peaks at $850', 'US inflation is running at 14% and confidence in the dollar is at a low.'),
    ('1987-10', 'Black Monday', 'The Dow falls 22.6% in a single day, with almost no warning in the data beforehand.'),
    ('2006-12', 'A quiet warning', 'Markets are calm, but the yield curve has been inverted for six months.'),
    ('2007-08', 'The credit crunch starts', 'BNP Paribas freezes three funds exposed to US subprime mortgages; banks stop lending to each other.'),
    ('2008-10', 'Lehman aftermath', 'Lehman Brothers has failed, AIG has been rescued and credit markets have frozen.'),
    ('2019-08', 'An inversion before Covid', 'The yield curve inverts again; nobody can see the pandemic coming.'),
    ('2020-03', 'Covid crash', 'US stocks fall a third in 23 trading days before the Fed backstops markets.'),
    ('2023-03', 'Banks wobble, recession doesn’t come', 'Silicon Valley Bank fails. The curve has its deepest inversion since 1981, yet no recession follows.'),
]


def events(db, months, X):
    """What the backtest tries to anticipate: hand-picked crises, NBER recessions, 20% stock-market falls, stagflation."""
    usrec, eq = monthly(db, 'USREC'), monthly(db, 'EQ_US')
    rec = [m for m in sorted(usrec) if usrec[m] == 1 and usrec.get(shift(m, -1)) == 0]
    bear, peak, down = [], None, False
    for m in months:
        if m in eq:
            if peak is None or eq[m] > peak:
                peak, down = eq[m], False
            if not down and eq[m] <= 0.8 * peak:
                bear.append(m)
                down = True
    stag = {m for m in X['infl'] if X['infl'][m] >= 0.05 and X['unemp'].get(m, 0) >= 0.5}  # 5%+ inflation, rising unemployment
    stag_starts = [m for m in sorted(stag) if shift(m, -1) not in stag and shift(m, -2) not in stag]
    return dict(crisis=([a for a, _, _ in CRISES], {m for a, b, _ in CRISES for m in span(a, b)}),
                recession=(rec, {m for m in usrec if usrec[m] == 1}),
                bear=(bear, set()),
                stagflation=(stag_starts, stag))


def auc(scores, labels):
    neg = sorted(s for s, y in zip(scores, labels) if not y)
    pos = [s for s, y in zip(scores, labels) if y]
    if not pos or not neg:
        return None
    return round(sum(bisect.bisect_left(neg, p) + .5 * (bisect.bisect_right(neg, p) - bisect.bisect_left(neg, p)) for p in pos)
                 / (len(pos) * len(neg)), 2)


def backtest(series, prob, ev):
    """How each reading did against each kind of event: AUC 'during' (is it high while the event happens?) and 'ahead'
    (is it high in the 12 months before it starts?). 0.5 = coin flip, 1 = perfect."""
    H = series['score']
    end = shift(max(H), -12)
    out = {}
    for name, (starts, inside) in ev.items():
        ahead = lambda m: any(m < s <= shift(m, 12) for s in starts)
        row = dict(events=len([s for s in starts if '1973-01' <= s <= max(H)]))
        for view, S in {**series, 'model': prob}.items():
            ms = [m for m in sorted(S) if '1973-01' <= m <= end]
            free = [m for m in ms if m not in inside]
            row[view] = dict(during=auc([S[m] for m in ms], [m in inside for m in ms]) if inside else None,
                             ahead=auc([S[m] for m in free], [ahead(m) for m in free]))
        out[name] = row

    starts, inside = ev['crisis']
    near = lambda m: m in inside or any(m < s <= shift(m, 12) for s in starts)
    test = [m for m in sorted(H) if m <= shift(max(starts), 12)]
    thresholds = []
    for th in [b[0] for b in BANDS[1:]]:
        hit = [m for m in test if H[m] >= th]
        thresholds.append(dict(at=th, months=len(hit), near=round(100 * sum(map(near, hit)) / len(hit)) if hit else None))
    calm = sorted(H[m] for m in test if not near(m))
    within = sorted(H[m] for m in test if m in inside)
    return dict(since=test[0], until=test[-1], months=len(test), targets=out, thresholds=thresholds,
                base=round(100 * sum(map(near, test)) / len(test)),
                calm_median=round(calm[len(calm) // 2]), crisis_median=round(within[len(within) // 2]),
                max=dict(month=max(H, key=H.get), score=round(max(H.values()), 1)),
                crises=[dict(start=a, end=b, label=l, peak=round(max(H.get(m, 0) for m in span(a, b))),
                             before=round(max(H.get(m, 0) for m in span(shift(a, -12), shift(a, -1))))) for a, b, l in CRISES])


def shown(c, real80):
    v, u = c['now'][1], c['unit']
    return {'idx': f'{v:.2f}', 'idx3': f'{v:.3f}', 'pts': f'{v:.1f}', 'pp': f'{v:+.2f} pp', 'ratio': f'{100 * v:.1f}%',
            'ratio_pp': f'{100 * v:+.1f} pp', 'pct': f'{v:.1f}%', 'log': f'{100 * (math.exp(v) - 1):+.1f}%',
            'neg_ratio': f'{-100 * v:+.1f}%', 'real_gold': f'{100 * (v / real80 - 1):+.0f}% vs 1980 peak',
            'pp_gdp': f'{v:+.1f} pp', 'neg_pp': f'{-v:.2f} pp', 'x': f'{v:.2f}'}[u]


def hyperinflation(db, C):
    """Conditions seen before hyperinflations elsewhere, and where the US is today (none of this is a forecast)."""
    (d1, c1), c0 = latest(db, 'CPI'), value_at(db, 'CPI', shift(latest(db, 'CPI')[0][:7], -1) + '-01')
    wd, wv = latest(db, 'FED_BS')
    fed = wv / value_at(db, 'FED_BS', days_before(wd, 365)) - 1
    rows = [
        ('Monthly inflation above 50% (Cagan’s definition of hyperinflation)', 100 * (c1 / c0 - 1), 50, '%'),
        ('Annual inflation above 40% (the "high-inflation crisis" line)', 100 * C['cpi']['now'][1], 40, '%'),
        ('Long-run inflation expectations above 3% (de-anchoring)', C['exp5y5y']['now'][1], 3, '%'),
        ('Interest bill above 30% of federal revenue', 100 * C['interest']['now'][1], 30, '%'),
        ('Federal deficit above 10% of GDP', C['deficit']['now'][1], 10, '%'),
        ('Fed balance sheet growing over 25% a year (money printing)', 100 * fed, 25, '%'),
        ('Dollar down over 20% in a year', -100 * C['usd_fall']['now'][1], -20, '%'),
    ]
    out = [dict(label=l, value=round(v, 2), threshold=t, met=(v <= t) if t < 0 else (v >= t)) for l, v, t, _ in rows]
    out.append(dict(label='Government debt owed in a foreign currency', value=0, threshold=None, met=False,
                    note='US federal debt is issued in dollars'))
    return out


# daily markets watched for sudden moves in either direction: (series, label, log change?)
MOVERS = [('VIX', 'Stock-market fear gauge (VIX)', True), ('BAA10Y', 'Corporate credit spread (Baa)', False),
          ('DGS10', '10-year Treasury yield', False), ('DGS30', '30-year Treasury yield', False),
          ('USD', 'Dollar index (broad)', True), ('GOLD', 'Gold', True), ('SILVER', 'Silver', True),
          ('OIL', 'Oil (WTI)', True), ('NIKKEI', 'Nikkei 225', True), ('BTC', 'Bitcoin', True)]
MOVE_DAYS, MOVE_Q, RECORD_YEARS = 5, 0.995, 20
mon = lambda m: datetime.strptime(m, '%Y-%m').strftime('%b %Y')


def pairs(db):
    """Markets that normally agree, and how far apart they are today against their own daily history. Unusual = beyond
    99% of past days (both ways for gold), or past a fixed line where the history is too short or crisis-heavy."""
    S = lambda k: dict(rows(db, k))
    shfe, nbp, cny, usd = S('SHFE_GOLD'), S('GOLD_NBP'), S('EUR_CNY'), S('EUR_USD')
    vix, vix3m, sofr99 = S('VIX'), S('VIX3M'), S('SOFR99')
    floor = {**S('IOER'), **S('IORB')}
    floor_at = lambda d: floor.get(d) or value_at(db, 'IORB' if d >= '2021-07-29' else 'IOER', d)
    cp, bill = S('CP_FIN_3M'), S('TBILL_3M')
    items = [  # key, label, unit, {date: gap}, two-sided?, fixed line (None = 99th percentile)
        ('gold_shanghai', 'Gold, Shanghai over London', '%',
         {d: 100 * (v / (cny[d] / usd[d]) * OZ_T / nbp[d] - 1) for d, v in shfe.items() if d in nbp and d in cny and d in usd},  # CNY/g → USD/oz
         True, None),
        ('gold_ny', 'Gold, New York futures over London spot (after carry)', '%', S('V_GOLD_NY_PREM'), True, 1.0),
        ('cp_bill', 'Bank borrowing cost: commercial paper over T-bills', 'pp',
         {d: v - bill[d] for d, v in cp.items() if d in bill}, False, None),
        ('repo', 'Overnight repo, 99th-percentile trade over the Fed’s reserve rate', 'pp',
         {d: v - floor_at(d) for d, v in sofr99.items() if floor_at(d) is not None}, False, None),
        ('vix_curve', 'Fear now vs in 3 months (VIX ÷ VIX3M; above 1 = inverted)', 'x',
         {d: vix[d] / v for d, v in vix3m.items() if d in vix}, False, None),
        ('swap_lines', 'Fed dollar loans to foreign central banks (swap lines)', '$bn',
         {d: v / 1000 for d, v in S('SWAP_LINES').items()}, False, 10.0),
    ]
    out = []
    for key, label, unit, h, both, line in items:
        if not h:
            continue
        d = max(h)
        now, past = h[d], sorted(v for k, v in h.items() if k < d)
        if len(past) < 250 and line is None:
            continue
        q = lambda p: past[int(p * (len(past) - 1))] if past else None
        hi, lo = (line, -line if both else None) if line is not None else (q(.99), q(.01) if both else None)
        out.append(dict(key=key, label=label, unit=unit, value=round(now, 3), date=d[:10], since=min(h)[:4],
                        usual=[round(q(.25), 3), round(q(.75), 3)] if len(past) >= 20 else None,
                        unusual=now >= hi or (lo is not None and now <= lo)))
    return out


def unusual(db, C, show, gaps):
    """What's out of the ordinary right now: score inputs at their highest in 20+ years, and weekly market moves bigger
    than 99.5% of all weekly moves on record. Raises awareness; the score itself only sees the inputs."""
    out = []
    for k, c in C.items():
        if k == 'sentiment':  # its reading is two years old by design: not news
            continue
        (d, v), h = c['now'], c['hist']
        year_ago = shift(d[:7], -12)  # a dip from last month's peak shouldn't hide a level not seen in decades
        older = [m for m, x in h.items() if x > v and m < year_ago]
        recent = [m for m, x in h.items() if x > v and year_ago <= m < d[:7]]
        if older and int(max(older)[:4]) > int(d[:4]) - RECORD_YEARS:
            continue
        note = (f'a level last seen in {max(older)[:4]}' if older else
                f'near the record set in {mon(max(recent, key=h.get))}' if recent else f'highest on record (data from {min(h)[:4]})')
        out.append(dict(kind='record', key=k, label=c['label'], shown=show(c), date=d[:10], note=note))
    for key, label, log in MOVERS:
        r = [x for x in rows(db, key) if x[1] > 0 or not log]  # WTI went below zero in April 2020
        if len(r) < 1000:
            continue
        v = [x for _, x in r]
        ch = [math.log(v[i] / v[i - MOVE_DAYS]) if log else v[i] - v[i - MOVE_DAYS] for i in range(MOVE_DAYS, len(v))]
        now, past = ch[-1], ch[:-MOVE_DAYS]  # past: weekly moves that don't overlap this one
        if abs(now) < sorted(map(abs, past))[int(MOVE_Q * len(past))]:
            continue
        bigger = [i for i, x in enumerate(past) if (x >= now if now > 0 else x <= now)]
        shown_ = f'{100 * (math.exp(now) - 1):+.1f}%' if log else f'{now:+.2f} pp'
        word = 'rise' if now > 0 else 'fall'
        out.append(dict(kind='move', key=key, label=label, shown=f'{shown_} in a week', date=r[-1][0][:10],
                        note=f'largest weekly {word} since {mon(r[bigger[-1] + MOVE_DAYS][0][:7])}' if bigger
                        else f'largest weekly {word} on record (data from {r[0][0][:4]})'))
    fmt = lambda g, v: f'{v:+.2f}%' if g['unit'] == '%' else f'{v:+.2f} pp' if g['unit'] == 'pp' else f'{v:.2f}' if g['unit'] == 'x' else f'${v:,.0f}bn'
    for g in gaps:
        if g['unusual']:
            out.append(dict(kind='gap', key=g['key'], label=g['label'], shown=fmt(g, g['value']), date=g['date'],
                            note=f"usually {fmt(g, g['usual'][0])} to {fmt(g, g['usual'][1])} (since {g['since']})" if g['usual'] else ''))
    return out


def leads(db, P, months, ev, now):
    """Event study (v6 research): how signals behaved around past crisis and 20%-fall starts, and today's pre-crisis checklist.
    None of these is in the score: tested inside it, they added nothing the four pillars don't already carry."""
    M = lambda k, how='last': monthly(db, k, how)
    yoy = lambda d: {m: d[m] / d[shift(m, -12)] - 1 for m in d if shift(m, -12) in d and d[shift(m, -12)]}
    vix, claims = M('VIX', 'mean'), M('CLAIMS', 'mean')
    sig = {
        'stress': ('Stress now (pillar)', {m: v for m, v in P['stress'].items()}),
        'warning': ('Early warning (pillar)', {m: v for m, v in P['warning'].items()}),
        'permits': ('Building permits falling, 12 months', {m: -v for m, v in yoy(M('PERMITS')).items()}),
        'lending': ('Banks tightening lending standards', ffill(M('LENDING'), months, 4)),
        'claims': ('Jobless claims rising from their low', {m: claims[m] / min(claims[shift(m, -k)] for k in range(12)) - 1
                                                         for m in claims if all(shift(m, -k) in claims for k in range(12))}),
        'vix_rise': ('VIX, 3-month rise', {m: vix[m] - vix[shift(m, -3)] for m in vix if shift(m, -3) in vix}),
    }
    pc = {k: expanding(h, months) if k not in ('stress', 'warning') else h for k, (_, h) in sig.items()}
    starts = sorted(set(ev['crisis'][0] + ev['bear'][0]))
    lags = list(range(-24, 13))
    study = {k: [round(100 * sum(v) / len(v)) if (v := [pc[k][shift(x, l)] for x in starts if shift(x, l) in pc[k]]) else None
                 for l in lags] for k in pc}

    def ahead(S, target, h=12):
        st, inside = ev[target]
        ms = [m for m in sorted(S) if '1973-01' <= m <= shift(now, -h) and m not in inside]
        return auc([S[m] for m in ms], [any(m < x <= shift(m, h) for x in st) for m in ms])
    checklist = []
    for k in ('permits', 'lending', 'claims', 'warning', 'vix_rise'):
        S = pc[k]
        last = max(S)
        before = [S[shift(x, -6)] for x in starts if shift(x, -6) in S]
        checklist.append(dict(key=k, label=sig[k][0], now=round(100 * S[last]), date=last,
                              before6=round(100 * sum(before) / len(before)) if before else None,
                              auc=dict(crisis=ahead(S, 'crisis'), bear=ahead(S, 'bear'), recession=ahead(S, 'recession'))))
    return dict(lags=lags, events=len(starts), study=study, labels={k: v[0] for k, v in sig.items()}, checklist=checklist)


def odds(H, ev, score, now):
    """What followed months that scored like this one (within ±3 points), against the average month."""
    starts, inside = ev['crisis']
    sev_inside = {m for a, b, _ in CRISES if a in SEVERE for m in span(a, b)}
    rec_starts, rec_inside = ev['recession']
    bear_starts, _ = ev['bear']
    tests = {
        'crisis_12m': (12, lambda m, h: m in inside or any(m < x <= shift(m, h) for x in starts)),
        'crisis_24m': (24, lambda m, h: m in inside or any(m < x <= shift(m, h) for x in starts)),
        'severe_24m': (24, lambda m, h: m in sev_inside or any(m < x <= shift(m, h) for x in SEVERE)),
        'recession_24m': (24, lambda m, h: m not in rec_inside and any(m < x <= shift(m, h) for x in rec_starts)),
        'bear_24m': (24, lambda m, h: any(m < x <= shift(m, h) for x in bear_starts)),
    }
    out = {}
    for key, (h, f) in tests.items():
        known = [m for m in H if m <= shift(now, -h)]
        like = [m for m in known if abs(H[m] - score) <= 3]
        out[key] = dict(like=round(100 * sum(f(m, h) for m in like) / len(like)) if like else None, months=len(like),
                        base=round(100 * sum(f(m, h) for m in known) / len(known)))
    bins = []
    for lo in range(30, 85, 5):
        known = [m for m in H if lo <= H[m] < lo + 5 and m <= shift(now, -24)]
        if known:
            bins.append(dict(lo=lo, months=len(known),
                             crisis_12m=round(100 * sum(tests['crisis_12m'][1](m, 12) for m in known) / len(known)),
                             crisis_24m=round(100 * sum(tests['crisis_24m'][1](m, 24) for m in known) / len(known))))
    out['bins'] = bins
    return out


def report(db, SERIES):
    C, W, X, months = components(db)
    for c in C.values():
        c['pct'] = pct(sorted(c['hist'].values()), c['now'][1])  # freshest value vs the whole monthly history

    # warning pillar: recession probability from the curve, financial conditions and credit
    sp, nf = W['spread'], W['nfci']
    now = max(sp)
    live_x = [sp[now], min(sp[shift(now, -k)] for k in range(18)), nf[now], nf[now] - nf[shift(now, -6)], latest(db, 'EBP')[1]]
    F = forecast.run(sp, nf, W['ebp'], W['usrec'], months, live_x)
    warn_hist = warning_pillar(W, F, months)

    pscore = {p: balanced(p, {k: c['pct'] for k, c in C.items() if c['pillar'] == p}) for p in WEIGHTS if p != 'warning'}
    pscore['warning'] = pct(sorted(F['history'].values()), F['now'])  # today's probability vs the model's own history
    H, P = history(C, warn_hist, months)
    for p in WEIGHTS:  # this month = today's live reading
        P[p][now] = pscore[p]
    H[now] = 100 * sum(pscore[p] * w for p, w in WEIGHTS.items()) / sum(WEIGHTS.values())
    A, S, SI = views(P, H)
    score, acute, structural, stagf = H[now], A[now], S[now], SI[now]
    band = [b for b in BANDS if score >= b[0]][-1]
    hm = sorted(H)
    ev = events(db, months, X)
    bt = backtest(dict(score=H, acute=A, structural=S, stagflation=SI), F['history'], ev)
    od = odds(H, ev, score, now)
    ld = leads(db, P, months, ev, now)

    # how unusual is today's structural reading?
    s_rank = dict(max=round(max(S.values()), 1), max_month=max(S, key=S.get), pct=round(100 * pct(sorted(S.values()), structural)))
    starts, inside = ev['stagflation']
    stag_near = lambda m: m in inside or any(m < x <= shift(m, 12) for x in starts)
    stag_test = [m for m in hm if m <= shift(now, -12)]
    stag_th = []
    for th in (50, 60, 70, 80):
        hit = [m for m in stag_test if SI[m] >= th]
        stag_th.append(dict(at=th, months=len(hit), near=round(100 * sum(map(stag_near, hit)) / len(hit)) if hit else None))

    real80 = monthly(db, 'GOLD_M')['1980-01'] / monthly(db, 'CPI')['1980-01']
    gaps = pairs(db)
    risky = lambda c: c['pct'] >= 0.85
    flags = [dict(key=k, label=c['label'], shown=shown(c, real80), pct=round(100 * c['pct']), pillar=c['pillar'])
             for k, c in sorted(C.items(), key=lambda kv: -kv[1]['pct']) if risky(c)]
    calm = [dict(key=k, label=c['label'], shown=shown(c, real80), pct=round(100 * c['pct']), pillar=c['pillar'])
            for k, c in sorted(C.items(), key=lambda kv: kv[1]['pct']) if c['pct'] <= 0.15]

    cases = []
    for m, title, text in CASES:
        if m in H:
            cases.append(dict(month=m, title=title, text=text, score=round(H[m]), acute=round(A[m]), structural=round(S[m]),
                              **{p: round(100 * P[p][m]) for p in WEIGHTS},
                              recession_risk=round(100 * F['history'][m]) if m in F['history'] else None,
                              band=[b for b in BANDS if H[m] >= b[0]][-1][3]))

    meta = {r[0]: r[1:] for r in db.execute('SELECT series, ok, fetched FROM meta')}
    lastd = dict(db.execute('SELECT series, max(d) FROM obs GROUP BY series'))
    names = dict(spread='10-year minus 3-month Treasury yield', inv18='Lowest 10y−3m spread in the last 18 months',
                 nfci='Financial conditions (Chicago Fed NFCI)', nfci_chg6='NFCI change over 6 months',
                 ebp='Excess bond premium (Fed)')
    r1 = lambda d: {m: round(v, 1) for m, v in d.items()}
    return dict(
        updated=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        version=VERSION,
        score=round(score, 1), acute=round(acute, 1), structural=round(structural, 1), stagflation=round(stagf, 1),
        structural_rank=s_rank,
        odds=od,
        leads=ld,
        themes={p: list(t) for p, t in THEMES.items()},
        band=dict(key=band[1], answer=band[2], label=band[3]),
        bands=[dict(at=b[0], key=b[1], answer=b[2], label=b[3]) for b in BANDS],
        pillars={p: round(100 * v, 1) for p, v in pscore.items()},
        weights=WEIGHTS,
        components=[dict(key=k, label=c['label'], pillar=c['pillar'], value=round(c['now'][1], 4), shown=shown(c, real80),
                         date=c['now'][0][:10], pct=round(100 * c['pct']), series=c['series']) for k, c in C.items()],
        flags=dict(high=flags, low=calm),
        unusual=unusual(db, C, lambda c: shown(c, real80), gaps),
        pairs=gaps,
        outlook=long_run(db),
        forecast=dict(probability=round(100 * F['now'], 1), percentile=round(100 * pscore['warning']),
                      inputs=[dict(key=k, label=names[k], value=round(v, 3), weight=round(F['weights'][k], 3))
                              for k, v in F['x'].items()],
                      intercept=round(F['weights']['intercept'], 3), track=F['track']),
        stagflation_detail=dict(thresholds=stag_th, episodes=starts, inflation=round(100 * C['cpi']['now'][1], 2),
                                unemployment_rise=round(C['unemp']['now'][1], 2)),
        hyperinflation=hyperinflation(db, C),
        history=dict(months=hm, score=[round(H[m], 1) for m in hm], acute=[round(A[m], 1) for m in hm],
                     structural=[round(S[m], 1) for m in hm], stagflation=[round(SI[m], 1) for m in hm],
                     **{p: [round(100 * P[p][m], 1) for m in hm] for p in WEIGHTS},
                     recession_risk=[round(100 * F['history'][m], 1) if m in F['history'] else None for m in hm]),
        events=dict(recessions=ev['recession'][0], bear_markets=ev['bear'][0], stagflation=ev['stagflation'][0]),
        backtest=bt,
        cases=cases,
        markets=markets(db),
        sources=[dict(key=k, label=v[3], source=v[4], ok=bool(meta.get(k, (0,))[0]), fetched=meta.get(k, (0, None))[1],
                      last=lastd.get(k)) for k, v in SERIES.items()],
    )


def external(db):
    """Gross external debt (all sectors) as % of GDP, latest quarter vs the latest annual GDP (World Bank QEDS / WDI)."""
    names = dict(USA='United States', JPN='Japan', DEU='Germany', GBR='United Kingdom', FRA='France', ITA='Italy', CAN='Canada',
                 CHN='China', IND='India', BRA='Brazil', EMU='Euro area')
    out = []
    for c, name in names.items():
        d, debt = latest(db, f'EXTDEBT_{c}') or (None, None)
        g = latest(db, f'GDPUSD_{c}')
        if debt and g:
            then = value_at(db, f'EXTDEBT_{c}', '2011-12-31')
            g11 = value_at(db, f'GDPUSD_{c}', '2011-12-31')
            out.append(dict(country=name, pct=round(100 * debt / g[1]), usd_tn=round(debt / 1e12, 2), date=d[:7],
                            pct_2011=round(100 * then / g11) if then and g11 else None))
    return sorted(out, key=lambda r: -r['pct'])


def oil(db):
    om = monthly(db, 'OIL_M')
    hist = sorted(om[m] / om[shift(m, -12)] - 1 for m in om if shift(m, -12) in om)
    ch = change(db, 'OIL', 365)
    return dict(price=latest(db, 'OIL')[1], date=latest(db, 'OIL')[0], change_1y=ch, pct=round(100 * pct(hist, ch)))


def niip_history(db):
    """US net foreign debt (−NIIP) as % of GDP: annual from 1976, quarterly from 2006."""
    gdp = monthly(db, 'GDP')
    pts = {d[:7]: v for d, v in rows(db, 'NIIP_A')}
    pts.update({d[:7]: v for d, v in rows(db, 'NIIP')})
    out = []
    for m in sorted(pts):
        g = value_at(db, 'GDP', m + '-01')
        if g:
            out.append([m, round(-pts[m] / 1000 / g * 100, 1)])
    return out


def markets(db):
    gold_d, gold = latest(db, 'GOLD')
    venues = []
    for key, name, kind in [('V_KRAKEN_PAXG', 'Kraken · PAX Gold', 'token'), ('V_KRAKEN_XAUT', 'Kraken · Tether Gold', 'token'),
                            ('V_BITSTAMP_PAXG', 'Bitstamp · PAX Gold', 'token'), ('V_COINBASE_PAXG', 'Coinbase · PAX Gold', 'token'),
                            ('GOLD', 'Central Bank of Argentina · daily reference', 'fix'),
                            ('GOLD_NBP', 'National Bank of Poland · daily reference', 'fix')]:
        r = latest(db, key)
        if r:
            venues.append(dict(key=key, name=name, kind=kind, price=round(r[1], 2), at=r[0]))
    live = sorted(v['price'] for v in venues if v['kind'] == 'token')
    spot = live[len(live) // 2] if live else gold  # median of the live venues

    fx, d = [], latest(db, 'EUR_USD')[0]
    yago = days_before(d, 365)
    for c in ('USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CNY', 'INR', 'TRY', 'BRL', 'ZAR'):
        def per_usd(day):  # ECB rates are per euro
            u = value_at(db, 'EUR_USD', day)
            return 1.0 if c == 'USD' else 1 / u if c == 'EUR' else value_at(db, f'EUR_{c}', day) / u
        now, then = value_at(db, 'GOLD', d) * per_usd(d), value_at(db, 'GOLD', yago) * per_usd(yago)
        fx.append(dict(ccy=c, price=round(now, 2), change_1y=round(now / then - 1, 4), date=d))

    debt = rows(db, 'DEBT')
    d1, v1 = debt[-1]
    d0, v0 = next(r for r in reversed(debt) if r[0] <= days_before(d1, 365))
    per_sec = (v1 - v0) / ((date.fromisoformat(d1) - date.fromisoformat(d0)).days * 86400)

    cb = []
    for c, name in [('CHN', 'China'), ('RUS', 'Russia'), ('IND', 'India'), ('TUR', 'Türkiye'), ('POL', 'Poland'),
                    ('USA', 'United States'), ('DEU', 'Germany'), ('JPN', 'Japan')]:
        r = latest(db, f'CB_GOLD_{c}')
        if r:
            past = value_at(db, f'CB_GOLD_{c}', shift(r[0][:7], -12) + '-01')
            cb.append(dict(country=name, tonnes=round(r[1] * OZ_T), change_12m=round((r[1] - past) * OZ_T) if past else None,
                           date=r[0][:7]))

    year = str(date.today().year - 1)
    gov = []
    for c, name in [('JPN', 'Japan'), ('ITA', 'Italy'), ('USA', 'United States'), ('FRA', 'France'), ('GBR', 'United Kingdom'),
                    ('CAN', 'Canada'), ('CHN', 'China'), ('BRA', 'Brazil'), ('IND', 'India'), ('DEU', 'Germany')]:
        v, then = value_at(db, f'GOVDEBT_{c}', f'{year}-12-31'), value_at(db, f'GOVDEBT_{c}', '2007-12-31')
        if v:
            gov.append(dict(country=name, pct=round(v, 1), in_2007=round(then, 1) if then else None))

    gm, cpi = monthly(db, 'GOLD_M'), monthly(db, 'CPI')
    cpi_now = latest(db, 'CPI')[1]
    silver = latest(db, 'SILVER')
    btc = latest(db, 'V_KRAKEN_BTC') or latest(db, 'BTC')
    y = lambda k: latest(db, k)[1]
    return dict(
        gold=dict(price=round(spot, 2), reference=round(gold, 2), reference_date=gold_d, change_1y=change(db, 'GOLD', 365),
                  real_vs_1980=round((spot / cpi_now) / (gm['1980-01'] / cpi['1980-01']) - 1, 3),
                  silver=round(silver[1], 2), gold_silver=round(gold / silver[1], 1)),
        venues=venues,
        spread=dict(usd=round(live[-1] - live[0], 2), pct=round(100 * (live[-1] / live[0] - 1), 3)) if len(live) > 1 else None,
        currencies=fx,
        btc=dict(price=round(btc[1], 2), in_gold_oz=round(btc[1] / spot, 2), change_1y=change(db, 'BTC', 365)),
        us_debt=dict(total=v1, date=d1, per_second=round(per_sec, 2), gdp_pct=y('DEBT_GDP')),
        yields=dict(us10=y('DGS10'), us2=y('DGS2'), us10_real=y('DFII10'), de10=y('Y10_DE'), gb10=y('Y10_GB'), jp10=y('Y10_JP'),
                    it10=y('Y10_IT')),
        central_banks=cb,
        government_debt=dict(year=year, countries=gov),
        fed=dict(rate=y('DFF'), real_rate=round(y('DFF') - 100 * (latest(db, 'CPI')[1] / value_at(db, 'CPI', shift(latest(db, 'CPI')[0][:7], -12) + '-01') - 1), 2),
                 rate_1y_ago=value_at(db, 'DFF', days_before(latest(db, 'DFF')[0], 365)),
                 balance_sheet=y('FED_BS'), balance_sheet_change_1y=change(db, 'FED_BS', 365)),
        fed_balance_sheet=y('FED_BS'),
        external_debt=external(db),
        niip_history=niip_history(db),
        oil=oil(db),
        house_prices=dict(change_1y=round(latest(db, 'HPI')[1] / value_at(db, 'HPI', days_before(latest(db, 'HPI')[0], 365)) - 1, 4),
                          date=latest(db, 'HPI')[0]),
        vix=y('VIX'),
    )


# ---- the long run: outside the score ----

def long_run(db):
    try:
        return outlook(db)
    except Exception as e:  # its sources (Shiller's file above all) must never stop the monitor
        print(f'! outlook: {e!r}', file=sys.stderr)
        return None


def quantile(v, p):
    v = sorted(v)
    k = (len(v) - 1) * p
    f = int(k)
    return v[f] + (v[min(f + 1, len(v) - 1)] - v[f]) * (k - f)


def ten_year(P, D, CPI, GS10, CAPE, H=120, MIN=240):
    """Walk-forward 10-year real total-return forecasts {month: % a year} and the outcomes {month: % a year}."""
    ms = sorted(P)
    tr, d = {ms[0]: 1.0}, D[ms[0]]
    for a, b in zip(ms, ms[1:]):
        d = D.get(b, d)  # dividends arrive months late: carry the last one
        tr[b] = tr[a] * (P[b] + d / 12) / P[a] * CPI[a] / CPI[b]  # real total return, dividends reinvested
    y = {m: 100 * ((tr[shift(m, H)] / tr[m]) ** (12 / H) - 1) for m in ms if shift(m, H) in tr}
    X = {'cape': {m: math.log(v) for m, v in CAPE.items()},
         'ecy': {m: 100 / v - GS10[m] + 100 * ((CPI[m] / CPI[shift(m, -120)]) ** .1 - 1) for m, v in CAPE.items() if shift(m, -120) in CPI}}
    fc = {}
    for k, x in X.items():  # running sums for OLS, adding each decade once it has ended
        s, j, train, fc[k] = [0.0] * 5, 0, sorted(m for m in x if m in y), {}
        for t in sorted(x):
            while j < len(train) and train[j] <= shift(t, -H):
                m = train[j]
                j += 1
                for i, v in enumerate((1, x[m], y[m], x[m] ** 2, x[m] * y[m])):
                    s[i] += v
            if s[0] >= MIN:
                den = s[0] * s[3] - s[1] ** 2
                b = (s[0] * s[4] - s[1] * s[2]) / den if den else 0.0
                fc[k][t] = (s[2] - b * s[1]) / s[0] + b * x[t]
    return {t: (fc['cape'][t] + fc['ecy'][t]) / 2 for t in fc['cape'] if t in fc['ecy']}, y


def outlook(db, H=120):
    """What US stocks returned over the next 10 years from valuations like today's, against what 10-year inflation-protected
    Treasuries pay. Walk-forward on Robert Shiller's data since 1871: at each month, a straight line fitted only on decades
    already over, from log CAPE and from the excess CAPE yield (earnings yield minus the real 10-year yield), averaged.
    The 80% range and the odds come from the model's own past misses. Valuation ratios with no precedent are shown, not
    modelled: any fit would be extrapolating far past every known outcome."""
    CAPE = monthly(db, 'SH_CAPE')
    combo, y = ten_year(*(monthly(db, f'SH_{k}') for k in ('P', 'D', 'CPI', 'GS10')), CAPE, H)
    now, f = max(combo), combo[max(combo)]
    known = [t for t in combo if t in y]
    err = [y[t] - combo[t] for t in known]
    tips = latest(db, 'DFII10')
    expensive = [m for m in y if CAPE.get(m, 0) >= CAPE[now]]
    recent = [t for t in known if t > shift(max(known), -240)]

    # ratios beyond history: today vs the highest value whose 10-year outcome is known
    today = date.today().strftime('%Y-%m')
    months = span('1950-01', today)
    Q = lambda k: ffill(monthly(db, k), months, 4)
    gdp, dpi = Q('GDP'), Q('DPI')
    beyond = []
    for key, label, num, den, scale, note in [
        ('valuation', 'US company shares, % of GDP', 'EQUITY_MV', gdp, 0.1, 'includes privately held firms'),
        ('hh_equity', 'Households’ financial assets held in stocks', 'HH_EQ_SHARE', None, 1, ''),
        ('net_worth', 'Household net worth, % of disposable income', 'HH_NW', dpi, 0.1, ''),
        ('profits', 'Corporate profits after tax, % of GDP', 'PROFITS', gdp, 100, 'high margins have tended to fall back'),
        ('tech', 'Investment in computers, software and data equipment, % of GDP', 'TECH_INV', gdp, 100, 'the AI build-out'),
    ]:
        series = {m: scale * v / (den[m] if den else 1) for m, v in Q(num).items() if not den or m in den}
        old = {m: v for m, v in series.items() if m <= shift(today, -H)}
        top = max(old, key=old.get)
        beyond.append(dict(key=key, label=label, now=round(series[max(series)], 1), date=max(monthly(db, num)),
                           known_max=round(old[top], 1), known_max_month=top, note=note))

    # long rates vs the Fed: the 10-year yield since the Fed's last peak rate, if it has cut at least half a point since
    ff, dff_d = monthly(db, 'FEDFUNDS'), latest(db, 'DFF')
    peak = max((m for m in ff if m >= shift(today, -36)), key=lambda m: (ff[m], m))
    easing = dff_d[1] <= ff[peak] - 0.5
    y10 = latest(db, 'DGS10')
    since = dict(month=peak, fed_change=round(dff_d[1] - ff[peak], 2),
                 y10_change=round(y10[1] - monthly(db, 'GS10')[peak], 2)) if easing else None
    return dict(
        month=now, expected=round(f, 1), lo80=round(f + quantile(err, .1), 1), hi80=round(f + quantile(err, .9), 1),
        p_negative=round(100 * sum(f + e < 0 for e in err) / len(err)), p_negative_all=round(100 * sum(v < 0 for v in y.values()) / len(y)),
        p_below_tips=round(100 * sum(f + e < tips[1] for e in err) / len(err)), average=round(sum(y.values()) / len(y), 1),
        cape=round(CAPE[now], 1), cape_max=round(max(CAPE.values()), 1), cape_max_month=max(CAPE, key=CAPE.get),
        tips=tips[1], tips_date=tips[0], y10=y10[1],
        y2000=dict(forecast=round(combo['2000-01'], 1), actual=round(y['2000-01'], 1)),
        expensive=dict(months=len(expensive), first=min(expensive), last=max(expensive),
                       worst=round(min(y[m] for m in expensive), 1), best=round(max(y[m] for m in expensive), 1)) if expensive else None,
        bias20=round(sum(y[t] - combo[t] for t in recent) / len(recent), 1), last_decade=dict(start=max(known), actual=round(y[max(known)], 1)),
        beyond=beyond, since_peak_rate=since,
    )


# ---- /why-gold/: long-run series for the charts (monthly) ----

def why_gold(db):
    gold, cpi, m2, wages = monthly(db, 'GOLD_M'), monthly(db, 'CPI'), monthly(db, 'M2'), monthly(db, 'WAGES')
    cpi_now = latest(db, 'CPI')[1]
    gd, g_now = latest(db, 'GOLD')
    months = sorted(gold)
    r1 = lambda v, n=2: round(v, n)

    # gold, consumer prices and money supply, indexed to August 1971 (the month the dollar left gold)
    base = '1971-08'
    since71 = [[m, r1(gold[m] / gold[base], 3), r1(cpi[m] / cpi[base], 3), r1(m2[m] / m2[base], 3) if m in m2 else None]
               for m in months if m >= base and m in cpi]
    # inflation-adjusted price (today's dollars) and its drawdown from the running peak
    real, peak, dd = [], 0, []
    for m in months:
        if m in cpi:
            v = gold[m] * cpi_now / cpi[m]
            peak = max(peak, v)
            real.append([m, round(v)])
            dd.append([m, r1(100 * (v / peak - 1), 1)])
    hours = [[m, r1(gold[m] / wages[m], 1)] for m in months if m in wages]
    # what a dollar of 1913 buys
    c13 = monthly(db, 'CPI_1913')
    years = sorted({m[:4] for m in c13})
    avg = {y: sum(v for m, v in c13.items() if m[:4] == y) / sum(1 for m in c13 if m[:4] == y) for y in years}
    dollar = [[y, r1(avg['1913'] / avg[y] * 100, 1)] for y in years]

    # gold priced in other currencies, indexed to January 2000 (ECB reference rates, monthly averages)
    fx = {c: monthly(db, f'EUR_{c}', 'mean') for c in ('USD', 'GBP', 'JPY', 'CHF', 'CNY', 'INR', 'TRY', 'BRL', 'ZAR')}
    usd = fx['USD']
    ccy = {}
    for c in ('USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CNY', 'INR', 'TRY', 'BRL', 'ZAR'):
        pts = []
        for m in months:
            if m < '2000-01' or m not in usd:  # every currency from the same start
                continue
            per_usd = 1 if c == 'USD' else 1 / usd[m] if c == 'EUR' else fx[c].get(m, 0) / usd[m]
            if per_usd:
                pts.append([m, gold[m] * per_usd])
        if pts:
            b = pts[0][1]
            ccy[c] = [[m, r1(100 * v / b, 1)] for m, v in pts]

    # central-bank gold, tonnes
    cb = {}
    for c in ('CHN', 'RUS', 'IND', 'TUR', 'POL', 'USA', 'DEU', 'JPN'):
        cb[c] = [[d[:7], round(v * OZ_T)] for d, v in rows(db, f'CB_GOLD_{c}')]

    debt = [[d[:7], r1(v, 1)] for d, v in rows(db, 'DEBT_GDP')]
    real_by_m = dict(real)
    debt_vs_gold = [[m, v, real_by_m.get(m)] for m, v in debt if m in real_by_m]

    cagr = lambda a, b, yrs: 100 * ((b / a) ** (1 / yrs) - 1)
    yrs = (int(months[-1][:4]) - 1971) + (int(months[-1][5:]) - 8) / 12
    g = gold[months[-1]]
    stats = dict(
        gold_cagr_71=r1(cagr(gold[base], g, yrs), 1), cpi_cagr_71=r1(cagr(cpi[base], cpi[months[-1]], yrs), 1),
        m2_cagr_71=r1(cagr(m2[base], m2[max(m2)], yrs), 1), gold_x_71=r1(g / gold[base], 1),
        cpi_x_71=r1(cpi[months[-1]] / cpi[base], 1), m2_x_71=r1(m2[max(m2)] / m2[base], 1),
        real_peak=max(real, key=lambda x: x[1]), dd_now=dd[-1][1], dd_worst=min(dd, key=lambda x: x[1]),
        hours_now=hours[-1], hours_low=min(hours, key=lambda x: x[1]), hours_high=max(hours, key=lambda x: x[1]),
        dollar_1913=dollar[-1], gold_now=round(g_now, 2), gold_date=gd,
        ccy_change={c: r1(v[-1][1] - 100, 0) for c, v in ccy.items()},
        cb_change={c: (v[0][0], v[0][1], v[-1][0], v[-1][1]) for c, v in cb.items() if v},
    )
    return dict(updated=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), stats=stats, since71=since71, real=real,
                drawdown=dd, hours=hours, dollar=dollar, currencies=ccy, central_banks=cb, debt_vs_gold=debt_vs_gold)
