"""FrankenClaude (Oct 16, 2026): late-game football near-lock bot, NFL + college. Live.
Idea: be the market. In the last 8 minutes, when a team is safely ahead (16 years of NFL play-by-play says it wins 88-99%),
rest buy orders for the leader a few cents under its fair chance so lazy/panicked market sellers fill us; also buy at once
if the price is already 6c+ under fair. Polymarket is the alarm: if the leader's ASK drops fast (real bad news, everyone
reprices) pull every order; if only the bid drops (a seller dumping), keep the orders up.
Rules:
  NFL buy spots: lead 8+ with ball (last 8 min); lead 9+ without ball (last 4); lead 4-7 with ball (last 4); lead 8 without ball (last 4).
  College: lead 9+ with ball or 14+ without (last 8 min).  Never a lead of 3 or less, never above 97c.
  Ladder: $1 at fair-4c, $2 at fair-8c, $2 at fair-12c. Instant buy $5 if the price is fair-6c or better.
  Early comeback (college; same-division games only, cross-division games skipped by every rule): 75c+ pregame favorite down 7+ in the first half, price down 15c+ -> $5 ($8 if 20c+).
  Clawback: if the account holds the trailing team and the leader is 95%+, use go-hard sizes.
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
           big_fair=0.98, big_gap=0.08, big_stake=10.0, big_game_cap=20.0, hedge_fair=0.95,
           ec_min_pre=0.75, ec_drop=0.15, ec_big_drop=0.20,   # Oct 17 retune: 15c+ drop $5, 20c+ $8
           ec_stake=5.0, ec_big=8.0, ec_wait=120,   # college early comeback (Oct 17)   # 'go hard' tier (Oct 16): near-certain + big discount
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


def _note(title, msg):
    """Phone notice for each buy, normal sound (Oct 17: Stephen wants a sound, not a loud alert)."""
    t = os.environ.get('NTFY_TOPIC', '').strip()
    if not t:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{t}', data=msg.encode(),
                               headers={'Title': title, 'Priority': 'default', 'Tags': 'football'}), timeout=20)
    except Exception:
        pass


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
    if sp == 'cfb':
        # Oct 17 college 4th-quarter table (2025 season, Polymarket prices after Q4 scores): leads of 11-16 won ~96%,
        # 17+ ~98%, while Polymarket priced them ~90-93c. Whole 4th quarter, possession doesn't matter.
        if lead < 11 or secs > 900:
            return None
        return 0.96 if lead <= 16 else 0.98
    ok = (lead >= 8 and ball) or (lead >= 9 and secs <= 240) or (4 <= lead <= 7 and ball and secs <= 240) or (lead == 8 and secs <= 240)
    if not ok:
        return None
    b = '4-7' if lead <= 7 else '8' if lead == 8 else '9-14' if lead <= 14 else '15'
    return TABLE[(b, 1 if ball else 0)][0 if secs > 240 else 1 if secs > 120 else 2]


_own_cache = {}
def _holds_other(ml_slug, ldr_away):
    """Clawback (Oct 16): True if the account holds the TRAILING team in this game (moneyline or spread).
    Long = away on football moneyline/spread markets. Any error -> False (no bigger bets on a guess)."""
    key = ml_slug.split('-', 1)[1] if '-' in ml_slug else ml_slug          # e.g. cfb-ecar-uab-2026-10-15
    hit = _own_cache.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    try:
        P = pmtrade.call('GET', '/v1/portfolio/positions').get('positions', {})
    except Exception:
        return False
    own = False
    for k, v in P.items():
        if key not in k or not (k.startswith('aec-') or k.startswith('asc-')):
            continue
        n = float(v.get('netPosition') or 0)
        if n and ((n > 0) != ldr_away):                                    # we hold the side that isn't the leader
            own = True
    _own_cache[key] = (time.time(), own)
    return own


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
        if used > 0.01:
            _note('FrankenClaude: standing order filled', f"{key.split('|')[1]}: ${used:.2f} at {o['cost']*100:.0f}c"
                  + (' (fill not readable, counted as full)' if f is None else ''))


def _game_used(s, key):
    return s['games'].get(key, 0.0) + sum(o['dollars'] for o in s['orders'].get(key, []))


def _early(c, sp, key, g, ml, per, a, h, p, s, mem):
    """College early comeback (Oct 17 test, 2025 season, 83 games: +13%; +25% when the price fell 15c+):
    a 75c+ pregame favorite falls behind by 7+ in the first half -> buy the favorite once the price has dropped 15c+ (Oct 17 retune: +30% on 32 games; 20c+ +51% on 18)
    and the deficit has stood for 2 minutes. $5 ($8 if the price dropped 20c+), one buy per game."""
    if sp != 'cfb' or g.get('cross') or not ml or not ml[1] or not ml[2]:
        return False                                                        # Oct 17: same-division games only (never back a smaller school)
    slug, bid, ask = ml
    pre = s.setdefault('pre', {})
    if key not in pre:
        if per == 1 and a == 0 and h == 0:
            pre[key] = round((bid + ask) / 2, 4); _save(p, s)               # away team's chance at kickoff
        return False
    if per not in (1, 2) or key in s.setdefault('ec', []):
        return False
    pa = pre[key]; fav_away = pa >= 0.5; favp = pa if fav_away else 1 - pa
    deficit = (h - a) if fav_away else (a - h)
    if favp < c['ec_min_pre'] or deficit < 7:
        mem.pop('ec_sig', None); return False
    cost = ask if fav_away else round(1 - bid, 4)
    drop = favp - cost
    if drop < c['ec_drop']:
        return True                                                         # watch closely
    if mem.get('ec_sig') != (a, h):
        mem['ec_sig'] = (a, h); mem['ec_t'] = time.time(); return True
    if time.time() - mem['ec_t'] < c['ec_wait']:
        return True
    d = min(c['ec_big'] if drop >= c['ec_big_drop'] else c['ec_stake'],
            c[sp]['week_cap'] - _week_spent(sp, s), c[sp]['game_cap'] - _game_used(s, key))
    s['ec'].append(key)
    if d >= 1:
        try:
            r = pmtrade.buy(slug, fav_away, min(cost + 0.01, favp - c['ec_drop'] + 0.01), d, size_price=cost)
        except Exception:
            r = dict(ok=False)
        if r.get('ok'):
            s['spent'][sp] = s['spent'].get(sp, 0.0) + r['spent']; s['games'][key] = s['games'].get(key, 0.0) + r['spent']
            tm = g['a'] if fav_away else g['h']
            _note(f'FrankenClaude: {tm} (early comeback)', f"{g['a']} {a}-{h} {g['h']}. Bought {tm} to win at {r.get('avg_cost', cost)*100:.0f}c, "
                  f"${r['spent']:.2f} (was {favp*100:.0f}c at kickoff).")
    _save(p, s); _alerts(c, sp, s)
    return False


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
    if per in (1, 2) and g.get('state') == 'in' and _early(c, sp, key, g, ml, per, a, h, p, s, mem):
        return True
    fair = None
    if per == 4 and secs <= (900 if sp == 'cfb' else 480) and a != h and g.get('state') == 'in' and ml and ml[1] and ml[2] and not g.get('cross'):
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
    hedge = fair >= c['hedge_fair'] and _holds_other(slug, ldr_away)      # clawback: we hold the trailing team
    big = ((fair >= c['big_fair'] or sp == 'cfb') and cost <= fair - c['big_gap']) or hedge  # big discount (college: any Q4 table spot), or clawback
    game_left = (c['big_game_cap'] if (big or hedge) else c[sp]['game_cap']) - _game_used(s, key)
    room = min(week_left, game_left)
    if room < 1:
        return True
    # 1) Instant buy when the price already lags fair by 6c+
    gap_i = 0.05 if sp == 'cfb' else c['instant_gap']                       # college: buy at 5c+ under fair (Oct 17)
    if cost <= min(fair - gap_i, c['max_price']) and not mem.get('took') == sig:
        try:
            r = pmtrade.buy(slug, ldr_away, min(fair - gap_i, c['max_price']), min(c['big_stake'] if big else c['stake'], room), size_price=cost)
        except Exception:
            r = dict(ok=False)
        mem['took'] = sig
        if r.get('ok'):
            s['spent'][sp] = s['spent'].get(sp, 0.0) + r['spent']; s['games'][key] = s['games'].get(key, 0.0) + r['spent']
            _note(f"FrankenClaude: {ldr}{' (clawback)' if hedge else ''}", f"{g['a']} {a}-{h} {g['h']}, {g.get('clock')} left. Bought {ldr} to win at "
                  f"{(r.get('avg_cost') or cost)*100:.0f}c, ${r['spent']:.2f} (real chance {fair*100:.0f}%).")
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
