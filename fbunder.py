"""NFL slow-start Under, v2 (Oct 15, 2026). Late in the 1st quarter (under 5:00 left), if 7 or fewer points AND both teams
are passing under 4 yards per dropback (ESPN live box score), buy the Under on the main live total (line nearest 50c),
only at 40-70c. $2, once per game, $10 a day. History 2012-25: low Q1 + bad passing -> under the pregame total 75% (364 games).
Unproven against LIVE prices; small stakes. Nothing printed about trades."""
import json, os, datetime as dt, urllib.request
import pmtrade
HERE = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(HERE, 'bot_state'); os.makedirs(SD, exist_ok=True)
CFG = dict(live=True, stake=2.0, day_cap=10.0, max_pts=7, max_ypd=4.0, min_dropbacks=6, min_cost=0.40, max_cost=0.70)
ESPN_SUM = 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}'
_checked = {}


def _cfg():
    try:
        return {**CFG, **json.load(open(os.path.join(HERE, 'bot.json'))).get('football_under', {})}
    except Exception:
        return dict(CFG)


def _state():
    p = os.path.join(SD, f"fbunder_{dt.date.today().isoformat()}.json")
    try:
        return p, json.load(open(p))
    except Exception:
        return p, dict(done=[], spent=0.0)


def _amt(x):
    return pmtrade.amt(x)


def consider(g, ev):
    c = _cfg()
    if not c['live'] or not pmtrade.have_keys() or g['sport'] != 'nfl':
        return
    try:
        per = int(g.get('period') or 0); mm = int(str(g.get('clock') or '0:00').split(':')[0])
        pts = int(g.get('sa') or 0) + int(g.get('sh') or 0)
    except ValueError:
        return
    if per != 1 or mm >= 5 or pts > c['max_pts']:
        return                                            # only late in the 1st quarter of a low-scoring game
    p, s = _state(); key = f"{g['sport']}|{g['a']}@{g['h']}"
    if key in s['done'] or s['spent'] + 1 > c['day_cap']:
        return
    import time
    if time.time() - _checked.get(key, 0) < 30:
        return                                            # box score at most every 30 s per game
    _checked[key] = time.time()
    try:                                                  # both teams' passing: net passing yards per dropback
        box = json.loads(urllib.request.urlopen(urllib.request.Request(ESPN_SUM.format(id=g['id']), headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read())
        ypd = []
        for tm in box['boxscore']['teams']:
            st = {x['name']: x.get('displayValue') for x in tm.get('statistics', [])}
            npy = float(st.get('netPassingYards') or 0)
            att = int(str(st.get('completionAttempts') or '0/0').replace('-', '/').split('/')[1])
            sk = int(str(st.get('sacksYardsLost') or '0-0').split('-')[0])
            if att + sk < c['min_dropbacks'] // 2:
                return                                    # too few dropbacks yet to judge
            ypd.append(npy / max(att + sk, 1))
        if len(ypd) != 2 or max(ypd) >= c['max_ypd']:
            return                                        # someone is throwing fine: not a defensive grind
    except Exception:
        return
    best = None
    for m in (ev.get('event', ev)).get('markets', []):
        t = m.get('sportsMarketType') or ''
        if 'full_game' not in t or 'total' not in t or 'team_points' in t:
            continue
        bid = _amt(m.get('bestBidQuote'))
        if not bid:
            continue
        cost = round(1 - bid, 4)                          # Under = short side
        if best is None or abs(cost - 0.5) < abs(best[1] - 0.5):
            best = (m['slug'], cost, m.get('line'))
    if not best or not (c['min_cost'] <= best[1] <= c['max_cost']):
        return
    slug, cost, line = best
    try:
        r = pmtrade.buy(slug, False, min(cost + 0.01, c['max_cost']), min(c['stake'], c['day_cap'] - s['spent']), size_price=cost)
    except Exception:
        s['done'].append(key); json.dump(s, open(p, 'w')); return
    s['done'].append(key)
    if r.get('ok'):
        s['spent'] += r['spent']
        t = os.environ.get('NTFY_TOPIC', '').strip()
        if t:
            try:
                urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{t}', data=(
                    f"{g['a']} {g['sa']}-{g['sh']} {g['h']}, early 2nd quarter. Under {line} at {r['avg_cost']*100:.0f}c, ${r['spent']:.2f}.").encode(),
                    headers={'Title': 'Bot bought: slow-start Under', 'Priority': 'high', 'Tags': 'football,robot'}), timeout=20)
            except Exception:
                pass
    json.dump(s, open(p, 'w'))
