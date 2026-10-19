"""Smallest checks that fail if the scoring maths breaks: python3 collector/test_collapse.py"""
import random
import forecast, score

assert score.shift('2026-01', -1) == '2025-12' and score.shift('2025-12', 13) == '2027-01'
assert score.span('2025-11', '2026-02') == ['2025-11', '2025-12', '2026-01', '2026-02']
assert score.pct([1, 2, 3, 4], 4) == 0.875 and score.pct([1, 2, 3, 4], 0) == 0
# the ridge logistic regression must find a positive effect where there is one
random.seed(1)
X = [[random.gauss(0, 1)] for _ in range(400)]
y = [int(random.random() < 1 / (1 + 2.718 ** (-2 * x[0]))) for x in X]
predict, w = forecast.fit(X, y)
assert w[1] > 0.5 and predict([2.0]) > 0.7 > 0.3 > predict([-2.0]), w

# the collector writes the live reading into the built page by element id
import collect
page = '<p class="answer" id="answer" data-band="checking" style="--len:9">CHECKING…</p><time id="updated">connecting…</time>'
html = collect.fill(page, 'answer', 'NO.', {'data-band': 'normal', 'style': '--len:3'})
assert html.startswith('<p class="answer" id="answer" data-band="normal" style="--len:3">NO.</p>'), html
assert '<time id="updated" datetime="2026-10-19T08:00:00Z">updated 19 Oct</time>' in collect.fill(page, 'updated', 'updated 19 Oct', {'datetime': '2026-10-19T08:00:00Z'})
strip = '<div class="flag-strip" id="flag-strip"></div><p>after</p>'
once = collect.fill(strip, 'flag-strip', raw='<a><b>x</b></a>')
assert collect.fill(once, 'flag-strip', raw='<a><b>y</b></a>') == strip.replace('></div>', '><a><b>y</b></a></div>')  # idempotent
# the summary must not call solid foundations weak
odd = dict(like=20, base=20)
r = dict(score=30, band=dict(label='Normal'), pillars=dict(stress=20), forecast=dict(probability=5), structural=30,
         structural_rank=dict(pct=25), flags=dict(high=[]), odds=dict(crisis_24m=odd, recession_24m=odd, bear_24m=odd, severe_24m=odd))
assert 'foundations are solid' in collect.summary(r) and 'no red flags' in collect.summary(r), collect.summary(r)
# unusual: a level not seen in 20+ years is flagged, a recent dip from a fresh record still counts, a gap is passed through
db = collect.connect(':memory:')
C = {'old': dict(label='A', now=('2026-10-01', 5.0), hist={'1990-01': 6.0, '2000-01': 4.0, '2026-09': 4.5}),
     'dip': dict(label='B', now=('2026-10-01', 9.0), hist={'1990-01': 6.0, '2026-02': 9.5}),
     'meh': dict(label='C', now=('2026-10-01', 5.0), hist={'2015-01': 6.0})}
gap = dict(key='g', label='G', unit='pp', value=2.0, date='2026-10-01', since='1997', usual=[0.1, 0.4], unusual=True)
u = {x['key']: x['note'] for x in score.unusual(db, C, lambda c: '', [gap])}
assert u == {'old': 'a level last seen in 1990', 'dip': 'near the record set in Feb 2026', 'g': 'usually +0.10 pp to +0.40 pp (since 1997)'}, u
# the 10-year outlook: a market returning a steady 5% a year after inflation must be forecast at 5%, whatever its CAPE
ms = score.span('1900-01', '2026-09')
P = {m: 1.05 ** (i / 12) for i, m in enumerate(ms)}
flat = lambda v: {m: v for m in ms}
combo, y = score.ten_year(P, flat(0.0), flat(1.0), flat(2.0), {m: 10 + i % 7 for i, m in enumerate(ms)})
assert abs(y['1950-01'] - 5) < 1e-9 and abs(combo[max(combo)] - 5) < 1e-6, (y['1950-01'], combo[max(combo)])
print('ok')
