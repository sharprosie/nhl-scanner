"""NHL 2025-26 public Polymarket moneyline price history, EARLY window (30 min before start to 100 min after), 1-minute.
For the early-deficit comeback test (Oct 17, 2026). Market data only. Output: research/pm_nhl_early_2025.json.gz"""
import csv, gzip, json, os, time, urllib.request, urllib.parse, datetime as dt
from concurrent.futures import ThreadPoolExecutor

OUT = 'research'; os.makedirs(OUT, exist_ok=True)
LOG = open(os.path.join(OUT, 'pm_early_log.txt'), 'w')
def log(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); LOG.write(s + '\n'); LOG.flush()

def get(u):
    for i in range(3):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read())
        except Exception as e:
            err = e; time.sleep(2 + 3 * i)
    raise err

def jl(x):
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return [x]
    return x or []

# ---- 1. find NFL events (tag listing), 2025-26 season
events = {}
for closed in ('true',):
    off = 0
    while off < 6000:
        try:
            page = get(f'https://gamma-api.polymarket.com/events?tag_slug=nhl&closed={closed}&limit=100&offset={off}'
                       f'&start_date_min=2025-10-01T00:00:00Z&start_date_max=2026-04-20T00:00:00Z')
        except Exception as e:
            log('listing failed', off, repr(e)); break
        if not page:
            break
        for e in page:
            s = e.get('slug', '')
            if s.startswith('nhl-') and s.count('-') >= 5:
                events[s] = e
        off += 100
log('events from tag listing:', len(events))

# ---- 3. price history for each game market (second half), 8 games at a time, saving every 40 games
def one(item):
    s, e = item
    try:
        mk = e.get('markets') or get(f'https://gamma-api.polymarket.com/events?slug={s}')[0].get('markets', [])
    except Exception as ex:
        log(s, 'markets failed', repr(ex)); return None
    start = None
    for m in mk:
        st = m.get('gameStartTime') or e.get('startTime') or e.get('startDate')
        if st:
            try:
                start = dt.datetime.fromisoformat(st.replace('Z', '+00:00').replace(' ', 'T')); break
            except Exception:
                pass
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    a, b = int(start.timestamp()) - 30 * 60, int(start.timestamp()) + 100 * 60
    keep = []
    for m in mk:
        typ = (m.get('sportsMarketType') or '').lower(); q = m.get('question') or ''
        if not (typ == 'moneyline' or (' vs' in q and 'Spread' not in q)):
            continue
        if any(k in q.lower() for k in ('1p', 'period', 'o/u', 'total', 'player', 'goal')):
            continue
        toks = jl(m.get('clobTokenIds'))
        if not toks:
            continue
        try:
            h = get(f'https://clob.polymarket.com/prices-history?market={toks[0]}&startTs={a}&endTs={b}&fidelity=1').get('history', [])
        except Exception as ex:
            log(s, 'history failed', q, repr(ex)); continue
        keep.append(dict(q=q, type=typ, line=m.get('line'), outcomes=jl(m.get('outcomes')), prices=jl(m.get('outcomePrices')),
                         hist=[[x['t'], x['p']] for x in h]))
    return dict(slug=s, start=start.isoformat(), markets=keep)

res = []
items = sorted(events.items())
with ThreadPoolExecutor(8) as ex:
    for n, r in enumerate(ex.map(one, items)):
        if r: res.append(r)
        if n % 40 == 0:
            json.dump(res, gzip.open(os.path.join(OUT, 'pm_nhl_early_2025.json.gz'), 'wt'))
            log(n, 'games done', len(res), 'markets', sum(len(x['markets']) for x in res))
json.dump(res, gzip.open(os.path.join(OUT, 'pm_nhl_early_2025.json.gz'), 'wt'))
log('DONE games', len(res), 'markets', sum(len(r['markets']) for r in res),
    'with history', sum(1 for r in res for m in r['markets'] if m['hist']))

