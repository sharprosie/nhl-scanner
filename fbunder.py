"""Football slow-start Under (Oct 15, 2026). Idea: early in a game, both offenses run into unexpected resistance and the
live total is slow to come down. Late in the 1st quarter (under 5:00 left), buy the Under on the main live total when:
  - few points so far (NFL <= 7, college <= 10), and
  - both teams are passing badly (net passing yards per dropback: NFL < 4.0, college < 4.5; ESPN live box score), and
  - the market is still slow: the main line has dropped 3 points or less since we first saw it, and the Under costs 40-70c.
History (NFL 2012-25, vs the Vegas closing total): low 1st quarter + both offenses struggling -> under about 70-75%.
Unproven against live prices. NFL $2 a game / $10 a day, college $1 a game / $10 a day. Nothing printed about trades."""
import json, os, time, datetime as dt, urllib.request
import pmtrade
HERE = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(HERE, 'bot_state'); os.makedirs(SD, exist_ok=True)
CFG = dict(live=True, min_cost=0.40, max_cost=0.70, max_line_drop=3.0, min_dropbacks=3,
           nfl=dict(stake=2.0, day_cap=10.0, max_pts=7, max_ypd=4.0),
           cfb=dict(stake=1.0, day_cap=10.0, max_pts=10, max_ypd=4.5))
ESPN_SUM = {'nfl': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}',
            'cfb': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary?event={id}'}
_checked, _base = {}, {}


def _cfg():
    c = {k: (dict(v) if isinstance(v, dict) else v) for k, v in CFG.items()}
    try:
        u = json.load(open(os.path.join(HERE, 'bot.json'))).get('football_under', {})
        for k, v in u.items():
            if isinstance(v, dict) and isinstance(c.get(k), dict):
                c[k].update(v)
            else:
                c[k] = v
    except Exception:
        pass
    return c


def _state():
    p = os.path.join(SD, f"fbunder_{dt.date.today().isoformat()}.json")
    try:
        return p, json.load(open(p))
    except Exception:
        return p, dict(done=[], spent={})


def _main_total(ev):
    """(slug, under_cost, line) for the live total priced nearest 50c."""
    best = None
    for m in (ev.get('event', ev)).get('markets', []):
        t = m.get('sportsMarketType') or ''
        if 'full_game' not in t or 'total' not in t or 'team_points' in t:
            continue
        bid = pmtrade.amt(m.get('bestBidQuote'))
        line = pmtrade.amt(m.get('line'))
        if not bid or line is None:
            continue
        cost = round(1 - bid, 4)
        if best is None or abs(cost - 0.5) < abs(best[1] - 0.5):
            best = (m['slug'], cost, line)
    return best


def _passing(sport, gid, min_db):
    box = json.loads(urllib.request.urlopen(urllib.request.Request(ESPN_SUM[sport].format(id=gid), headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read())
    out = []
    for tm in box['boxscore']['teams']:
        st = {x['name']: x.get('displayValue') for x in tm.get('statistics', [])}
        npy = float(st.get('netPassingYards') or 0)
        att = int(str(st.get('completionAttempts') or '0/0').replace('-', '/').split('/')[1])
        sk = int(str(st.get('sacksYardsLost') or '0-0').split('-')[0])
        if att + sk < min_db:
            return None
        out.append(npy / (att + sk))
    return out if len(out) == 2 else None


def consider(g, ev):
    c = _cfg(); sp = g.get('sport')
    if not c['live'] or sp not in ('nfl', 'cfb') or not pmtrade.have_keys():
        return
    key = f"{sp}|{g['a']}@{g['h']}|{g['start'].date() if hasattr(g.get('start'), 'date') else ''}"
    mt = _main_total(ev)
    if mt and key not in _base:
        _base[key] = mt[2]                                # first main line we see = the market's starting view
    try:
        per = int(g.get('period') or 0); mm = int(str(g.get('clock') or '0:00').split(':')[0])
        pts = int(g.get('sa') or 0) + int(g.get('sh') or 0)
    except ValueError:
        return
    s_cfg = c[sp]
    if per != 1 or mm >= 5 or pts > s_cfg['max_pts'] or not mt:
        return
    p, s = _state()
    spent = s['spent'].get(sp, 0.0)
    if key in s['done'] or spent + 0.5 > s_cfg['day_cap']:
        return
    slug, cost, line = mt
    if key not in _base or _base[key] - line > c['max_line_drop'] or not (c['min_cost'] <= cost <= c['max_cost']):
        return                                            # market already caught on (or we came in too late to know)
    if time.time() - _checked.get(key, 0) < 30:
        return
    _checked[key] = time.time()
    try:
        ypd = _passing(sp, g['id'], c['min_dropbacks'])
    except Exception:
        return
    if not ypd or max(ypd) >= s_cfg['max_ypd']:
        return                                            # someone can throw: not a grind
    try:
        r = pmtrade.buy(slug, False, min(cost + 0.01, c['max_cost']), min(s_cfg['stake'], s_cfg['day_cap'] - spent), size_price=cost)
    except Exception:
        r = dict(ok=False)
    s['done'].append(key)
    if r.get('ok'):
        s['spent'][sp] = spent + r['spent']
        t = os.environ.get('NTFY_TOPIC', '').strip()
        if t:
            try:
                urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{t}', data=(
                    f"{g['a']} {g['sa']}-{g['sh']} {g['h']}, late 1st Q, passing {ypd[0]:.1f}/{ypd[1]:.1f} yds per dropback. "
                    f"Under {line:g} at {r['avg_cost']*100:.0f}c, ${r['spent']:.2f} (line opened {_base[key]:g}).").encode(),
                    headers={'Title': f"Bot bought: {sp.upper()} slow-start Under", 'Priority': 'high', 'Tags': 'football,robot'}), timeout=20)
            except Exception:
                pass
    json.dump(s, open(p, 'w'))
