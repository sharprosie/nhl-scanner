"""Price recorder for in-game research (no alerts, no bets): NFL, college football, NHL, NBA.
Every ~5 seconds during live games (slower when many games are live), saves the REAL buy (ask) and sell (bid) prices on Polymarket US for each
full-game moneyline, spread and total, plus the game state from ESPN. To keep files small, a market's row is
written only when its bid or ask changed (or the score/period changed).
Output: out/rec_<sport>_<date>.csv.gz"""
import csv, gzip, json, os, re, sys, time, urllib.parse, urllib.request, datetime as dt
try:
    import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); import quiethours  # 10 PM-7 AM pings are silent
except Exception:
    pass
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, 'out'); os.makedirs(OUT, exist_ok=True)
ET, PT = ZoneInfo('America/New_York'), ZoneInfo('America/Los_Angeles')
POLL = 5          # seconds between checks; slows down automatically on big slates (stay under ~10 requests/second)
SPORTS = {   # name: (ESPN path, extra query, Polymarket slug prefix)
    'nfl': ('football/nfl', '', 'nfl'),
    'cfb': ('football/college-football', ('&groups=80', '&groups=81'), 'cfb'),   # FBS + FCS: every college game Polymarket might list
    'nhl': ('hockey/nhl', '', 'nhl'),
    'nba': ('basketball/nba', '', 'nba'),
}
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/{p}/scoreboard?dates={d}&limit=300{x}&nocache={n}'
EVENT = 'https://gateway.polymarket.us/v1/events/slug/{s}'
SEARCH = 'https://gamma-api.polymarket.com/public-search?q={q}&events_status=active&limit_per_type=8'
COLS = ['ts', 'game', 'period', 'clock', 'away_score', 'home_score', 'possession', 'situation',
        'slug', 'type', 'line', 'long_side', 'bid', 'ask']


def get(u, timeout=20):
    r = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def note(msg):
    print(msg, flush=True)
    with open(os.path.join(OUT, 'rec_log.txt'), 'a') as f:
        f.write(f"{dt.datetime.now(PT).isoformat(timespec='seconds')} {msg}\n")


def games(sport, now, ahead=0):
    path, extra, _ = SPORTS[sport]; seen = {}
    base = now.astimezone(ET).date()
    for d in {base, base - dt.timedelta(days=1)} | {base + dt.timedelta(days=k) for k in range(1, ahead + 1)}:
      for x in (extra if isinstance(extra, tuple) else (extra,)):
        try:
            sb = get(ESPN.format(p=path, d=d.strftime('%Y%m%d'), x=x, n=int(time.time())))
        except Exception as e:
            note(f'{sport} scoreboard error {e}'); continue
        for ev in sb.get('events', []):
            c = ev['competitions'][0]; tm = {x['homeAway']: x for x in c['competitors']}
            if 'away' not in tm or 'home' not in tm:
                continue
            st = ev['status']; sit = c.get('situation') or {}
            poss = next((x['team']['abbreviation'] for x in c['competitors']
                         if sit.get('possession') and x['team']['id'] == sit.get('possession')), '')
            seen[ev['id']] = dict(
                id=ev['id'], sport=sport, a=tm['away']['team']['abbreviation'], h=tm['home']['team']['abbreviation'],
                an=tm['away']['team'].get('shortDisplayName') or tm['away']['team'].get('displayName', ''),
                hn=tm['home']['team'].get('shortDisplayName') or tm['home']['team'].get('displayName', ''),
                start=dt.datetime.fromisoformat(ev['date'].replace('Z', '+00:00')), state=st['type']['state'],
                period=st.get('period'), clock=st.get('displayClock'), sa=tm['away'].get('score'), sh=tm['home'].get('score'),
                poss=poss, sit=(sit.get('shortDownDistanceText', '') + ' ' + str(sit.get('yardLine', ''))).strip())
    return seen


SLUG = {}
def event(g):
    k = (g['sport'], g['a'], g['h'], g['start'].date())
    if SLUG.get(k):
        try:
            return get(EVENT.format(s=SLUG[k]))
        except Exception:
            pass
    if k in SLUG and SLUG[k] is None and time.time() % 600 > 30:      # not found before: retry about every 10 min
        return None
    pre = SPORTS[g['sport']][2]
    days = {g['start'].astimezone(ET).date(), g['start'].astimezone(PT).date(), g['start'].date()}
    for d in days:
        s = f"{pre}-{g['a'].lower()}-{g['h'].lower()}-{d.isoformat()}"
        try:
            ev = get(EVENT.format(s=s)); SLUG[k] = s; note(f"{g['a']}@{g['h']}: {s}"); return ev
        except Exception:
            pass
    try:                                                                # names differ (college, some NBA codes): search
        r = get(SEARCH.format(q=urllib.parse.quote(f"{g['an']} {g['hn']}")))
        for e in r.get('events', []):
            s = e.get('slug', '')
            if s.startswith(pre + '-') and any(s.endswith(d.isoformat()) for d in days):
                ev = get(EVENT.format(s=s)); SLUG[k] = s; note(f"{g['a']}@{g['h']}: {s} (search)"); return ev
    except Exception:
        pass
    SLUG[k] = None
    return None


LAST = {}
def rows(g, ev, ts):
    ev = ev.get('event', ev); out = []
    q = lambda m, k: (m.get(k) or {}).get('value') if isinstance(m.get(k), dict) else m.get(k)
    state = (g['sa'], g['sh'], g['period'])
    for m in ev.get('markets', []):
        t = m.get('sportsMarketType') or ''
        if 'full_game' not in t or not any(x in t for x in ('winner', 'spread', 'total')) or 'team_points' in t:
            continue
        bid, ask = q(m, 'bestBidQuote'), q(m, 'bestAskQuote'); key = m.get('slug')
        if LAST.get(key) == (bid, ask, state):
            continue
        LAST[key] = (bid, ask, state)
        long = next((s.get('description') for s in m.get('marketSides', []) if s.get('long')), '')
        out.append([ts, f"{g['a']}@{g['h']}", g['period'], g['clock'], g['sa'], g['sh'], g['poss'], g['sit'],
                    key, re.sub(r'^.*full_game_', '', t), m.get('line'), long, bid, ask])
    return out


def ping(title, msg, prio='default'):
    t = os.environ.get('NTFY_TOPIC', '').strip()
    print('[ping]', title, msg, flush=True)
    if not t:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{t}', data=msg.encode(),
                               headers={'Title': title, 'Priority': prio, 'Tags': 'movie_camera'}), timeout=20)
    except Exception as e:
        note(f'ping failed {e}')


HOT, _hot_t = {}, [0.0]
def hot_check(force=False):
    """FrankenClaude: re-check games that are in a buy spot about every 2 seconds (fresh ESPN state + fresh Polymarket prices)."""
    if not HOT or (not force and time.time() - _hot_t[0] < 2):
        return
    _hot_t[0] = time.time()
    try:
        import frankenclaude
        now = dt.datetime.now(dt.timezone.utc)
        for sp in {g['sport'] for g in HOT.values()}:
            G = games(sp, now)
            for gid in [k for k, g in HOT.items() if g['sport'] == sp]:
                g = G.get(gid)
                ev = event(g) if g else None
                if not g or ev is None or not frankenclaude.consider(g, ev):
                    HOT.pop(gid, None)
    except Exception:
        pass


def main(max_hours=5.8, probe=False, sports=None):
    sports = sports or list(SPORTS)
    if not probe:
        try:
            import frankenclaude; frankenclaude.cleanup()          # leftovers from a run that died
        except Exception:
            pass
    STATS = {sp: dict(live=set(), matched=set(), rows=0) for sp in sports}
    end = time.time() + max_hours * 3600
    while time.time() < end:
        now = dt.datetime.now(dt.timezone.utc); live, upcoming = [], []
        for sp in sports:
            G = games(sp, now, ahead=2 if probe else 0)
            for g in G.values():
                if g['state'] == 'in' or (probe and g['state'] == 'pre'):
                    live.append(g)
                elif g['state'] == 'pre':
                    upcoming.append(g['start'])
        if probe:
            live = sorted(live, key=lambda g: g['start'])
            live = [next((g for g in live if g['sport'] == sp), None) for sp in sports]
            live = [g for g in live if g]
        if not live:
            nxt = min(upcoming) if upcoming else None
            if not nxt or nxt - now > dt.timedelta(hours=3):
                note(f'no live games; next start {nxt}; stopping'); break
            time.sleep(60); continue
        ts = now.isoformat(timespec='seconds'); counts = {}
        for g in live:
            gid = (g['a'], g['h']); STATS[g['sport']]['live'].add(gid)
            try:
                ev = event(g)
                if ev is None:
                    continue
                STATS[g['sport']]['matched'].add(gid)
                try:
                    import fbunder; fbunder.consider(g, ev)
                except Exception:
                    pass
                try:
                    import frankenclaude
                    if frankenclaude.consider(g, ev):
                        HOT[g['id']] = g
                    else:
                        HOT.pop(g['id'], None)
                except Exception:
                    pass
                hot_check()
                r = rows(g, ev, ts); STATS[g['sport']]['rows'] += len(r)
                path = os.path.join(OUT, f"rec_{g['sport']}_{dt.datetime.now(PT).strftime('%Y-%m-%d')}.csv.gz")
                new = not os.path.exists(path)
                with gzip.open(path, 'at', newline='') as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(COLS)
                    w.writerows(r)
                counts[g['sport']] = counts.get(g['sport'], 0) + len(r)
            except Exception as e:
                note(f"{g['a']}@{g['h']}: error {e}")
        if probe:
            note(f'PROBE rows by sport: {counts}; games: {[g["sport"] + " " + g["a"] + "@" + g["h"] for g in live]}'); break
        pause = time.time() + max(POLL, len(live) / 10.0)
        while time.time() < pause:
            hot_check(force=True) if HOT else None
            time.sleep(2 if HOT else max(0.5, pause - time.time()))
    if not probe:
        try:
            import frankenclaude; frankenclaude.cleanup()          # never leave resting orders behind
        except Exception:
            pass
    note('finished')
    seen = {sp: v for sp, v in STATS.items() if v['live']}
    if seen and not probe:
        broken = [sp.upper() for sp, v in seen.items() if not v['matched'] or v['rows'] == 0]
        total = sum(len(v['matched']) for v in seen.values())
        if broken:
            ping('Recorder problem', f"Nothing recorded for {', '.join(broken)}.", 'high')
        else:
            ping('Recorder OK', f'{total} games recorded.', 'low')


if __name__ == '__main__':
    main(probe='--probe' in sys.argv)
