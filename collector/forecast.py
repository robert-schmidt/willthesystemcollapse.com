"""12-month US recession probability from the yield curve, financial conditions and credit (the score's "warning" pillar).

Logistic regression (ridge, stdlib Newton solver) on five monthly inputs:
  spread      10-year minus 3-month Treasury yield (inverted curve → recession risk)
  inv18       the lowest spread of the last 18 months (inversions lead recessions by 6–24 months, often after un-inverting)
  nfci        Chicago Fed National Financial Conditions Index (tight conditions → risk)
  nfci_chg6   6-month change in NFCI (tightening)
  ebp         Gilchrist–Zakrajšek excess bond premium (credit investors' risk appetite)
Target: an NBER recession starts within the next 12 months.
Backtest is walk-forward: refit every January on months whose 12-month outcome was already known, predict that year.
"""
import math

FEATURES = ['spread', 'inv18', 'nfci', 'nfci_chg6', 'ebp']
H, LAM, START = 12, 20.0, '1979-01'


def shift(m, k):
    y, mo = map(int, m.split('-'))
    mo += k
    return f'{y + (mo - 1) // 12:04d}-{(mo - 1) % 12 + 1:02d}'


def solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [x - f * y for x, y in zip(M[r], M[c])]
    return [M[i][n] / M[i][i] for i in range(n)]


def sigmoid(z):
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def fit(X, y):
    """Standardise, then ridge logistic regression (intercept unpenalised). Returns a predict(x) function and weights."""
    mu = [sum(c) / len(c) for c in zip(*X)]
    sd = [max(1e-9, (sum((v - a) ** 2 for v in c) / len(c)) ** .5) for c, a in zip(zip(*X), mu)]
    Z = [[1.0] + [(v - a) / s for v, a, s in zip(x, mu, sd)] for x in X]
    k = len(Z[0])
    w = [0.0] * k
    for _ in range(50):
        g, Hs = [0.0] * k, [[0.0] * k for _ in range(k)]
        for z, t in zip(Z, y):
            p = sigmoid(sum(a * b for a, b in zip(w, z)))
            for i in range(k):
                g[i] += (p - t) * z[i]
                for j in range(k):
                    Hs[i][j] += p * (1 - p) * z[i] * z[j]
        for i in range(1, k):
            g[i] += LAM * w[i]
            Hs[i][i] += LAM
        step = solve(Hs, g)
        w = [a - b for a, b in zip(w, step)]
        if max(map(abs, step)) < 1e-9:
            break
    predict = lambda x: sigmoid(sum(a * b for a, b in zip(w, [1.0] + [(v - m) / s for v, m, s in zip(x, mu, sd)])))
    return predict, [w[0]] + [b / s for b, s in zip(w[1:], sd)]  # weights per raw unit, for display


def features(spread, nfci, ebp, months):
    rows = {}
    for m in months:
        win = [spread.get(shift(m, -k)) for k in range(18)]
        x = [spread.get(m), min(win) if None not in win else None, nfci.get(m),
             nfci[m] - nfci[shift(m, -6)] if m in nfci and shift(m, -6) in nfci else None, ebp.get(m)]
        if None not in x:
            rows[m] = x
    return rows


def run(spread, nfci, ebp, usrec, months, live_x=None):
    """Walk-forward probabilities (from START) + today's probability from a fit on all resolved months."""
    starts = [m for m in sorted(usrec) if usrec[m] == 1 and usrec.get(shift(m, -1)) == 0]
    last_known = max(usrec)

    def label(m):  # None while inside a recession or before the outcome is known
        if usrec.get(m) == 1 or shift(m, H) > last_known:
            return None
        return int(any(m < s <= shift(m, H) for s in starts))

    rows = features(spread, nfci, ebp, months)
    labels = {m: label(m) for m in rows}
    out, model = {}, None
    for t in [m for m in sorted(rows) if m >= START]:
        if model is None or t.endswith('-01'):
            train = [m for m in rows if m <= shift(t, -H) and labels[m] is not None]
            model, _ = fit([rows[m] for m in train], [labels[m] for m in train])
        out[t] = model(rows[t])
    final, weights = fit([rows[m] for m in rows if labels[m] is not None], [labels[m] for m in rows if labels[m] is not None])
    x = live_x or rows[max(rows)]
    now = final(x)

    # out-of-sample track record: calibration buckets and hit rate per recession
    scored = [m for m in out if labels[m] is not None]
    buckets = []
    for lo, hi in ((0, .2), (.2, .4), (.4, .6), (.6, 1.01)):
        ms = [m for m in scored if lo <= out[m] < hi]
        buckets.append(dict(lo=round(100 * lo), hi=min(100, round(100 * hi)), months=len(ms),
                            hit=round(100 * sum(labels[m] for m in ms) / len(ms)) if ms else None))
    ys = [labels[m] for m in scored]
    ps = [out[m] for m in scored]
    pos, neg = sorted(p for p, y in zip(ps, ys) if y), sorted(p for p, y in zip(ps, ys) if not y)
    import bisect
    auc = sum(bisect.bisect_left(neg, p) + .5 * (bisect.bisect_right(neg, p) - bisect.bisect_left(neg, p)) for p in pos) / (len(pos) * len(neg))
    episodes = [dict(start=s, peak_before=round(100 * max((out[m] for m in out if shift(s, -H) <= m < s), default=0)),
                     peak_month=max((m for m in out if shift(s, -H) <= m < s), key=out.get, default=None)) for s in starts if s >= START]
    return dict(now=now, x=dict(zip(FEATURES, x)), weights=dict(zip(['intercept'] + FEATURES, weights)), history=out,
                track=dict(since=START, auc=round(auc, 2), buckets=buckets, recessions=episodes,
                           brier=round(sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys), 3),
                           base=round(sum(ys) / len(ys), 3)))
