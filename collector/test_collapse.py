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
# odds: months already inside a crisis don't count, so a calm month's odds mean "one starts", like the recession card
ms = score.span('1990-01', '1999-12')
ev = dict(crisis=(['1995-01'], set(score.span('1995-01', '1995-06'))), recession=([], set()), bear=([], set()))
o = score.odds({m: 50.0 for m in ms}, ev, 50.0, '1999-12')
assert o['crisis_12m'] == dict(like=12, months=102, base=12), o['crisis_12m']
# ...and months scoring far from today carry no weight: the 12 before the crisis scored 90, so a 50 saw none follow
o = score.odds({m: 90.0 if '1994' <= m < '1995' else 50.0 for m in ms}, ev, 50.0, '1999-12')
assert o['crisis_12m'] == dict(like=0, months=90, base=12), o['crisis_12m']
# the ticker's change: the previous day's value for daily data, nothing without history
db.executemany('INSERT INTO obs VALUES (?,?,?)', [('D', '2026-10-16', 1.0), ('D', '2026-10-19', 2.0)])
assert score.ago(db, 'D') == 1.0 and score.ago(db, 'none') is None
# unrest: a country counts for 12 months after a major event, once however many events
ev = {'A': [(f'{2020 + m // 12}-{m % 12 + 1:02d}-01', int(m in (0, 1))) for m in range(14)], 'B': [('2021-01-01', 1), ('2021-02-01', 0)]}
assert collect.unrest_count(ev) == [('2020-12-01', 1), ('2021-01-01', 2), ('2021-02-01', 1)], collect.unrest_count(ev)
# society: news tone and confidence report their low as the record; percentiles rank the latest value in its own history
db.executemany('INSERT INTO obs VALUES (?,?,?)', [('NEWS_SENT', f'2026-0{i}-01', v) for i, v in enumerate((0.1, -0.5, 0.3, 0.2), 1)])
n = score.society(db)['news']
assert (n['max'], n['max_month'], n['pct']) == (-0.5, '2026-02', 62), n  # mid-rank: 2.5 of 4
# households: a $100k home with 20% down at 6% costs $479.64 a month; years of pay compare 1970 with now and keep the worst
assert abs(score.mortgage_payment(100000, 6.0) - 479.64) < 0.01, score.mortgage_payment(100000, 6.0)
db = collect.connect(':memory:')
db.executemany('INSERT INTO obs VALUES (?,?,?)', [('HOME_PRICE', '1970-01-01', 20800.0), ('WAGES', '1970-01-01', 2.5), ('HOME_PRICE', '2000-01-01', 166400.0),
                                                  ('WAGES', '2000-01-01', 10.0), ('HOME_PRICE', '2026-04-01', 416000.0), ('WAGES', '2026-04-01', 40.0)])
y = score.households(db)['years_pay']
assert (y['then'], y['then_year'], y['now'], y['worst'], y['worst_date'], y['bad']) == (4.0, '1970', 5.0, 8.0, '2000-01', 50), y
print('ok')
