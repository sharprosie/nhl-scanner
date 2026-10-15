"""Soccer: Polymarket US price vs Pinnacle's sharp fair price (the 'stale price' edge).
Backtest (football-data.co.uk, 10 leagues, 2019-25): betting a soft book's EARLY price when it beat the sharp
book's fair price by 4%+ returned about +12% (748 bets); by kickoff the gap was gone. So: bet early, never late."""
import json, re, unicodedata, datetime as dt, urllib.request

PIN = {'epl': 1980, 'lal': 2196, 'bun': 1842, 'sea': 2436, 'lg1': 2036, 'ucl': 2627, 'uel': 2630, 'uecl': 214101}
NAMES = {'epl': 'Premier League', 'lal': 'La Liga', 'bun': 'Bundesliga', 'sea': 'Serie A', 'lg1': 'Ligue 1',
         'ucl': 'Champions League', 'uel': 'Europa League', 'uecl': 'Conference League'}
PH = {'User-Agent': 'Mozilla/5.0', 'X-Api-Key': 'CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R', 'Referer': 'https://www.pinnacle.com/', 'Accept': 'application/json'}
GW = 'https://gateway.polymarket.us/v1'
FEE = lambda c: 0.05 * c * (1 - c)
MIN_EV, MAX_SPREAD, MIN_PRICE = 0.04, 0.05, 0.25


def _get(url, hdr=None):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=hdr or {'User-Agent': 'Mozilla/5.0'}), timeout=40).read())


def _dec(american):
    return 1 + (american / 100 if american > 0 else 100 / -american)


def _norm(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower().replace('&', ' and ')
    s = re.sub(r'\b(fc|afc|cf|sc|ac|as|ssc|club|de|the|calcio|1\.)\b', ' ', s)
    return set(re.sub(r'[^a-z0-9 ]', ' ', s).split())


def _sim(a, b):
    A, B = _norm(a), _norm(b)
    return len(A & B) / max(1, min(len(A), len(B)))


def pinnacle(league_id):
    """Return list of dicts: home, away, start (UTC datetime), fair {'home','draw','away','over','under'}."""
    M = _get(f'https://guest.api.arcadia.pinnacle.com/0.1/leagues/{league_id}/matchups', PH)
    K = _get(f'https://guest.api.arcadia.pinnacle.com/0.1/leagues/{league_id}/markets/straight', PH)
    out = {}
    for m in M:
        if m.get('type') != 'matchup' or m.get('parent') or not m.get('participants'):
            continue
        al = {p.get('alignment'): p for p in m['participants']}
        if 'home' not in al or 'away' not in al:
            continue
        out[m['id']] = dict(home=al['home']['name'], away=al['away']['name'], start=m.get('startTime'),
                            pid={p.get('id'): p.get('alignment') for p in m['participants']}, fair={})
    for k in K:
        g = out.get(k.get('matchupId'))
        per = k.get('period')
        if not g or per not in (0, 1):
            continue
        key = k.get('key', '')
        if key == f's;{per};m' and per == 0:
            px = {}
            for p in k['prices']:
                side = p.get('designation') or g['pid'].get(p.get('participantId'))
                if side in ('home', 'away', 'draw'):
                    px[side] = 1 / _dec(p['price'])
            if len(px) == 3:
                t = sum(px.values()); g['fair'].update({('ml', sd): v / t for sd, v in px.items()})
        elif key.startswith(f's;{per};ou;'):
            px = {p['designation']: 1 / _dec(p['price']) for p in k['prices'] if p.get('designation') in ('over', 'under')}
            line = float(key.split(';')[3])
            if len(px) == 2 and line % 1 == 0.5:
                t = sum(px.values()); g['fair'].update({('ou', per, line, sd): v / t for sd, v in px.items()})
        elif key.startswith(f's;{per};s;'):
            hp = [p for p in k['prices'] if p.get('designation') == 'home']
            h = float(hp[0]['points']) if hp and hp[0].get('points') is not None else float(key.split(';')[3])
            px = {p['designation']: 1 / _dec(p['price']) for p in k['prices'] if p.get('designation') in ('home', 'away')}
            if len(px) == 2 and abs(h) % 1 == 0.5:
                t = sum(px.values()); g['fair'].update({('sp', per, h, sd): v / t for sd, v in px.items()})
    return [g for g in out.values() if g['fair']]


def poly_events():
    evs = []
    for off in range(0, 4000, 200):
        try:
            d = _get(f'{GW}/events?limit=200&offset={off}&active=true&closed=false')
        except Exception:
            break
        e = d.get('events', []) if isinstance(d, dict) else d
        if not e:
            break
        evs += [x for x in e if re.match(r'^(' + '|'.join(PIN) + r')-[a-z0-9]+-[a-z0-9]+-\d{4}-\d\d-\d\d$', x.get('slug', ''))]
    return evs


def bbo(slug):
    try:
        m = _get(f'{GW}/markets/{slug}/bbo')['marketData']
        b = float(m['bestBid']['value']) if m.get('bestBid') else None
        a = float(m['bestAsk']['value']) if m.get('bestAsk') else None
        return b, a, float(m.get('bidShares') or 0), float(m.get('askShares') or 0)
    except Exception:
        return None, None, 0, 0


def contracts(e, h_name, a_name):
    """Map each Polymarket market in the event to (fair-key, label) for its YES and NO sides."""
    sl = e['slug']; hc, ac = sl.split('-')[1], sl.split('-')[2]
    out = []
    for m in e.get('markets', []):
        ms = m.get('slug', '')
        tail = ms[len(sl) + 1 + ms.index(sl):] if sl in ms else ''
        per = 1 if tail.startswith('fh-') else 0
        if tail.startswith('sh-'):
            continue
        t = tail[3:] if per else tail
        half = ' (1st half)' if per else ''
        if ms.startswith('atc-') and per == 0 and t in (hc, 'draw', ac):
            sd = 'home' if t == hc else ('away' if t == ac else 'draw')
            lab = f'{h_name} to win' if sd == 'home' else (f'{a_name} to win' if sd == 'away' else 'Draw')
            out.append((ms, ('ml', sd), lab, None, None))
        elif ms.startswith('tsc-') and re.fullmatch(r'\d+pt5', t):
            L = float(t.replace('pt', '.'))
            out.append((ms, ('ou', per, L, 'over'), f'Over {L:g} goals{half}', ('ou', per, L, 'under'), f'Under {L:g} goals{half}'))
        elif ms.startswith('asc-') and re.fullmatch(r'(neg|pos)-\d+pt5', t):
            L = float(t.split('-')[1].replace('pt', '.')) * (-1 if t.startswith('neg') else 1)
            out.append((ms, ('sp', per, L, 'home'), f'{h_name} {L:+g}{half}', ('sp', per, L, 'away'), f'{a_name} {-L:+g}{half}'))
    return out


def _pt(iso):
    try:
        t = dt.datetime.fromisoformat(iso.replace('Z', '+00:00')) - dt.timedelta(hours=7)
        return t.strftime('%a %b %d, %I:%M %p PT').replace(' 0', ' ')
    except Exception:
        return iso or ''


def run(bankroll, today=None):
    bets, notes, scanned = [], [], 0
    today = today or dt.datetime.utcnow()
    evs = poly_events()
    by_lg = {}
    for e in evs:
        by_lg.setdefault(e['slug'].split('-')[0], []).append(e)
    for lg, E in by_lg.items():
        try:
            P = pinnacle(PIN[lg])
        except Exception as ex:
            notes.append(f'{NAMES[lg]}: Pinnacle prices unavailable ({ex})'); continue
        for e in E:
            start = e.get('startDate') or e.get('gameStartTime') or ''
            try:
                sdt = dt.datetime.fromisoformat(start.replace('Z', '+00:00')).replace(tzinfo=None)
            except Exception:
                continue
            if sdt <= today or sdt > today + dt.timedelta(days=8):
                continue
            title = e.get('title', '')
            if ' vs' not in title:
                continue
            h_name, a_name = [x.strip(' .') for x in re.split(r'\s+vs\.?\s+', title, maxsplit=1)]
            best, score = None, 0
            for g in P:
                try:
                    gs = dt.datetime.fromisoformat(g['start'].replace('Z', '+00:00')).replace(tzinfo=None)
                except Exception:
                    continue
                if abs((gs - sdt).total_seconds()) > 36 * 3600:
                    continue
                sc = _sim(h_name, g['home']) + _sim(a_name, g['away'])
                if sc > score:
                    best, score = g, sc
            if not best or score < 1.0:
                notes.append(f'{title}: Pinnacle has no odds posted yet'); continue
            scanned += 1
            cands = []
            for ms, kyes, lyes, kno, lno in contracts(e, h_name, a_name):
                if kyes not in best['fair'] and (kno is None or kno not in best['fair']):
                    continue
                b, a, bs, as_ = bbo(ms)
                if b is None or a is None or a - b > MAX_SPREAD:
                    continue
                for key, lab, cost, depth in ((kyes, lyes, a, as_), (kno, lno, (1 - b) if b is not None else None, bs)):
                    q = best['fair'].get(key) if key else None
                    if q is None or cost is None or cost < MIN_PRICE or depth < 15:
                        continue
                    ev = q / (cost + FEE(cost)) - 1
                    cands.append((ev, key, lab, cost, q, q / (1 + MIN_EV) - 0.0125))
            if not cands:
                continue
            ev, side, label, cost, q, cmax = max(cands, key=lambda c: c[0])
            if ev < MIN_EV:
                continue
            stake = bankroll * (0.01 if ev >= 0.08 else 0.005)
            bets.append(dict(sport='SOCCER', game=f'{h_name} vs {a_name} ({NAMES[lg]})', start=_pt(start), bet=label,
                             price=cost * 100, max_price=cmax * 100, fair=q * 100, edge=ev * 100, stake=stake, agree='',
                             side=str(side), kind={'ml': 'ml', 'ou': 'total', 'sp': 'spread'}[side[0]],
                             why=f"Pinnacle fair {q*100:.0f}% vs Polymarket {cost*100:.0f}c: +{ev*100:.1f}% value after fee", alt='', check=''))
    bets.sort(key=lambda b: -b['edge'])
    notes.append(f'{scanned} matches priced against Pinnacle (next 8 days). Rule: bet when Polymarket is 4%+ better than the sharp fair price; bet early, the gap closes by kickoff.')
    return dict(sport='SOCCER', title='Soccer — Polymarket vs sharp price (Pinnacle)', bets=bets, scanned=scanned, notes=notes)
