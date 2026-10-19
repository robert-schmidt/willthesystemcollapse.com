#!/usr/bin/env python3
"""Will the system collapse? — data collector for willthesystemcollapse.com.

Fetches public series (FRED, OFR, ECB, Fed, US Treasury, IMF, World Bank, central banks, exchange tickers) into
SQLite, scores system stress (score.py) and writes the JSON the page polls (collapse.json), plus why-gold.json (gold
history, read cross-origin). Stdlib only.

    python3 collector/collect.py --db ~/app/data/collapse.db --out ~/web/willthesystemcollapse.com/public_html/live

Cron runs it every 15 minutes. Each series has a TTL, so a run normally fetches only the live prices; a failed source
keeps its last good data and is reported in the JSON's `sources` list.
"""
import argparse, csv, functools, io, json, os, re, sqlite3, sys, time, urllib.request, zipfile
import xml.etree.ElementTree as ET
from html import escape
from datetime import date, datetime, timedelta, timezone

UA = 'willthesystemcollapse.com monitor (+https://willthesystemcollapse.com/)'
OZ = 31.1034768  # grams per troy ounce


def get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


# ---- fetchers: each returns [(date 'YYYY-MM-DD' or ISO datetime, value)] ----

def fred(sid):
    rows = csv.reader(io.StringIO(get(f'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}').decode()))
    next(rows)
    return [(d, num(v)) for d, v in rows if num(v) is not None]


def ofr(col):
    rows = csv.DictReader(io.StringIO(get('https://www.financialresearch.gov/financial-stress-index/data/fsi.csv').decode()))
    return [(r['Date'], num(r[col])) for r in rows if num(r[col]) is not None]


def ebp(col):
    rows = csv.DictReader(io.StringIO(get('https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv').decode()))
    return [(r['date'], num(r[col])) for r in rows if num(r[col]) is not None]


def ecb(key):
    flow, rest = key.split('.', 1)
    raw = get(f'https://data-api.ecb.europa.eu/service/data/{flow}/{rest}?format=csvdata').decode()
    return [(r['TIME_PERIOD'], num(r['OBS_VALUE'])) for r in csv.DictReader(io.StringIO(raw)) if num(r['OBS_VALUE']) is not None]


def treasury_debt(_):
    url = ('https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny'
           '?fields=record_date,tot_pub_debt_out_amt&sort=-record_date&page[size]=10000')
    return [(r['record_date'], num(r['tot_pub_debt_out_amt'])) for r in json.loads(get(url))['data']]


def treasury_rate(_):
    url = ('https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates'
           '?fields=record_date,avg_interest_rate_amt&filter=security_desc:eq:Total%20Marketable&sort=-record_date&page[size]=10000')
    return [(r['record_date'], num(r['avg_interest_rate_amt'])) for r in json.loads(get(url))['data']]


def bcra(code, have):
    """Argentina's central bank daily reference (USD per oz). Max 1000 rows per call, so backfill in 3-year chunks."""
    start = date.fromisoformat(have) - timedelta(days=10) if have else date(2002, 1, 1)
    out = []
    while start <= date.today():
        end = min(start + timedelta(days=3 * 365), date.today())
        url = f'https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones/{code}?fechadesde={start}&fechahasta={end}&limit=1000'
        for r in json.loads(get(url))['results']:
            v = num(r['detalle'][0]['tipoPase']) if r['detalle'] else None
            if v:
                out.append((r['fecha'], v))
        start = end + timedelta(days=1)
    return out


def nbp_gold(_, have):
    """Poland's central bank: gold PLN/g ÷ PLN per USD → USD per oz. Max 367 days per call."""
    start = date.fromisoformat(have) - timedelta(days=10) if have else date(2013, 1, 2)
    out = []
    while start <= date.today():
        end = min(start + timedelta(days=360), date.today())
        g = {r['data']: r['cena'] for r in json.loads(get(f'https://api.nbp.pl/api/cenyzlota/{start}/{end}?format=json'))}
        fx = json.loads(get(f'https://api.nbp.pl/api/exchangerates/rates/a/usd/{start}/{end}/?format=json'))['rates']
        out += [(r['effectiveDate'], g[r['effectiveDate']] / r['mid'] * OZ) for r in fx if r['effectiveDate'] in g]
        start = end + timedelta(days=1)
    return out


def pink_sheet(col):
    """World Bank Pink Sheet monthly prices (CC BY 4.0). The xlsx link changes each month, so find it first."""
    page = get('https://www.worldbank.org/en/research/commodity-markets', {'User-Agent': 'Mozilla/5.0'}).decode()
    url = re.search(r'https://[^"]*CMO-Historical-Data-Monthly\.xlsx', page).group(0)
    z = zipfile.ZipFile(io.BytesIO(get(url)))
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    shared = [''.join(t.text or '' for t in si.iter(f'{{{ns["m"]}}}t'))
              for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns)]
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    rid = next(s.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
               for s in wb.iter(f'{{{ns["m"]}}}sheet') if s.get('name') == 'Monthly Prices')
    target = next(r.get('Target') for r in rels if r.get('Id') == rid)
    sheet = ET.fromstring(z.read('xl/' + target.lstrip('/').removeprefix('xl/')))

    def val(c):
        v = c.find('m:v', ns)
        if v is None:
            return None
        return shared[int(v.text)] if c.get('t') == 's' else v.text

    header, out = None, []
    for row in sheet.iter(f'{{{ns["m"]}}}row'):
        cells = {re.match(r'[A-Z]+', c.get('r')).group(0): val(c) for c in row.findall('m:c', ns)}
        if header is None:
            name = next((k for k, v in cells.items() if v and v.strip().lower() == col.lower()), None)
            if name:
                header = name
            continue
        m = re.fullmatch(r'(\d{4})M(\d{2})', (cells.get('A') or '').strip())
        if m and num(cells.get(header)) is not None:
            out.append((f'{m[1]}-{m[2]}-01', num(cells[header])))
    return out


def imf_gold(country):
    """Central-bank gold holdings, millions of fine troy ounces (IMF International Liquidity)."""
    url = f'https://api.imf.org/external/sdmx/2.1/data/IMF.STA,IL,13.0.1/{country}.RGV_REVS.FTO.M?startPeriod=1990'
    raw = get(url, {'Accept': 'application/vnd.sdmx.data+csv;version=1.0.0'}).decode()
    out = []
    for r in csv.DictReader(io.StringIO(raw)):
        y, m = r['TIME_PERIOD'].split('-M')
        if num(r['OBS_VALUE']) is not None:
            out.append((f'{y}-{m}-01', num(r['OBS_VALUE']) / 1e6))
    return out


_imf_debt = {}


def imf_debt(country):
    """General government gross debt, % of GDP (IMF WEO via DataMapper; recent years are IMF estimates)."""
    if not _imf_debt:
        _imf_debt.update(json.loads(get('https://www.imf.org/external/datamapper/api/v1/GGXWDG_NGDP'))['values']['GGXWDG_NGDP'])
    return [(f'{y}-12-31', v) for y, v in _imf_debt.get(country, {}).items() if v is not None]


_wb = {}


def world_bank(arg):
    """World Bank API, all countries in one call per indicator (CC BY 4.0). arg = (indicator, source, country)."""
    ind, src, country = arg
    if (ind, src) not in _wb:
        url = f'https://api.worldbank.org/v2/country/{";".join(WB_COUNTRIES)}/indicator/{ind}?source={src}&format=json&per_page=20000'
        out = {}
        for r in json.loads(get(url))[1] or []:
            c = r['countryiso3code'] or r['country']['id']
            q = re.fullmatch(r'(\d{4})Q(\d)', r['date'])
            d = f'{q[1]}-{3 * int(q[2]):02d}-01' if q else f'{r["date"]}-12-31'
            if r['value'] is not None:
                out.setdefault({'US': 'USA', 'EU': 'EMU', 'XC': 'EMU'}.get(c, c), []).append((d, float(r['value'])))
        _wb[(ind, src)] = out
    return _wb[(ind, src)].get(country, [])


WB_COUNTRIES = ['USA', 'JPN', 'DEU', 'GBR', 'FRA', 'ITA', 'CAN', 'CHN', 'IND', 'BRA', 'EMU']


def nyfed_mct(_):
    """New York Fed Multivariate Core Trend inflation (© Federal Reserve Bank of New York)."""
    rows = list(csv.reader(io.StringIO(get('https://www.newyorkfed.org/medialibrary/Research/Interactives/Data/mct/mct-chart-data.csv').decode('utf-8-sig'))))
    head = next(i for i, r in enumerate(rows) if len(r) > 2 and r[1] == 'Date')
    out = []
    for r in rows[head + 1:]:
        if len(r) > 3 and num(r[3]) is not None:  # columns: date, lower band, MCT, upper band, …
            d = r[1].strip()
            m = re.fullmatch(r'(\d{4})-(\d{2})(-\d{2})?', d) or re.fullmatch(r'(\d{1,2})/\d{1,2}/(\d{4})', d)
            if m and '-' in d:
                out.append((f'{m[1]}-{m[2]}-01', num(r[3])))
            elif m:
                out.append((f'{m[2]}-{int(m[1]):02d}-01', num(r[3])))
    return out


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def shfe_gold(_, have):
    """Shanghai Futures Exchange daily settlement of the most-traded gold contract, CNY per gram. One file per trading day
    (404 on holidays). SHFE allows non-commercial use: only the derived premium is published, never the prices.
    The first run samples one day a week back to 2013, then every trading day."""
    day = date.fromisoformat(have[:10]) + timedelta(days=1) if have else date(2013, 1, 2)
    out = []
    while day < date.today() + timedelta(days=1):
        if day.weekday() < 5 and (have or day.weekday() == 2 or day > date.today() - timedelta(days=14)):
            try:
                rows = json.loads(get(f'https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{day:%Y%m%d}.dat'))['o_curinstrument']
                au = [r for r in rows if r['PRODUCTID'].strip() == 'au_f' and str(r['DELIVERYMONTH']).isdigit()]
                if au:
                    out.append((str(day), float(max(au, key=lambda r: r['OPENINTEREST'])['SETTLEMENTPRICE'])))
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
            time.sleep(0.3)  # be gentle with their server
        day += timedelta(days=1)
    return out


def gold_ny_premium(_):
    """New York gold futures (Coinbase Derivatives' contract settles on the COMEX price) over spot gold (PAX Gold, which
    is backed by London bars), minus the cost of carry, %. Both quotes come from Coinbase at the same moment."""
    fut = [p for p in json.loads(get('https://api.coinbase.com/api/v3/brokerage/market/products?product_type=FUTURE'
                                     '&contract_expiry_type=EXPIRING'))['products']
           if p['product_id'].startswith('GOL-') and p['price'] and float(p['volume_24h'] or 0) > 0]
    f = min(fut, key=lambda p: p['future_product_details']['contract_expiry'])  # front month
    days = (datetime.fromisoformat(f['future_product_details']['contract_expiry'].replace('Z', '+00:00')) -
            datetime.now(timezone.utc)).days
    spot = num(json.loads(get('https://api.exchange.coinbase.com/products/PAXG-USD/ticker'))['price'])
    # ponytail: carry at a fixed 4% a year; a 1 pp error moves a two-month contract by under 0.2%, blowouts are 1–4%
    return [(now_iso(), 100 * (float(f['price']) / spot - 1 - 0.04 * days / 365))]


@functools.lru_cache(maxsize=1)
def shiller_cells():
    """Robert J. Shiller's monthly US stock-market data (shillerdata.com; the file link changes, so find it first)."""
    import xls_min  # ponytail: a 70-line stdlib reader for the numeric cells of an .xls, instead of a dependency
    page = get('https://shillerdata.com/').decode()
    return xls_min.sheet(get('https:' + re.search(r'href="(//[^"]*/ie_data\.xls)', page).group(1)), 'Data')


def shiller(col):
    """One column of Shiller's Data sheet (1 price, 2 dividend, 4 CPI, 6 10-year yield, 12 CAPE) as monthly rows. Only
    derived numbers (the 10-year outlook, the CAPE level) are published, never the series. The newest row is partial
    (a mid-month close and an estimated CPI), so it is left out."""
    cells = shiller_cells()
    month = lambda r: f'{int(cells[r, 0]):04d}-{round((cells[r, 0] - int(cells[r, 0])) * 100):02d}-01'
    newest = max(month(r) for (r, c) in cells if c == 1 and r >= 8 and (r, 0) in cells)
    return [(month(r), v) for (r, c), v in sorted(cells.items()) if c == col and r >= 8 and (r, 0) in cells and month(r) < newest]


def kraken(pair):
    r = json.loads(get(f'https://api.kraken.com/0/public/Ticker?pair={pair}'))['result']
    return [(now_iso(), num(next(iter(r.values()))['c'][0]))]


def bitstamp(pair):
    return [(now_iso(), num(json.loads(get(f'https://www.bitstamp.net/api/v2/ticker/{pair}/'))['last']))]


def coinbase(pair):
    return [(now_iso(), num(json.loads(get(f'https://api.exchange.coinbase.com/products/{pair}/ticker'))['price']))]


# ---- what to collect: key → (fetcher, argument, TTL hours, label, source) ----
D, M, W = 12, 24, 24 * 7  # TTLs: daily data twice a day, monthly data daily, slow annual/IMF data weekly
LIVE = 0.2

SERIES = {
    # market stress
    'NFCI': (fred, 'NFCI', D, 'Chicago Fed National Financial Conditions Index', 'Federal Reserve Bank of Chicago via FRED'),
    'STLFSI4': (fred, 'STLFSI4', D, 'St. Louis Fed Financial Stress Index', 'Federal Reserve Bank of St. Louis'),
    'OFR_FSI': (ofr, 'OFR FSI', D, 'OFR Financial Stress Index', 'Office of Financial Research'),
    'CISS_EA': (ecb, 'CISS.D.U2.Z0Z.4F.EC.SS_CIN.IDX', D, 'Euro-area systemic stress (CISS)', 'European Central Bank'),
    'VIX': (fred, 'VIXCLS', D, 'VIX volatility index', 'Cboe via FRED'),
    'BAA10Y': (fred, 'BAA10Y', D, 'Corporate (Baa) spread over 10y Treasuries', "Moody's via FRED"),
    'BAA': (fred, 'BAA', M, 'Baa corporate yield', "Moody's via FRED"),
    'GS10': (fred, 'GS10', M, '10-year Treasury yield (monthly)', 'Federal Reserve via FRED'),
    'TB3MS': (fred, 'TB3MS', M, '3-month Treasury bill (monthly)', 'Federal Reserve via FRED'),
    'EBP': (ebp, 'ebp', M, 'Excess bond premium', 'Federal Reserve Board (Gilchrist–Zakrajšek)'),
    'USREC': (fred, 'USREC', M, 'US recession indicator (NBER dates)', 'NBER via FRED'),
    'ANFCI': (fred, 'ANFCI', D, 'Adjusted financial conditions (Chicago Fed)', 'Federal Reserve Bank of Chicago via FRED'),
    'T10Y3M': (fred, 'T10Y3M', D, 'Yield curve: 10y minus 3m', 'Federal Reserve Bank of St. Louis'),
    'DGS10': (fred, 'DGS10', D, 'US 10-year Treasury yield', 'Federal Reserve via FRED'),
    'DGS2': (fred, 'DGS2', D, 'US 2-year Treasury yield', 'Federal Reserve via FRED'),
    'DGS30': (fred, 'DGS30', D, 'US 30-year Treasury yield', 'Federal Reserve via FRED'),
    'GS2': (fred, 'GS2', M, '2-year Treasury yield (monthly)', 'Federal Reserve via FRED'),
    'T10Y2Y': (fred, 'T10Y2Y', D, 'Yield curve: 10y minus 2y', 'Federal Reserve Bank of St. Louis'),
    'GDP': (fred, 'GDP', M, 'US nominal GDP (annual rate)', 'BEA via FRED'),
    'DEBT_AVG_RATE': (treasury_rate, None, M, 'Average interest rate on US marketable debt', 'US Treasury, Fiscal Data'),
    'FEDFUNDS': (fred, 'FEDFUNDS', M, 'Federal funds rate (monthly)', 'Federal Reserve via FRED'),
    'DFF': (fred, 'DFF', D, 'Federal funds rate (daily)', 'Federal Reserve via FRED'),
    'UNRATE': (fred, 'UNRATE', M, 'US unemployment rate', 'BLS via FRED'),
    'CORE_CPI': (fred, 'CPILFESL', M, 'US core CPI (ex food & energy)', 'BLS via FRED'),
    'PCE': (fred, 'PCEPI', M, 'PCE price index', 'BEA via FRED'),
    'STICKY_CPI': (fred, 'CORESTICKM159SFRBATL', M, 'Sticky-price CPI, 12 months (Atlanta Fed)', 'Federal Reserve Bank of Atlanta via FRED'),
    'MEDIAN_CPI': (fred, 'MEDCPIM158SFRBCLE', M, 'Median CPI, annualised monthly (Cleveland Fed)', 'Federal Reserve Bank of Cleveland via FRED'),
    'INFL_5Y5Y': (fred, 'T5YIFR', D, '5-year, 5-year forward inflation expectation', 'Federal Reserve Bank of St. Louis'),
    'BREAKEVEN10': (fred, 'T10YIE', D, '10-year breakeven inflation', 'Federal Reserve Bank of St. Louis'),
    'PPI': (fred, 'PPIACO', M, 'Producer price index, all commodities', 'BLS via FRED'),
    'MONETARY_BASE': (fred, 'BOGMBASE', M, 'US monetary base', 'Federal Reserve via FRED'),
    'POT_GDP': (fred, 'GDPPOT', M, 'Real potential GDP (CBO)', 'CBO via FRED'),
    'REAL_GDP': (fred, 'GDPC1', M, 'US real GDP', 'BEA via FRED'),
    'DEFICIT_GDP': (fred, 'FYFSGDA188S', W, 'Federal surplus or deficit, % of GDP (annual)', 'OMB via FRED'),
    'FOREIGN_HELD': (fred, 'FDHBFIN', M, 'Federal debt held by foreign investors', 'US Treasury via FRED'),
    'NIIP': (fred, 'IIPUSNETIQ', M, 'US net international investment position', 'BEA via FRED'),
    'HOME_PRICE': (fred, 'MSPUS', M, 'Median US home sale price', 'Census/HUD via FRED'),
    'SAHM': (fred, 'SAHMREALTIME', M, 'Sahm rule recession indicator (real time)', 'Claudia Sahm via FRED'),
    'DFII10': (fred, 'DFII10', D, 'US 10-year real yield (TIPS)', 'Federal Reserve via FRED'),
    'NIKKEI': (fred, 'NIKKEI225', D, 'Nikkei 225', 'Nikkei Inc. via FRED'),
    'EQ_US': (fred, 'SPASTT01USM661N', M, 'US share prices (OECD index)', 'OECD via FRED'),
    'EQ_DE': (fred, 'SPASTT01DEM661N', M, 'Germany share prices (OECD index)', 'OECD via FRED'),
    'EQ_GB': (fred, 'SPASTT01GBM661N', M, 'UK share prices (OECD index)', 'OECD via FRED'),
    'EQ_JP': (fred, 'SPASTT01JPM661N', M, 'Japan share prices (OECD index)', 'OECD via FRED'),
    'EQ_CN': (fred, 'SPASTT01CNM661N', M, 'China share prices (OECD index)', 'OECD via FRED'),
    'EQ_IN': (fred, 'SPASTT01INM661N', M, 'India share prices (OECD index)', 'OECD via FRED'),
    # money, gold, currencies
    'GOLD': (bcra, 'XAU', 6, 'Gold, USD per ounce (daily reference)', 'Banco Central de la República Argentina'),
    'SILVER': (bcra, 'XAG', 6, 'Silver, USD per ounce (daily reference)', 'Banco Central de la República Argentina'),
    'GOLD_NBP': (nbp_gold, None, 6, 'Gold, USD per ounce (Warsaw reference)', 'Narodowy Bank Polski'),
    'GOLD_M': (pink_sheet, 'Gold', W, 'Gold, USD per ounce (monthly average)', 'World Bank Pink Sheet (CC BY 4.0)'),
    'SILVER_M': (pink_sheet, 'Silver', W, 'Silver, USD per ounce (monthly average)', 'World Bank Pink Sheet (CC BY 4.0)'),
    'GVZ': (fred, 'GVZCLS', D, 'Gold volatility index', 'Cboe via FRED'),
    'CPI': (fred, 'CPIAUCSL', M, 'US consumer prices', 'BLS via FRED'),
    'M2': (fred, 'M2SL', M, 'US money supply (M2)', 'Federal Reserve via FRED'),
    'FED_BS': (fred, 'WALCL', D, 'Federal Reserve balance sheet', 'Federal Reserve via FRED'),
    'USD': (fred, 'DTWEXBGS', D, 'Dollar index (broad)', 'Federal Reserve via FRED'),
    'USD_OLD': (fred, 'TWEXMMTH', W, 'Dollar index (major currencies, to 2019)', 'Federal Reserve via FRED'),
    'BTC': (fred, 'CBBTCUSD', D, 'Bitcoin, USD', 'Coinbase via FRED'),
    **{f'EUR_{c}': (ecb, f'EXR.D.{c}.EUR.SP00.A', D, f'{c} per euro', 'European Central Bank')
       for c in ('USD', 'GBP', 'JPY', 'CHF', 'CNY', 'INR', 'TRY', 'BRL', 'ZAR')},
    **{f'CB_GOLD_{c}': (imf_gold, c, W, f'{c} central-bank gold, million oz', 'IMF International Liquidity')
       for c in ('CHN', 'RUS', 'IND', 'TUR', 'POL', 'USA', 'DEU', 'JPN')},
    # debt and fiscal
    'DEBT': (treasury_debt, None, D, 'US federal debt (Debt to the Penny)', 'US Treasury, Fiscal Data'),
    'DEBT_GDP': (fred, 'GFDEGDQ188S', M, 'US federal debt, % of GDP', 'OMB / St. Louis Fed via FRED'),
    'INTEREST': (fred, 'A091RC1Q027SBEA', M, 'US federal interest payments (annual rate)', 'BEA via FRED'),
    'RECEIPTS': (fred, 'FGRECPT', M, 'US federal receipts (annual rate)', 'BEA via FRED'),
    **{f'Y10_{c}': (fred, f'IRLTLT01{c}M156N', M, f'{c} 10-year government bond yield', 'OECD via FRED')
       for c in ('DE', 'GB', 'JP', 'IT')},
    **{f'GOVDEBT_{c}': (imf_debt, c, W, f'{c} government debt, % of GDP', 'IMF World Economic Outlook')
       for c in ('USA', 'JPN', 'ITA', 'FRA', 'GBR', 'CHN', 'DEU', 'IND', 'CAN', 'BRA')},
    # more inflation measures, oil, housing, external debt
    'MCT': (nyfed_mct, None, M, 'Multivariate Core Trend inflation (NY Fed)', 'Federal Reserve Bank of New York'),
    'TRIM_CPI': (fred, 'TRMMEANCPIM158SFRBCLE', M, 'Trimmed-mean CPI, annualised (Cleveland Fed)', 'Federal Reserve Bank of Cleveland via FRED'),
    'TRIM_PCE': (fred, 'PCETRIM12M159SFRBDAL', M, 'Trimmed-mean PCE, 12 months (Dallas Fed)', 'Federal Reserve Bank of Dallas via FRED'),
    'EXP_INF1': (fred, 'EXPINF1YR', M, 'Expected inflation, 1 year (Cleveland Fed model)', 'Federal Reserve Bank of Cleveland via FRED'),
    'EXP_INF10': (fred, 'EXPINF10YR', M, 'Expected inflation, 10 years (Cleveland Fed model)', 'Federal Reserve Bank of Cleveland via FRED'),
    'OIL': (fred, 'DCOILWTICO', D, 'WTI crude oil, USD per barrel', 'EIA via FRED'),
    'OIL_M': (fred, 'WTISPLC', M, 'WTI crude oil, monthly (from 1946)', 'Federal Reserve Bank of St. Louis'),
    'HPI': (fred, 'USSTHPI', M, 'US house price index (FHFA)', 'FHFA via FRED'),
    'FED_TREASURIES': (fred, 'TREAST', D, 'Treasury securities held by the Fed', 'Federal Reserve via FRED'),
    'NIIP_A': (fred, 'IIPUSNETIA', W, 'US net international investment position (annual)', 'BEA via FRED'),
    **{f'EXTDEBT_{c}': (world_bank, ('DT.DOD.DECT.CD.AR.US', 22, c), W, f'{c} gross external debt, USD', 'World Bank / IMF QEDS (CC BY 4.0)')
       for c in WB_COUNTRIES},
    **{f'GDPUSD_{c}': (world_bank, ('NY.GDP.MKTP.CD', 2, c), W, f'{c} GDP, current USD', 'World Bank WDI (CC BY 4.0)')
       for c in WB_COUNTRIES},
    # valuation, leverage, liquidity (Fed Z.1 financial accounts, quarterly)
    'EQUITY_MV': (fred, 'NCBEILQ027S', M, 'US corporate equities, market value', 'Federal Reserve Z.1 via FRED'),
    'CORP_DEBT': (fred, 'BCNSDODNS', M, 'Nonfinancial corporate debt', 'Federal Reserve Z.1 via FRED'),
    'HH_DEBT': (fred, 'CMDEBT', M, 'Household debt', 'Federal Reserve Z.1 via FRED'),
    'DSR': (fred, 'TDSP', M, 'Household debt service, % of disposable income', 'Federal Reserve via FRED'),
    'RESERVES': (fred, 'WRESBAL', D, 'Bank reserves at the Fed', 'Federal Reserve via FRED'),
    'FED_DEBT_LEVEL': (fred, 'GFDEBTN', M, 'US federal debt, total', 'US Treasury via FRED'),
    # leading indicators (tested in v6)
    'CLAIMS': (fred, 'ICSA', D, 'Initial jobless claims (weekly)', 'US Department of Labor via FRED'),
    'PERMITS': (fred, 'PERMIT', M, 'Building permits', 'US Census Bureau via FRED'),
    'LENDING': (fred, 'DRTSCILM', M, 'Banks tightening C&I lending standards, net %', 'Federal Reserve SLOOS via FRED'),
    'VIX3M': (fred, 'VXVCLS', D, 'Cboe 3-month volatility index', 'Cboe via FRED'),
    'COPPER_M': (pink_sheet, 'Copper', W, 'Copper, USD per tonne (monthly average)', 'World Bank Pink Sheet (CC BY 4.0)'),
    # for why-gold.json
    'WAGES': (fred, 'AHETPI', M, 'Average hourly earnings, production & non-supervisory workers', 'BLS via FRED'),
    'CPI_1913': (fred, 'CPIAUCNS', M, 'US consumer prices since 1913 (not seasonally adjusted)', 'BLS via FRED'),
    # live venues (snapshots)
    'V_KRAKEN_PAXG': (kraken, 'PAXGUSD', LIVE, 'PAX Gold on Kraken', 'Kraken public API'),
    'V_KRAKEN_XAUT': (kraken, 'XAUTUSD', LIVE, 'Tether Gold on Kraken', 'Kraken public API'),
    'V_BITSTAMP_PAXG': (bitstamp, 'paxgusd', LIVE, 'PAX Gold on Bitstamp', 'Bitstamp public API'),
    'V_COINBASE_PAXG': (coinbase, 'PAXG-USD', LIVE, 'PAX Gold on Coinbase', 'Coinbase Exchange public API'),
    'V_KRAKEN_BTC': (kraken, 'XBTUSD', LIVE, 'Bitcoin on Kraken', 'Kraken public API'),
    'V_GOLD_NY_PREM': (gold_ny_premium, None, LIVE, 'Gold futures (COMEX-settled) over spot, minus carry, %', 'Coinbase public API'),
    # markets that should agree (the "Unusual right now" box)
    'SHFE_GOLD': (shfe_gold, None, D, 'Gold futures, Shanghai, most-traded contract (CNY/g; only the premium is shown)',
                  'Shanghai Futures Exchange (non-commercial use)'),
    'CP_FIN_3M': (fred, 'DCPF3M', D, '3-month AA financial commercial paper rate', 'Federal Reserve via FRED'),
    'CP_OLD': (fred, 'WCP3M', W, '3-month commercial paper rate, weekly (to 1997)', 'Federal Reserve via FRED'),
    'BIS_CREDIT': (fred, 'QUSPAM770A', M, 'Credit to the private non-financial sector, % of GDP', 'Bank for International Settlements via FRED'),
    'TBILL_3M': (fred, 'DTB3', D, '3-month Treasury bill rate (daily)', 'Federal Reserve via FRED'),
    'SOFR99': (fred, 'SOFR99', D, 'SOFR, 99th percentile of overnight repo trades', 'Federal Reserve Bank of New York via FRED'),
    'IORB': (fred, 'IORB', D, 'Interest on reserve balances', 'Federal Reserve via FRED'),
    'IOER': (fred, 'IOER', W, 'Interest on excess reserves (to July 2021)', 'Federal Reserve via FRED'),
    'SWAP_LINES': (fred, 'SWPT', D, 'Fed dollar swap lines with foreign central banks', 'Federal Reserve via FRED'),
    # the long run (outside the score): Shiller's data for the 10-year outlook, and ratios beyond history
    **{f'SH_{k}': (shiller, c, W, f'{name}, monthly (Shiller)', 'Robert J. Shiller, shillerdata.com')
       for k, c, name in (('P', 1, 'S&P composite price'), ('D', 2, 'S&P composite dividends'), ('CPI', 4, 'US consumer prices'),
                          ('GS10', 6, '10-year Treasury yield'), ('CAPE', 12, 'Cyclically adjusted P/E'))},
    'HH_EQ_SHARE': (fred, 'BOGZ1FL153064486Q', M, 'Households: stocks as a share of financial assets', 'Federal Reserve Z.1 via FRED'),
    'HH_NW': (fred, 'TNWBSHNO', M, 'Household net worth', 'Federal Reserve Z.1 via FRED'),
    'DPI': (fred, 'DPI', M, 'Disposable personal income', 'BEA via FRED'),
    'PROFITS': (fred, 'CP', M, 'Corporate profits after tax', 'BEA via FRED'),
    'TECH_INV': (fred, 'A679RC1Q027SBEA', M, 'Business investment in computers, software and data equipment', 'BEA via FRED'),
}
INCREMENTAL = {bcra, nbp_gold, shfe_gold}  # fetchers that take the last stored date and only fetch what's new


def connect(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS obs (series TEXT, d TEXT, v REAL, PRIMARY KEY (series, d)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS meta (series TEXT PRIMARY KEY, fetched REAL, ok INTEGER, msg TEXT);
    ''')
    return db


def refresh(db, only=None, force=False):
    meta = {r[0]: r[1:] for r in db.execute('SELECT series, fetched, ok FROM meta')}
    for key, (fn, arg, ttl, *_ ) in SERIES.items():
        if only and key not in only:
            continue
        fetched, ok = meta.get(key, (0, 0))
        # a failed fetch retries after a quarter of its TTL, a good one after the full TTL
        if not force and time.time() - fetched < ttl * 3600 * (1 if ok else 0.25):
            continue
        try:
            if fn in INCREMENTAL:
                have = db.execute('SELECT max(d) FROM obs WHERE series=?', (key,)).fetchone()[0]
                rows = fn(arg, have)
            else:
                rows = fn(arg)
            if not rows:
                raise ValueError('no data')
            db.executemany('INSERT OR REPLACE INTO obs VALUES (?,?,?)', [(key, d, v) for d, v in rows])
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?,1,?)', (key, time.time(), f'{len(rows)} rows'))
        except Exception as e:  # one broken source must not stop the others
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?,0,?)', (key, time.time(), str(e)[:200]))
            print(f'! {key}: {e}', file=sys.stderr)
        db.commit()


def summary(r):
    """The hero's one-paragraph reading of a report."""
    p, sr, od, high = r['pillars'], r['structural_rank'], r['odds'], r['flags']['high']
    mood = 'calm' if p['stress'] < 40 else 'uneasy' if p['stress'] < 70 else 'in distress'
    base = 'weak' if sr['pct'] >= 70 else 'middling' if sr['pct'] >= 40 else 'solid'
    flags = (f"{len(high)} red flag{'s' * (len(high) != 1)} led by “{high[0]['label']}” at {high[0]['shown']}"
             if high else 'no red flags')
    above = [k for k in ('crisis_24m', 'recession_24m', 'bear_24m') if od[k]['like'] is not None and od[k]['like'] - od[k]['base'] >= 10]
    c, s = od['crisis_24m'], od['severe_24m']
    horizon = (f"Over two years the odds are above average: {c['like']}% of months like this saw a crisis within 24 months "
               f"(average {c['base']}%), mostly milder ones; {s['like']}% a severe one (average {s['base']}%)."
               if above else 'Over two years the odds are about average.')
    return (f"Score {r['score']:.0f}/100 ({r['band']['label'].lower()}). Markets are {mood} (stress {p['stress']:.0f}) and "
            f"recession risk over the next 12 months is {r['forecast']['probability']:.0f}%. The foundations are {base}: "
            f"structural risk is {r['structural']:.0f}/100, higher than in {sr['pct']}% of months since 1973, with {flags}. {horizon}")


def fill(html, el_id, text='', attrs={}, raw=None):
    """Set the content (text, or raw HTML without nested tags of the same name) and some attributes of the element with
    this id in a built page. Runs every 15 minutes on the same page, so it replaces rather than adds."""
    def sub(m):
        tag = m[1]
        for k, v in attrs.items():
            new = f' {k}="{escape(v)}"'
            tag, n = re.subn(rf'\s{k}="[^"]*"', lambda _: new, tag)
            if not n:
                tag = tag[:-1] + new + '>'
        return tag + (raw if raw is not None else escape(text, quote=False)) + m[3]
    html, n = re.subn(rf'(<([a-z]+)[^>]*\sid="{el_id}"[^>]*>).*?(</\2>)', sub, html, count=1, flags=re.S)
    if not n:
        raise ValueError(f'#{el_id} not found in the page')
    return html


def snapshot(page, r):
    """Write the live reading into the built page, so crawlers and the first paint see it before the script runs."""
    if not os.path.exists(page):  # ponytail: only a deployed site has the built page next to live/
        return
    with open(page) as f:
        html = f.read()
    answer = r['band']['answer'] + ('' if r['band']['answer'].endswith('.') else '.')
    at = datetime.fromisoformat(r['updated'].replace('Z', '+00:00'))
    html = fill(html, 'answer', answer, {'data-band': r['band']['key'], 'style': f'--len:{len(answer)}'})
    html = fill(html, 'summary', r.get('summary') or summary(r))
    html = fill(html, 'score', f"{r['score']:.1f}")
    html = fill(html, 'band-label', r['band']['label'])
    html = fill(html, 'pill-text', f"{r['score']:.0f} · {r['band']['label']}")
    html = fill(html, 'fc-prob', f"{r['forecast']['probability']:.0f}%")
    html = fill(html, 'ro-rec', f"{r['forecast']['probability']:.0f}%")
    for el_id, k in (('ro-acute', 'acute'), ('ro-structural', 'structural'), ('ro-stag', 'stagflation')):
        html = fill(html, el_id, f'{r[k]:.0f}')
    sr = r['structural_rank']
    html = fill(html, 'ro-record', f"record {sr['max']:.0f}, {datetime.strptime(sr['max_month'], '%Y-%m'):%b %Y}")
    high = r['flags']['high']  # the same chips the page script draws
    html = fill(html, 'flag-strip', raw=''.join(
        f'<a class="fchip" href="#flags-s"><i></i>{escape(f["label"])} <b>{escape(f["shown"])}</b></a>' for f in high[:5]) +
        (f'<a class="fchip more" href="#flags-s">+{len(high) - 5} more</a>' if len(high) > 5 else ''))
    kind = {'record': 'Record', 'move': 'Sudden move', 'gap': 'Markets apart'}  # the same list the page script draws
    html = fill(html, 'unusual-list', raw=''.join(
        f'<li><i>{kind[u["kind"]]}</i><span>{escape(u["label"])} <b>{escape(u["shown"])}</b></span><em>{escape(u["note"])}</em></li>'
        for u in r.get('unusual', [])) or '<li class="none">Nothing out of the ordinary: no records, sudden moves or markets pulling apart.</li>')
    # short enough for one line on a phone, like the script's "updated 5 min ago"; the date is in datetime and the JSON-LD
    html = fill(html, 'updated', f"updated {at:%H:%M} UTC", {'datetime': r['updated']})
    html = re.sub(r'"dateModified":"[^"]*"', f'"dateModified":"{r["updated"]}"', html)
    tmp = page + '.tmp'
    with open(tmp, 'w') as f:
        f.write(html)
    os.replace(tmp, page)


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import score

    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.path.join(os.path.dirname(__file__), 'data', 'collapse.db'))
    ap.add_argument('--out', help='directory for collapse.json (the page polls it)')
    ap.add_argument('--only', nargs='*', help='refresh only these series')
    ap.add_argument('--force', action='store_true', help='ignore TTLs')
    ap.add_argument('--snapshot', action='store_true', help='only write the last reading in --out into ../index.html (deploys)')
    a = ap.parse_args()
    if a.snapshot:
        with open(os.path.join(os.path.expanduser(a.out), 'collapse.json')) as f:
            snapshot(os.path.join(os.path.expanduser(a.out), '..', 'index.html'), json.load(f))
        sys.exit()
    db = connect(os.path.expanduser(a.db))
    refresh(db, a.only, a.force)
    # live venue snapshots are only shown as "now"; keep 30 days
    db.execute("DELETE FROM obs WHERE series LIKE 'V!_%' ESCAPE '!' AND d < ?",
               ((datetime.now(timezone.utc) - timedelta(days=30)).strftime('%Y-%m-%d'),))
    db.commit()
    if a.out:
        out = os.path.expanduser(a.out)
        os.makedirs(out, exist_ok=True)
        tmp = os.path.join(out, '.collapse.json.tmp')
        rep = score.report(db, SERIES)
        rep['summary'] = summary(rep)
        with open(tmp, 'w') as f:
            json.dump(rep, f, separators=(',', ':'))
        os.replace(tmp, os.path.join(out, 'collapse.json'))  # atomic: the page never reads a half-written file
        with open(tmp, 'w') as f:
            json.dump(score.why_gold(db), f, separators=(',', ':'))
        os.replace(tmp, os.path.join(out, 'why-gold.json'))
        snapshot(os.path.join(out, '..', 'index.html'), rep)
