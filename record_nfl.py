"""Test 2 recorder (no alerts, no bets): during NFL games, every ~20 seconds save the REAL buy (ask) and sell (bid)
prices on Polymarket US for every full-game market (moneyline, all spreads, all totals) plus the game state from ESPN.
Purpose: measure whether spread/total markets lag the moneyline at prices you could actually trade.
Output: out/nfl_rec_<date>.csv.gz (one row per market per check)."""
import csv, gzip, json, os, sys, time, datetime as dt, urllib.request
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, 'out'); os.makedirs(OUT, exist_ok=True)
ET, PT = ZoneInfo('America/New_York'), ZoneInfo('America/Los_Angeles')
POLL = 20
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={d}&limit=50&nocache={n}'
EVENT = 'https://gateway.polymarket.us/v1/events/slug/{s}'
COLS = ['ts', 'game', 'period', 'clock', 'away_score', 'home_score', 'possession', 'down_dist', 'yardline',
        'slug', 'type', 'line', 'long_side', 'bid', 'ask']


def get(u, timeout=20):
    r = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def note(msg):
    print(msg, flush=True)
    with open(os.path.join(OUT, 'nfl_rec_log.txt'), 'a') as f:
        f.write(f"{dt.datetime.now(PT).isoformat(timespec='seconds')} {msg}\n")


def games(now, ahead=0):
    seen = {}
    days = {now.astimezone(ET).date(), (now.astimezone(ET) - dt.timedelta(days=1)).date()}
    days |= {(now.astimezone(ET) + dt.timedelta(days=k)).date() for k in range(1, ahead + 1)}
    for d in days:
        try:
            sb = get(ESPN.format(d=d.strftime('%Y%m%d'), n=int(time.time())))
        except Exception as e:
            note(f'scoreboard error {e}'); continue
        for ev in sb.get('events', []):
            c = ev['competitions'][0]; tm = {x['homeAway']: x for x in c['competitors']}
            a, h = tm['away']['team']['abbreviation'], tm['home']['team']['abbreviation']
            start = dt.datetime.fromisoformat(ev['date'].replace('Z', '+00:00'))
            st = ev['status']; sit = c.get('situation') or {}
            poss = ''
            for x in c['competitors']:
                if sit.get('possession') and x['team']['id'] == sit.get('possession'):
                    poss = x['team']['abbreviation']
            seen[f'{a}@{h}'] = dict(a=a, h=h, start=start, state=st['type']['state'], period=st.get('period'),
                                    clock=st.get('displayClock'), sa=tm['away'].get('score'), sh=tm['home'].get('score'),
                                    poss=poss, dd=sit.get('shortDownDistanceText', ''), yl=sit.get('yardLine', ''))
    return seen


SLUG = {}
def event(g):
    k = f"{g['a']}@{g['h']}"
    if k in SLUG:
        try:
            return get(EVENT.format(s=SLUG[k]))
        except Exception:
            pass
    for d in {g['start'].astimezone(ET).date(), g['start'].astimezone(PT).date(), g['start'].date()}:
        s = f"nfl-{g['a'].lower()}-{g['h'].lower()}-{d.isoformat()}"
        try:
            ev = get(EVENT.format(s=s)); SLUG[k] = s; note(f'{k}: Polymarket event {s}'); return ev
        except Exception:
            continue
    return None


def rows(g, ev, ts):
    ev = ev.get('event', ev); out = []
    q = lambda m, k: (m.get(k) or {}).get('value') if isinstance(m.get(k), dict) else m.get(k)
    for m in ev.get('markets', []):
        t = m.get('sportsMarketType') or ''
        if not any(x in t for x in ('full_game_winner', 'full_game_spread', 'full_game_total')) or 'team_points' in t:
            continue
        long = next((s.get('description') for s in m.get('marketSides', []) if s.get('long')), '')
        out.append([ts, f"{g['a']}@{g['h']}", g['period'], g['clock'], g['sa'], g['sh'], g['poss'], g['dd'], g['yl'],
                    m.get('slug'), t.replace('football_team_full_game_', ''), m.get('line'), long,
                    q(m, 'bestBidQuote'), q(m, 'bestAskQuote')])
    return out


def main(max_hours=5.8, probe=False):
    end = time.time() + max_hours * 3600
    path = os.path.join(OUT, f"nfl_rec_{dt.datetime.now(PT).strftime('%Y-%m-%d')}.csv.gz")
    new = not os.path.exists(path)
    while time.time() < end:
        now = dt.datetime.now(dt.timezone.utc); G = games(now, ahead=2 if probe else 0)
        live = {k: g for k, g in G.items() if g['state'] == 'in'}
        soon = [g for g in G.values() if g['state'] == 'pre' and g['start'] - now < dt.timedelta(minutes=15)]
        if probe:
            live = {k: g for k, g in sorted(G.items(), key=lambda x: x[1]['start']) if g['state'] != 'post'}
            live = dict(list(live.items())[:2])
        if not live and not soon:
            later = [g['start'] for g in G.values() if g['state'] == 'pre']
            if not later or min(later) - now > dt.timedelta(hours=3):
                note(f'no live games; next start {min(later) if later else None}; stopping'); break
            time.sleep(60); continue
        ts = now.isoformat(timespec='seconds'); n = 0
        with gzip.open(path, 'at', newline='') as f:
            w = csv.writer(f)
            if new:
                w.writerow(COLS); new = False
            for k, g in live.items():
                try:
                    ev = event(g)
                    if ev is None:
                        note(f'{k}: no Polymarket event found'); continue
                    r = rows(g, ev, ts); w.writerows(r); n += len(r)
                except Exception as e:
                    note(f'{k}: error {e}')
        if probe:
            note(f'PROBE recorded {n} market rows for {list(live)}'); break
        time.sleep(POLL)
    note('finished')


if __name__ == '__main__':
    main(probe='--probe' in sys.argv)
