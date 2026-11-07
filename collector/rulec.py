"""Acceptance rule C (CLAUDE.md) for a candidate score input, against the live score as baseline.

    python3 collector/rulec.py            # calibrates the placebo lines on today's score, then tests CANDIDATES

A candidate is a monthly history {YYYY-MM: value} (higher = more risk), the pillar its meaning says, and the theme it joins
(a new name = its own theme at standard weight). Its series must already be in the database (add it to collect.py's SERIES,
then `collect.py --only KEY`). Stage 1: among months the score rates alike (quintiles), the candidate's stratified AUC on the
pillar's own job (stress: a crisis under way; others: a crisis starting within 24 months) beats the 99th percentile of random
autocorrelated series, and is ≥ 0.5 in 1973–99 and 2000+. Stage 2: added, no target falls by more than 0.02 and months ≥ 70
stay ≥ 90% near a crisis. Expect 2% of random candidates to pass: report passes against tries.
"""
import sys, os, math, random, copy, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score, forecast
from score import shift, span, ffill, expanding, auc, monthly, pct, SEVERE

db = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'collapse.db'))
C0, W, X, months = score.components(db)
sp, nf = W['spread'], W['nfci']
now = max(sp)
live_x = [sp[now], min(sp[x] for k in range(18) if (x := shift(now, -k)) in sp), nf[now], nf[now] - nf[shift(now, -6)], score.latest(db, 'EBP')[1]]
F = forecast.run(sp, nf, W['ebp'], W['usrec'], months, live_x)
warn = score.warning_pillar(W, F, months)
ev = score.events(db, months, X)
H0, P0 = score.history(C0, warn, months)
TEST = [m for m in sorted(H0) if m <= shift(max(H0), -24)]  # every month whose 24 months are known
cr_starts, cr_inside = ev['crisis']
rec_starts, rec_inside = ev['recession']
bear_starts, bear_inside = ev['bear']
within = lambda m, starts, h: any(m < s <= shift(m, h) for s in starts)


def targets(H):
    ms = [m for m in TEST if m in H]
    free = [m for m in ms if m not in cr_inside]
    rf, bf = [m for m in ms if m not in rec_inside], [m for m in ms if m not in bear_inside]
    T = dict(crisis_now=auc([H[m] for m in ms], [m in cr_inside for m in ms]),
             crisis_12=auc([H[m] for m in free], [within(m, cr_starts, 12) for m in free]),
             crisis_24=auc([H[m] for m in free], [within(m, cr_starts, 24) for m in free]),
             recession_12=auc([H[m] for m in rf], [within(m, rec_starts, 12) for m in rf]),
             severe_12=auc([H[m] for m in free], [within(m, SEVERE, 12) for m in free]),
             bear_12=auc([H[m] for m in bf], [within(m, bear_starts, 12) for m in bf]))
    near = lambda m: m in cr_inside or within(m, cr_starts, 12)
    hit = [m for m in ms if H[m] >= 70]
    T['ge70_near'] = round(100 * sum(map(near, hit)) / len(hit)) if hit else None
    return T


def job(pillar):
    ms = TEST if pillar == 'stress' else [m for m in TEST if m not in cr_inside]
    return ms, {m: (m in cr_inside) if pillar == 'stress' else within(m, cr_starts, 24) for m in ms}


def strat_auc(S, pillar, lo='1973-01', hi='2099-12'):
    """AUC within each quintile of the baseline score, pooled by pairs."""
    ms, lab = job(pillar)
    ms = [m for m in ms if m in S and lo <= m <= hi]
    if len(ms) < 60:
        return None
    ranked, num, den = sorted(ms, key=H0.get), 0.0, 0.0
    q = len(ranked) // 5
    for i in range(5):
        st = ranked[i * q:(i + 1) * q] if i < 4 else ranked[i * q:]
        n_pos = sum(lab[m] for m in st)
        if 0 < n_pos < len(st):
            num += auc([S[m] for m in st], [lab[m] for m in st]) * n_pos * (len(st) - n_pos)
            den += n_pos * (len(st) - n_pos)
    return round(num / den, 3) if den else None


def stage1(hist, pillar):
    S = expanding(ffill(hist, months, 6), months)
    return dict(all=strat_auc(S, pillar), era1=strat_auc(S, pillar, hi='1999-12'), era2=strat_auc(S, pillar, lo='2000-01'))


def placebo(pillar, n=400):
    random.seed(7)
    out = []
    for _ in range(n):
        phi, v, h = random.choice([0.9, 0.97, 0.99, 1.0]), 0.0, {}
        for m in span('1950-01', max(months)):
            v = phi * v + random.gauss(0, 1)
            h[m] = v
        out.append(stage1(h, pillar)['all'])
    out = sorted(x for x in out if x is not None)
    return out[int(0.99 * (len(out) - 1))]


def test(key, pillar, theme, hist, line):
    s1 = stage1(hist, pillar)
    pass1 = s1['all'] is not None and s1['all'] > line and (s1['era1'] or 0) >= 0.5 and (s1['era2'] or 0) >= 0.5
    C, TH = copy.deepcopy(C0), copy.deepcopy(score.THEMES)
    C[key] = dict(label=key, pillar=pillar, hist=ffill(hist, months, 6), now=(max(hist), hist[max(hist)]), series=key, unit='x', obs=None)
    TH[pillar][theme] = list(TH[pillar].get(theme, [])) + [key]
    saved, score.THEMES = score.THEMES, TH
    H1, P1 = score.history(C, warn, months)
    score.THEMES = saved
    T1 = targets(H1)
    harm = {k: round(T1[k] - T0[k], 3) for k in T0 if k != 'ge70_near'}
    pass2 = all(v >= -0.02 for v in harm.values()) and (T1['ge70_near'] or 0) >= 90
    last = max(m for m in H0 if m in H1)
    print(f'\n{key} [{pillar} / {theme}]  months {min(hist)}..{max(hist)}')
    print(f'  stage 1: stratified AUC {s1["all"]} (1973–99 {s1["era1"]}, 2000+ {s1["era2"]}), line {line} → {"PASS" if pass1 else "fail"}')
    print(f'  stage 2: {harm}, ≥70 near a crisis {T1["ge70_near"]}% → {"PASS" if pass2 else "fail"}')
    print(f'  score {last}: {H0[last]:.1f} → {H1[last]:.1f}; latest {hist[max(hist)]:.4f} = {100 * pct(sorted(hist.values()), hist[max(hist)]):.0f}th pct of its history')
    return pass1 and pass2


if __name__ == '__main__':
    T0 = targets(H0)
    print(f'baseline v{score.VERSION}:', T0)
    lines = {p: placebo(p) for p in ('stress', 'money', 'fiscal', 'buildup')}
    print('placebo 99th percentiles:', lines)

    # ---- candidates: key, pillar, theme, monthly history (higher = more risk) ----
    gdp_q = monthly(db, 'GDP')
    mq = monthly(db, 'MARGIN')
    mg = dict(sorted((shift(m, 5), v / 1000 / gdp_q[m]) for m, v in mq.items() if m in gdp_q))
    CANDIDATES = [
        # rejected in the v9 round: the 3-year change in margin loans over GDP (0.709 against a 0.719 line)
        ('margin_3y', 'buildup', 'leverage boom', ffill({m: mg[m] - mg[shift(m, -36)] for m in mg if shift(m, -36) in mg}, months, 4)),
    ]
    passed = sum(test(k, p, t, h, lines[p]) for k, p, t, h in CANDIDATES)
    print(f'\n{passed} of {len(CANDIDATES)} passed; {0.02 * len(CANDIDATES):.2f} expected by chance')
