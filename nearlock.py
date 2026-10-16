"""NHL Near-Lock & Cash-Out Scanner (v1.0) — runs continuously on game nights.

Watches EVERY live NHL game in the 3rd period about every 20 seconds and pushes to your phone when:
  1. NEAR-LOCK BUY: a side that is almost certain to win (real chance 85%+) is selling 5c+ below its real chance
     on Polymarket US (moneyline, puck lines and totals). Example 13 Oct: Dallas down 1 with 8:29 left; "No" on
     Dallas -1.5 was worth ~98.7c and the app showed 82c.
  2. CASH OUT: one of today's card bets can be sold for 4c+ MORE than its real chance (only for shaky positions,
     real chance under 92%). Example 13 Oct: Utah -1.5 up 5-3 with ~12 min left was worth ~76c; the cash-out
     paid like 87c.

Real chances come from a minute-by-minute scoring model of the rest of the game, calibrated to how often NHL
leads hold after two periods (up 1: 77%, up 2: 92%, up 3: 98%) and including empty-net goals when the
trailing team pulls its goalie (down 1 in the last 2:00, down 2 in the last 3:00).

Skipped on purpose: power plays (the model assumes even strength), overtime, 1st/2nd periods.
Feeds: NHL scoreboard (api-web.nhle.com) and Polymarket US (gateway.polymarket.us). Alerts: ntfy.
"""
import csv, json, os, sys, time, datetime as dt, urllib.request
try:
    import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); import quiethours  # 10 PM-7 AM pings are silent
except Exception:
    pass
from functools import lru_cache
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
OUT = os.path.join(HERE, 'out')   # logs (public: prices and alerts only, no personal bets)
PT = ZoneInfo('America/Los_Angeles')

EDGE = float(os.environ.get('NL_EDGE', 0.05))         # near-lock: buy only if 5c+ under the real chance
MIN_FAIR = float(os.environ.get('NL_MIN_FAIR', 0.85))  # ...and the side is a near-lock (85%+)
MAX_COST = 0.97                                         # never pay more than 97c
SELL_EDGE = 0.04                                        # cash-out: sell only if paid 4c+ over the real chance
SELL_MAX_FAIR = 0.92                                    # ...and only shaky positions (real chance under 92%)
STAKE = os.environ.get('NL_STAKE', '$5')                # test stake while the first alerts are graded
MODE = os.environ.get('NL_MODE', 'LIVE')                # LIVE = alerts say buy; PAPER = log only
POLL_LIVE, POLL_IDLE = 20, 90                           # seconds between scans (3rd period live / otherwise)
ONLY = os.environ.get('NL_ONLY', 'buy')                # 'buy' (near-locks), 'sell' (cash-outs) or 'both'

# ---------------------------------------------------------------- the late-game model ---
BASE = 2.6 / 60          # goals per team per minute at even strength late in games (calibrated)
EN_LEADER = 0.26         # leader's empty-net goals per minute while the other goalie is pulled
SIX_ON_FIVE = 0.10       # trailer's goals per minute with the extra attacker
STEP = 5                 # seconds per step
MAXD, MAXK = 10, 12


@lru_cache(maxsize=4096)
def outcome(d0, secs):
    """Distribution of (final home margin, goals still to come) given current home margin d0 and seconds left.
    Ties after regulation go to overtime/shootout: a coin flip that ends +1/-1 and adds one goal."""
    P = {(d0, 0): 1.0}
    for i in range(int(secs) // STEP):
        t = secs - i * STEP
        Q = {}
        for (d, k), p in P.items():
            if p < 1e-12:
                continue
            ad = abs(d)
            pull = (ad == 1 and t <= 120) or (ad == 2 and t <= 180)
            if d == 0:
                rh = ra = BASE
            else:
                lead_r = EN_LEADER if pull else BASE
                trail_r = SIX_ON_FIVE if pull else BASE
                rh, ra = (lead_r, trail_r) if d > 0 else (trail_r, lead_r)
            ph, pa = rh * STEP / 60, ra * STEP / 60
            kk = min(k + 1, MAXK)
            for (nd, nk), q in (((min(d + 1, MAXD), kk), ph), ((max(d - 1, -MAXD), kk), pa), ((d, k), 1 - ph - pa)):
                Q[(nd, nk)] = Q.get((nd, nk), 0) + p * q
        P = Q
    F = {}
    for (d, k), p in P.items():
        if d == 0:
            for nd in (1, -1):
                F[(nd, min(k + 1, MAXK))] = F.get((nd, min(k + 1, MAXK)), 0) + p / 2
        else:
            F[(d, k)] = F.get((d, k), 0) + p
    return F


def p_margin(d0, secs, home_side, handicap):
    """Chance a side covers: (its final margin + handicap) > 0. Moneyline = handicap 0."""
    s = 1 if home_side else -1
    return sum(p for (d, k), p in outcome(d0, secs).items() if s * d + handicap > 0)


def p_over(d0, secs, goals_now, line):
    return sum(p for (d, k), p in outcome(d0, secs).items() if goals_now + k > line)


# ---------------------------------------------------------------- feeds ---
def get(u, timeout=20):
    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def num(x):
    if isinstance(x, dict):
        x = x.get('value')
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def quotes(m):
    """(bid, ask) for the market's long side. Fresh from the bbo endpoint; fall back to the event copy."""
    try:
        md = get(f"https://gateway.polymarket.us/v1/markets/{m['slug']}/bbo")['marketData']
        b, a = num(md.get('bestBid')), num(md.get('bestAsk'))
        if b is not None and a is not None:
            return b, a
    except Exception:
        pass
    return num(m.get('bestBidQuote') or m.get('bestBid')), num(m.get('bestAskQuote') or m.get('bestAsk'))


def parse_markets(ev, away, home):
    """Full-game moneyline, puck lines and game totals -> list of dicts describing the LONG side:
       kind 'side' (home_side, handicap) or 'total' (line); plus slug and a plain-English label."""
    ev = ev.get('event', ev)
    mk = ev.get('markets', [])
    team_of = {}
    for m in mk:                                    # moneyline: long side = away team
        t = (m.get('sportsMarketType') or '')
        if 'full_game_winner' in t:
            for s in m.get('marketSides', []):
                if s.get('teamId') is not None:
                    team_of[s['teamId']] = 'away' if s.get('long') else 'home'
    res = []
    for m in mk:
        t = (m.get('sportsMarketType') or '')
        if 'first' in t or 'second' in t or 'third' in t or 'period' in t or not m.get('slug'):
            continue
        sides = m.get('marketSides', [])
        long = next((s for s in sides if s.get('long')), sides[0] if sides else {})
        if 'full_game_winner' in t:
            res.append(dict(slug=m['slug'], kind='side', home_side=False, handicap=0.0,
                            label=f"{away} to win", other=f"{home} to win"))
        elif 'full_game_spread' in t:
            try:
                h = float(str(long.get('description', '')).replace('+', ''))
            except ValueError:
                continue
            side = team_of.get(long.get('teamId'))
            if side is None:
                continue
            T, O = (home, away) if side == 'home' else (away, home)
            res.append(dict(slug=m['slug'], kind='side', home_side=(side == 'home'), handicap=h,
                            label=f"{T} {h:+.1f}", other=f"{O} {-h:+.1f}"))
        elif 'total' in t and '-tt-' not in m['slug'] and 'team_points' not in t:
            ln = num(m.get('line'))
            if ln is None:
                continue
            res.append(dict(slug=m['slug'], kind='total', line=ln, label=f"Over {ln:g}", other=f"Under {ln:g}"))
    return res


def fair_long(m, d0, secs, goals):
    if m['kind'] == 'total':
        return p_over(d0, secs, goals, m['line'])
    return p_margin(d0, secs, m['home_side'], m['handicap'])


# ---------------------------------------------------------------- alerts ---
def push(title, msg, prio='max', tags='bell,rotating_light'):
    print(f'[PUSH] {title} | {msg}', flush=True)
    if MODE == 'PAPER':
        return
    try:
        import notify
        t = notify.topic()
        status = 'no topic (NTFY_TOPIC secret missing or empty)'
        if t:
            r = urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{t}", data=msg.encode(),
                                       headers={'Title': title, 'Priority': prio, 'Tags': tags}), timeout=20)
            status = f'sent to topic of length {len(t)} starting {t[:4]}..., ntfy answered HTTP {r.status}'
        if os.environ.get('NL_TEST_PUSH') == '1':
            os.makedirs(OUT, exist_ok=True)
            open(os.path.join(OUT, 'push_test.txt'), 'w').write(f"{dt.datetime.now(PT).isoformat()} {status}\n")
        print('push status:', status, flush=True)
        print(f'::warning title=push::{status}', flush=True)
    except Exception as e:
        print('push failed:', e, flush=True)
        print(f'::warning title=push::FAILED {type(e).__name__}: {e}', flush=True)


def log(row, name='nearlock_log.csv'):
    path = os.path.join(OUT, name); new = not os.path.exists(path)
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def card_bets(today):
    """Today's NHL card bets -> {game: [bet text]}."""
    try:
        bets = json.load(open(os.path.join(OUT, f'bets_{today}.json'))).get('NHL', [])
    except Exception:
        return {}
    out = {}
    for b in bets:
        out.setdefault(b['game'], []).append(b['bet'])
    return out


def match_bet(text, m, away, home):
    """Does a card bet correspond to this market? Returns 'long', 'short' or None."""
    s = text.replace('(puck line)', '').replace('goals', '').strip()
    if m['kind'] == 'total':
        for side, lab in (('long', 'Over'), ('short', 'Under')):
            if s.startswith(lab):
                try:
                    if abs(float(s.split()[1]) - m['line']) < 1e-6:
                        return side
                except (IndexError, ValueError):
                    pass
        return None
    parts = s.split()
    team = parts[0]
    if s.endswith('to win'):
        h = 0.0
    else:
        try:
            h = float(parts[1])
        except (IndexError, ValueError):
            return None
    long_team = home if m['home_side'] else away
    if team == long_team and abs(h - m['handicap']) < 1e-6:
        return 'long'
    if team != long_team and team in (away, home) and abs(h + m['handicap']) < 1e-6:
        return 'short'
    return None


def even_strength(g):
    sit = g.get('situation') or {}
    descs = []
    for side in ('homeTeam', 'awayTeam'):
        descs += (sit.get(side) or {}).get('situationDescriptions') or []
    return 'PP' not in descs


def scan_game(g, today, cache, sent, bets, bot=None):
    A, H = g['awayTeam']['abbrev'], g['homeTeam']['abbrev']; game = f'{A} @ {H}'
    if g.get('gameState') not in ('LIVE', 'CRIT'):
        return
    per = (g.get('periodDescriptor') or {}).get('number') or 0
    clock = g.get('clock') or {}
    if per != 3 or clock.get('inIntermission'):
        return
    mm, ss = map(int, (clock.get('timeRemaining') or '0:00').split(':'))
    secs = mm * 60 + ss
    if secs < 15:
        return
    a_s, h_s = int(g['awayTeam'].get('score', 0)), int(g['homeTeam'].get('score', 0))
    d0, goals = h_s - a_s, a_s + h_s
    if not even_strength(g):
        print(game, 'power play - skipped'); return
    ck = cache.get(game)
    if not ck or time.time() - ck[0] > 600:
        try:
            ev = get(f"https://gateway.polymarket.us/v1/events/slug/nhl-{A.lower()}-{H.lower()}-{today}")
            ck = cache[game] = (time.time(), parse_markets(ev, A, H))
        except Exception as e:
            print(game, 'markets unavailable:', e); return
    mine = bets.get(game, [])
    for m in ck[1]:
        f_long = fair_long(m, d0, secs, goals)
        f = {'long': f_long, 'short': 1 - f_long}
        held = [side for side in ('long', 'short') if any(match_bet(b, m, A, H) == side for b in mine)]
        near = [side for side in ('long', 'short') if f[side] >= MIN_FAIR] if ONLY != 'sell' else []
        if ONLY == 'buy':
            held = []
        if not near and not held:
            continue                                   # nothing to check: skip the price call
        bid, ask = quotes(m)
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            continue
        cost = {'long': ask, 'short': round(1 - bid, 4)}
        sell = {'long': bid, 'short': round(1 - ask, 4)}
        name = {'long': m['label'], 'short': m['other']}
        state = f"{A} {a_s}-{h_s} {H}, {mm}:{ss:02d} left in the 3rd"
        for side in near:
            gap = f[side] - cost[side]
            bot_owns = bool(bot and bot.enabled and not bot.s['halted'] and m['kind'] == 'side')
            if bot_owns:
                lt = H if m['home_side'] else A
                team, hc = (lt, m['handicap']) if side == 'long' else ((A if lt == H else H), -m['handicap'])
                bot.consider(game, m, side, team, hc, f[side], cost[side], f'{A} {a_s}-{h_s} {H} {mm}:{ss:02d}', name[side], secs)
            if gap >= 0.03:
                log(dict(time=dt.datetime.now(PT).isoformat(timespec='seconds'), date=today, game=game, type='snapshot',
                         bet=name[side], score=f'{a_s}-{h_s}', left=f'{mm}:{ss:02d}', fair=f'{f[side]:.3f}',
                         price=f'{cost[side]:.2f}', gap=f'{gap:+.3f}', result=''), 'nearlock_snapshots.csv')
            key = f"{today}|{m['slug']}|{side}|{a_s}-{h_s}|buy"
            if gap >= EDGE and cost[side] <= MAX_COST and key not in sent:
                sent[key] = 1
                if bot and bot.enabled:                        # bot-only: no manual buy alerts (logged only)
                    log(dict(time=dt.datetime.now(PT).isoformat(timespec='seconds'), date=today, game=game, type='near-lock buy',
                             bet=name[side], score=f'{a_s}-{h_s}', left=f'{mm}:{ss:02d}', fair=f'{f[side]:.3f}',
                             price=f'{cost[side]:.2f}', gap=f'{gap:+.3f}', result=''))
                    continue
                head = 'PAPER near-lock (log only)' if MODE == 'PAPER' else f'NEAR-LOCK BUY: {name[side]}'
                push(head, f"{state}. Real chance {f[side]*100:.1f}%, Polymarket asks {cost[side]*100:.0f}c "
                           f"(gap {gap*100:.0f}c). Buy up to {min(cost[side]+0.02, f[side]-0.03)*100:.0f}c, {STAKE} stake. "
                           f"Skip if a penalty was just called or the goalie is already pulled.")
                log(dict(time=dt.datetime.now(PT).isoformat(timespec='seconds'), date=today, game=game, type='near-lock buy',
                         bet=name[side], score=f'{a_s}-{h_s}', left=f'{mm}:{ss:02d}', fair=f'{f[side]:.3f}',
                         price=f'{cost[side]:.2f}', gap=f'{gap:+.3f}', result=''))
        for side in held:
            over = sell[side] - f[side]
            key = f"{today}|{m['slug']}|{side}|{a_s}-{h_s}|sell"
            if over >= SELL_EDGE and f[side] <= SELL_MAX_FAIR and key not in sent:
                sent[key] = 1
                push(f'CASH OUT: {name[side]}', f"{state}. Polymarket pays {sell[side]*100:.0f}c for your position; "
                     f"real chance about {f[side]*100:.0f}%. Selling is worth {over*100:.0f}c per share more than holding "
                     f"(if you placed this card bet).", 'high', 'moneybag')
                log(dict(time=dt.datetime.now(PT).isoformat(timespec='seconds'), date=today, game=game, type='cash-out',
                         bet=name[side], score=f'{a_s}-{h_s}', left=f'{mm}:{ss:02d}', fair=f'{f[side]:.3f}',
                         price=f'{sell[side]:.2f}', gap=f'{over:+.3f}', result=''))


def main(max_hours=7.0, once=False):
    os.makedirs(OUT, exist_ok=True)
    today = dt.datetime.now(PT).strftime('%Y-%m-%d')
    sp = os.path.join(OUT, 'nearlock_sent.json')
    try:
        sent = json.load(open(sp))
    except Exception:
        sent = {}
    sent = {k: v for k, v in sent.items() if k.startswith(today)}
    cache, end = {}, time.time() + max_hours * 3600
    print(f'Near-lock scanner {today}, mode {MODE}, edge {EDGE}, min fair {MIN_FAIR}', flush=True)
    bot = None
    try:
        import nlbot
        bot = nlbot.Bot(today)
        print('::notice title=bot::' + ('enabled' if bot.enabled else 'off'), flush=True)
    except Exception as e:
        print('bot unavailable:', type(e).__name__, flush=True)
    while time.time() < end:
        try:
            games = get(f'https://api-web.nhle.com/v1/score/{today}').get('games', [])
        except Exception as e:
            print('scoreboard error', e, flush=True); time.sleep(POLL_IDLE); continue
        if not games:
            print('no NHL games today'); break
        states = [g.get('gameState') for g in games]
        if all(s in ('OFF', 'FINAL') for s in states):
            print('all games final'); break
        starts = [dt.datetime.fromisoformat(g['startTimeUTC'].replace('Z', '+00:00')) for g in games
                  if g.get('gameState') in ('FUT', 'PRE') and g.get('startTimeUTC')]
        live = [g for g in games if g.get('gameState') in ('LIVE', 'CRIT')]
        if not live and starts and min(starts) - dt.datetime.now(dt.timezone.utc) > dt.timedelta(hours=3):
            print('next game is 3+ hours away; stopping'); break
        bets = card_bets(today)
        for g in live:
            try:
                scan_game(g, today, cache, sent, bets, bot)
            except Exception as e:
                print('scan error', g.get('id'), e, flush=True)
        json.dump(sent, open(sp, 'w'))
        if bot and bot.s['trades']:
            try:
                bot.grade({f"{g['awayTeam']['abbrev']} @ {g['homeTeam']['abbrev']}": (g['awayTeam']['abbrev'], g['homeTeam']['abbrev'],
                           int(g['awayTeam'].get('score', 0)), int(g['homeTeam'].get('score', 0)))
                           for g in games if g.get('gameState') in ('OFF', 'FINAL')})
            except Exception as e:
                print('bot grade error', type(e).__name__, flush=True)
        if once:
            break
        third = any((g.get('periodDescriptor') or {}).get('number') == 3 for g in live)
        time.sleep((bot.c.get('poll_secs', 2) if bot and bot.enabled and not bot.s['halted'] else POLL_LIVE) if third else POLL_IDLE)
    fill_results()
    json.dump(sent, open(sp, 'w'))
    if bot and bot.s['trades']:
        try:
            games = get(f'https://api-web.nhle.com/v1/score/{today}').get('games', [])
            bot.grade({f"{g['awayTeam']['abbrev']} @ {g['homeTeam']['abbrev']}": (g['awayTeam']['abbrev'], g['homeTeam']['abbrev'],
                       int(g['awayTeam'].get('score', 0)), int(g['homeTeam'].get('score', 0)))
                       for g in games if g.get('gameState') in ('OFF', 'FINAL')})
        except Exception:
            pass
        if all(t.get('result') for t in bot.s['trades']):
            bot.summary()
    print('finished', flush=True)


def fill_results():
    """Grade logged alerts/snapshots once games are final (W = the alerted side won)."""
    for name in ('nearlock_log.csv', 'nearlock_snapshots.csv'):
        path = os.path.join(OUT, name)
        if not os.path.exists(path):
            continue
        rows = list(csv.DictReader(open(path)))
        finals = {}
        for d in {r['date'] for r in rows if not r['result']}:
            try:
                for g in get(f'https://api-web.nhle.com/v1/score/{d}').get('games', []):
                    if g.get('gameState') in ('OFF', 'FINAL'):
                        A, H = g['awayTeam']['abbrev'], g['homeTeam']['abbrev']
                        finals[(d, f'{A} @ {H}')] = (int(g['awayTeam']['score']), int(g['homeTeam']['score']),
                                                     (g.get('periodDescriptor') or {}).get('number', 3) > 3)
            except Exception:
                pass
        for r in rows:
            k = (r['date'], r['game'])
            if r['result'] or k not in finals:
                continue
            a, h, ot = finals[k]; A, H = r['game'].split(' @ ')
            b = r['bet'].split()
            try:
                if b[0] in ('Over', 'Under'):
                    won = (a + h > float(b[1])) == (b[0] == 'Over')
                elif b[-2:] == ['to', 'win']:
                    won = (a > h) == (b[0] == A)
                else:
                    marg = (h - a) if b[0] == H else (a - h)
                    won = marg + float(b[1]) > 0
                r['result'] = 'W' if won else 'L'
            except Exception:
                pass
        with open(path, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


if __name__ == '__main__':
    if os.environ.get('NL_TEST_PUSH') == '1':
        push('Near-lock scanner connected', 'Test from GitHub: this is how a near-lock alert will sound (max priority).', 'max', 'bell')
        sys.exit(0)
    if '--once' in sys.argv:
        main(once=True)
    else:
        main(float(sys.argv[1]) if len(sys.argv) > 1 else 7.0)
