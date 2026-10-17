"""FrankenClaude (Oct 16, 2026): late-game football near-lock bot, NFL + college. Live.
Idea: be the market. In the last 8 minutes, when a team is safely ahead (16 years of NFL play-by-play says it wins 88-99%),
rest buy orders for the leader a few cents under its fair chance so lazy/panicked market sellers fill us; also buy at once
if the price is already 6c+ under fair. Polymarket is the alarm: if the leader's ASK drops fast (real bad news, everyone
reprices) pull every order; if only the bid drops (a seller dumping), keep the orders up.
Rules:
  NFL buy spots: lead 8+ with ball (last 8 min); lead 9+ without ball (last 4); lead 4-7 with ball (last 4); lead 8 without ball (last 4).
  College: lead 9+ with ball or 14+ without (last 8 min).  Never a lead of 3 or less, never above 97c.
  Ladder: $1 at fair-4c, $2 at fair-8c, $2 at fair-12c. Instant buy $5 if the price is fair-6c or better.
  Go hard: when fair is 98%+ and the price is 8c+ under it, instant buy $10 and allow $20 for that game.
  Limits: $10 a game, $50 a week per league (Tue-Mon, Pacific). Every resting order counts as spent until its fill is known.
  One phone alert at $40 of $50 for a league, one at the cap. Holds to the end (no selling yet).
Nothing about trades is printed (public repo). State lives in bot_state/ (Actions cache)."""
import json, os, time, datetime as dt, urllib.request
from zoneinfo import ZoneInfo
import pmtrade

HERE = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(HERE, 'bot_state'); os.makedirs(SD, exist_ok=True)
PT = ZoneInfo('America/Los_Angeles')
CFG = dict(live=True, max_price=0.97, instant_gap=0.06, ladder=[[0.04, 1.0], [0.08, 2.0], [0.12, 2.0]], stake=5.0,
           news_drop=0.04, cooldown=60, alert_at=40.0,
           big_fair=0.98, big_gap=0.08, big_stake=10.0, big_game_cap=20.0,   # 'go hard' tier (Oct 16): near-certain + big discount
           nfl=dict(game_cap=10.0, week_cap=50.0), cfb=dict(game_cap=10.0, week_cap=50.0))
# NFL 2010-25, 4th quarter: leader's real win chance by (lead bucket, has ball) for 4-8 min / 2-4 min / under 2 min left
TABLE = {('4-7', 1): (.838, .878, .974), ('4-7', 0): (.735, .737, .816), ('8', 1): (.934, .955, .985), ('8', 0): (.863, .892, .936),
         ('9-14', 1): (.963, .998, .999), ('9-14', 0): (.921, .961, .996), ('15', 1): (.995, .998, .999), ('15', 0): (.99, .995, .999)}
_mem = {}          # per game: sig, poss, last leader ask, cooldown-until, last check time


def _cfg():
    c = json.loads(json.dumps(CFG))
    try:
        for k, v in json.load(open(os.path.join(HERE, 'bot.json'))).get('frankenclaude', {}).items():
            if isinstance(v, dict) and isinstance(c.get(k), dict):
                c[k].update(v)
            else:
                c[k] = v
    except Exception:
        pass
    return c


def _today():
    return dt.datetime.now(PT).date()


def _week_start():
    t = _today()
    return t - dt.timedelta(days=(t.weekday() - 1) % 7)                  # most recent Tuesday


def _load(p, default):
    try:
        return json.load(open(p))
    except Exception:
        return default


def _state():
    p = os.path.join(SD, f"fc_{_today().isoformat()}.json")
    return p, _load(p, dict(spent={}, games={}, orders={}))


def _save(p, s):
    json.dump(s, open(p, 'w'))


def _week_spent(sp, s_today):
    tot, start = 0.0, _week_start()
    for i in range((_today() - start).days + 1):
        d = start + dt.timedelta(days=i)
        s = s_today if d == _today() else _load(os.path.join(SD, f"fc_{d.isoformat()}.json"), {})
        tot += float(s.get('spent', {}).get(sp, 0.0))
        tot += sum(o['dollars'] for os_ in s.get('orders', {}).values() for o in os_ if o.get('sport') == sp)  # open orders = spent
    return tot


def _ping(title, msg):
    t = os.environ.get('NTFY_TOPIC', '').strip()
    if not t:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{t}', data=msg.encode(),
                               headers={'Title': title, 'Priority': 'high', 'Tags': 'football,robot'}), timeout=20)
    except Exception:
        pass


def _alerts(c, sp, s):
    ap = os.path.join(SD, f"fc_alerts_{_week_start().isoformat()}.json"); a = _load(ap, {})
    w = _week_spent(sp, s); cap = c[sp]['week_cap']; name = 'NFL' if sp == 'nfl' else 'College'
    if w >= cap - 1 and not a.get(f'{sp}_cap'):
        a[f'{sp}_cap'] = a[f'{sp}_40'] = True
        _ping(f'FrankenClaude: {name} at weekly limit', f'{name} has used ${w:.0f} of ${cap:.0f} this week. It stops buying {name} until Tuesday.')
    elif w >= c['alert_at'] and not a.get(f'{sp}_40'):
        a[f'{sp}_40'] = True
        _ping(f'FrankenClaude: {name} near weekly limit', f'{name} has used ${w:.0f} of ${cap:.0f} this week.')
    json.dump(a, open(ap, 'w'))


def _fair(sp, lead, ball, secs):
    if lead < 4:
        return None
    if sp == 'nfl':
        ok = (lead >= 8 and ball) or (lead >= 9 and secs <= 240) or (4 <= lead <= 7 and ball and secs <= 240) or (lead == 8 and secs <= 240)
    else:
        ok = (lead >= 9 and ball) or lead >= 14
    if not ok:
        return None
    b = '4-7' if lead <= 7 else '8' if lead == 8 else '9-14' if lead <= 14 else '15'
    return TABLE[(b, 1 if ball else 0)][0 if secs > 240 else 1 if secs > 120 else 2]


def _moneyline(ev):
    for m in (ev.get('event', ev)).get('markets', []):
        t = m.get('sportsMarketType') or ''
        if 'full_game' in t and 'winner' in t:
            return m['slug'], pmtrade.amt(m.get('bestBidQuote')), pmtrade.amt(m.get('bestAskQuote'))
    return None


def _close(s, key):
    """Cancel all resting orders for a game; book what filled (or the full amount when the fill can't be read)."""
    for o in s['orders'].pop(key, []):
        pmtrade.cancel(o['slug'], o['id'])
        f = pmtrade.fill(o['id'])
        if f is None:
            used = o['dollars']                                              # unknown: assume all of it filled
        else:
            q, px = f
            c = (px if o['long'] else (1 - px)) if px else o['cost']
            used = min(o['dollars'], q * c)
        s['spent'][o['sport']] = s['spent'].get(o['sport'], 0.0) + used
        s['games'][key] = s['games'].get(key, 0.0) + used


def _game_used(s, key):
    return s['games'].get(key, 0.0) + sum(o['dollars'] for o in s['orders'].get(key, []))


def consider(g, ev):
    """Called by record_all.py for each live game. Returns True while the game is in a buy spot (so it gets checked fast)."""
    c = _cfg(); sp = g.get('sport')
    if not c['live'] or sp not in ('nfl', 'cfb') or not pmtrade.have_keys():
        return False
    key = f"{sp}|{g['a']}@{g['h']}"
    p, s = _state(); mem = _mem.setdefault(key, {})
    try:
        per = int(g.get('period') or 0); m_, ss = str(g.get('clock') or '0:00').split(':'); secs = int(m_) * 60 + int(float(ss))
        a, h = int(g.get('sa') or 0), int(g.get('sh') or 0)
    except ValueError:
        return False
    ml = _moneyline(ev)
    fair = None
    if per == 4 and secs <= 480 and a != h and g.get('state') == 'in' and ml and ml[1] and ml[2]:
        ldr_away = a > h; ldr = g['a'] if ldr_away else g['h']
        if g.get('poss'):
            mem['poss'] = g['poss']
        if mem.get('score') != (a, h):
            mem['score'] = (a, h); mem['poss'] = g.get('poss') or ''          # after a score, wait for fresh possession
        ball = mem.get('poss') == ldr
        fair = _fair(sp, abs(a - h), ball, secs)
    if fair is None:
        if s['orders'].get(key):
            _close(s, key); _save(p, s); _alerts(c, sp, s)
        mem.pop('ask', None)
        return False
    slug, bid, ask = ml
    cost = ask if ldr_away else round(1 - bid, 4)                          # what the leader costs to buy now
    lbid = bid if ldr_away else round(1 - ask, 4)                          # where sellers of the leader are hitting
    tb = 0 if secs > 240 else 1 if secs > 120 else 2
    sig = (a, h, ball, tb)
    now = time.time()
    # Polymarket alarm: leader's ask falling fast = real news -> pull everything, sit out a minute
    if mem.get('ask') is not None and mem['ask'] - cost >= c['news_drop']:
        mem['cool'] = now + c['cooldown']
    mem['ask'] = cost
    if now < mem.get('cool', 0) or sig != mem.get('sig'):
        if s['orders'].get(key):
            _close(s, key); _save(p, s); _alerts(c, sp, s)
        if now < mem.get('cool', 0):
            return True
        mem['sig'] = sig
    week_left = c[sp]['week_cap'] - _week_spent(sp, s)
    big = fair >= c['big_fair'] and cost <= fair - c['big_gap']           # near-certain AND a big discount: go hard
    game_left = (c['big_game_cap'] if fair >= c['big_fair'] else c[sp]['game_cap']) - _game_used(s, key)
    room = min(week_left, game_left)
    if room < 1:
        return True
    # 1) Instant buy when the price already lags fair by 6c+
    if cost <= min(fair - c['instant_gap'], c['max_price']) and not mem.get('took') == sig:
        try:
            r = pmtrade.buy(slug, ldr_away, min(fair - c['instant_gap'], c['max_price']), min(c['big_stake'] if big else c['stake'], room), size_price=cost)
        except Exception:
            r = dict(ok=False)
        mem['took'] = sig
        if r.get('ok'):
            s['spent'][sp] = s['spent'].get(sp, 0.0) + r['spent']; s['games'][key] = s['games'].get(key, 0.0) + r['spent']
            _save(p, s); _alerts(c, sp, s)
            room -= r['spent']
    # 2) Ladder of resting orders under fair (posted once per game situation)
    if not s['orders'].get(key) and mem.get('posted') != sig and room >= 1:
        mem['posted'] = sig; placed = []
        for gap, dollars in c['ladder']:
            px = min(fair - gap, c['max_price'])
            d = min(dollars, room)
            if d < 1 or px >= cost:                                         # would cross the ask: the instant rule covers that
                continue
            try:
                r = pmtrade.post(slug, ldr_away, px, d)
            except Exception:
                r = dict(ok=False)
            if r.get('ok'):
                placed.append(dict(id=r['order_id'], slug=slug, long=ldr_away, cost=r['cost'], dollars=round(r['qty'] * (r['cost'] + 0.02), 2), sport=sp))
                room -= placed[-1]['dollars']
        if placed:
            s['orders'][key] = placed; _save(p, s); _alerts(c, sp, s)
    return True


def cleanup():
    """Cancel every resting order this bot has (end of a recorder run, or leftovers from a crashed run)."""
    if not pmtrade.have_keys():
        return
    c = _cfg()
    for d in (_today(), _today() - dt.timedelta(days=1)):
        p = os.path.join(SD, f"fc_{d.isoformat()}.json"); s = _load(p, None)
        if not s or not s.get('orders'):
            continue
        sports = {o['sport'] for os_ in s['orders'].values() for o in os_}
        for key in list(s['orders']):
            _close(s, key)
        _save(p, s)
        for sp in sports:
            if d == _today():
                _alerts(c, sp, s)
