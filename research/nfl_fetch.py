"""NFL 2025 season, for in-game edge tests (Oct 17, 2026). Market data + public scores only.
1) International Polymarket: every cfb game event, moneyline + full-game totals, price history (2-min) from 30 min
   before kickoff to 5 hours after.  2) ESPN: every FBS game's scoring plays with real wall-clock times.
Output: research/nfl_pm_2025.json.gz, research/nfl_espn_2025.json.gz"""
import gzip, json, os, time, urllib.request, datetime as dt
from concurrent.futures import ThreadPoolExecutor
OUT = 'research'; LOG = open(os.path.join(OUT, 'nfl_fetch_log.txt'), 'w')
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
        try: return json.loads(x)
        except Exception: return [x]
    return x or []

events = {}
for tag in ('nfl',):
    off = 0
    while off < 10000:
        try:
            page = get(f'https://gamma-api.polymarket.com/events?tag_slug={tag}&closed=true&limit=100&offset={off}'
                       f'&start_date_min=2025-09-01T00:00:00Z&start_date_max=2026-02-15T00:00:00Z')
        except Exception as e:
            log('listing failed', tag, off, repr(e)); break
        if not page: break
        for e in page:
            s = e.get('slug', '')
            if s.startswith('nfl-') and s.count('-') >= 5:
                events[s] = e
        off += 100
    log('tag', tag, 'events so far', len(events))

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
            try: start = dt.datetime.fromisoformat(st.replace('Z', '+00:00').replace(' ', 'T')); break
            except Exception: pass
    if start is None: return None
    if start.tzinfo is None: start = start.replace(tzinfo=dt.timezone.utc)
    a, b = int(start.timestamp()) - 30 * 60, int(start.timestamp()) + 5 * 3600
    keep = []
    for m in mk:
        typ = (m.get('sportsMarketType') or '').lower(); q = m.get('question') or ''
        is_ml = typ == 'moneyline' or (' vs' in q and 'Spread' not in q and 'O/U' not in q)
        is_tot = typ == 'totals' or ('O/U' in q and '1H' not in q and 'Half' not in q)
        if not (is_ml or is_tot) or any(k in q.lower() for k in ('1h', 'half', 'quarter', 'player')):
            continue
        toks = jl(m.get('clobTokenIds'))
        if not toks: continue
        try:
            h = get(f'https://clob.polymarket.com/prices-history?market={toks[0]}&startTs={a}&endTs={b}&fidelity=2').get('history', [])
        except Exception as ex:
            log(s, 'history failed', q, repr(ex)); continue
        keep.append(dict(q=q, kind='ml' if is_ml else 'total', line=m.get('line'), outcomes=jl(m.get('outcomes')),
                         final=jl(m.get('outcomePrices')), hist=[[x['t'], x['p']] for x in h]))
    return dict(slug=s, title=e.get('title'), start=start.isoformat(), markets=keep)

res = []
with ThreadPoolExecutor(8) as ex:
    for n, r in enumerate(ex.map(one, sorted(events.items()))):
        if r and r['markets']: res.append(r)
        if n % 100 == 0:
            log(n, 'events done', len(res))
json.dump(res, gzip.open(os.path.join(OUT, 'nfl_pm_2025.json.gz'), 'wt'))
log('PM DONE', len(res), 'games')

# ---- ESPN scoring plays with wall clock
dates = sorted({r['start'][:10] for r in res} | {(dt.date.fromisoformat(r['start'][:10]) - dt.timedelta(days=1)).isoformat() for r in res})
ids = {}
for d in dates:
    try:
        sb = get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={d.replace('-', '')}&limit=300")
    except Exception as e:
        log('scoreboard failed', d, repr(e)); continue
    for ev in sb.get('events', []):
        ids[ev['id']] = ev
log('espn games', len(ids))
def game(item):
    gid, ev = item
    try:
        sm = get(f'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={gid}')
    except Exception:
        return None
    c = ev['competitions'][0]; tm = {x['homeAway']: x for x in c['competitors']}
    sc = []
    for dr in (sm.get('drives') or {}).get('previous', []):
        for p in dr.get('plays', []):
            if p.get('scoringPlay'):
                sc.append([p.get('wallclock'), (p.get('period') or {}).get('number'), (p.get('clock') or {}).get('displayValue'),
                           p.get('awayScore'), p.get('homeScore')])
    T = lambda x: dict(abbr=x['team'].get('abbreviation'), name=x['team'].get('displayName'), short=x['team'].get('shortDisplayName'),
                       nick=x['team'].get('name'), loc=x['team'].get('location'), score=x.get('score'))
    return dict(id=gid, date=ev['date'], away=T(tm['away']), home=T(tm['home']), scoring=sc)
out = []
with ThreadPoolExecutor(8) as ex:
    for r in ex.map(game, ids.items()):
        if r: out.append(r)
json.dump(out, gzip.open(os.path.join(OUT, 'nfl_espn_2025.json.gz'), 'wt'))
log('ESPN DONE', len(out), 'games;', sum(1 for g in out if g['scoring'] and g['scoring'][0][0]), 'with wall clock')
