"""Big club right after a league LOSS (backtest Oct 15, 2026: +5.6% at Pinnacle closing odds, 1,790 games, every era positive).
Rule: a marquee club playing a non-marquee team in its domestic league, favored (Pinnacle fair > 45%), whose previous
league game was a loss. Buy it to win on Polymarket only at or under Pinnacle's fair price after the fee.
Previous results come from football-data.co.uk's current-season files (updated about twice a week)."""
import csv, io, datetime as dt, urllib.request
import soccer as S

LEAGUE_FILE = {'epl': 'E0', 'lal': 'SP1', 'bun': 'D1', 'sea': 'I1', 'lg1': 'F1'}     # domestic leagues only (as tested)
BIG = {  # football-data name: names Polymarket / Pinnacle may use
    'Man United': ['Manchester United', 'Man United', 'Man Utd'], 'Liverpool': ['Liverpool'], 'Arsenal': ['Arsenal'],
    'Chelsea': ['Chelsea'], 'Man City': ['Manchester City', 'Man City'], 'Tottenham': ['Tottenham Hotspur', 'Tottenham', 'Spurs'],
    'Real Madrid': ['Real Madrid'], 'Barcelona': ['Barcelona'], 'Ath Madrid': ['Atletico Madrid', 'Atlético Madrid', 'Atletico de Madrid'],
    'Bayern Munich': ['Bayern Munich', 'Bayern München', 'Bayern'], 'Dortmund': ['Borussia Dortmund', 'Dortmund'],
    'Juventus': ['Juventus'], 'Milan': ['AC Milan', 'Milan'], 'Inter': ['Inter Milan', 'Inter', 'Internazionale'],
    'Napoli': ['Napoli'], 'Roma': ['Roma', 'AS Roma'], 'Paris SG': ['Paris Saint-Germain', 'PSG', 'Paris SG'],
    'Marseille': ['Marseille', 'Olympique Marseille', 'Olympique de Marseille']}


def big_name(name):
    n = S._norm(name)
    for fd, al in BIG.items():
        if any(S._norm(a) == n for a in al):
            return fd
    return None


def season_code(today):
    y = today.year if today.month >= 7 else today.year - 1
    return f'{y % 100:02d}{(y + 1) % 100:02d}'


def last_results(div, today):
    """{team: (date, goals_for, goals_against)} of each team's most recent league game in the current season."""
    raw = urllib.request.urlopen(urllib.request.Request(f'https://www.football-data.co.uk/mmz4281/{season_code(today)}/{div}.csv',
                                 headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read().decode('latin-1')
    out = {}
    for r in csv.DictReader(io.StringIO(raw.lstrip('﻿'))):
        try:
            d = dt.datetime.strptime(r['Date'], '%d/%m/%Y' if len(r['Date']) == 10 else '%d/%m/%y').date()
            hg, ag = int(r['FTHG']), int(r['FTAG'])
        except (KeyError, ValueError):
            continue
        for t, gf, ga in ((r.get('HomeTeam'), hg, ag), (r.get('AwayTeam'), ag, hg)):
            if t and (t not in out or d > out[t][0]):
                out[t] = (d, gf, ga)
    return out


def find(today=None):
    """Return candidate bets: dict(game, bet, slug, fair, cap, price, start)."""
    today = today or dt.datetime.utcnow()
    evs = [e for e in S.poly_events() if e['slug'].split('-')[0] in LEAGUE_FILE]
    bets, notes = [], []
    by_lg = {}
    for e in evs:
        by_lg.setdefault(e['slug'].split('-')[0], []).append(e)
    for lg, E in by_lg.items():
        try:
            P = S.pinnacle(S.PIN[lg]); R = last_results(LEAGUE_FILE[lg], today)
        except Exception as ex:
            notes.append(f'{lg}: data unavailable ({type(ex).__name__})'); continue
        notes.append(f"{lg}: {len(E)} events, {len(R)} teams with results, big clubs whose last league game was a loss: {sum(1 for t in BIG if t in R and R[t][1] < R[t][2])}")
        starts = {}
        for e in E:                                   # each team's upcoming league games, to know which is next
            try:
                sdt = dt.datetime.fromisoformat((e.get('startDate') or e.get('gameStartTime') or '').replace('Z', '+00:00')).replace(tzinfo=None)
            except ValueError:
                continue
            for nm in S.re.split(r'\s+vs\.?\s+', e.get('title', ''), maxsplit=1):
                starts.setdefault(frozenset(S._norm(nm)), []).append(sdt)
        for e in E:
            title = e.get('title', '')
            if ' vs' not in title:
                continue
            try:
                sdt = dt.datetime.fromisoformat((e.get('startDate') or e.get('gameStartTime') or '').replace('Z', '+00:00')).replace(tzinfo=None)
            except ValueError:
                continue
            if sdt <= today or sdt > today + dt.timedelta(days=8):
                if any(big_name(n) for n in S.re.split(r'\s+vs\.?\s+', title, maxsplit=1)): notes.append(f'{title}: outside the next 8 days')
                continue
            h_name, a_name = [x.strip(' .') for x in S.re.split(r'\s+vs\.?\s+', title, maxsplit=1)]
            for side, me, opp in (('home', h_name, a_name), ('away', a_name, h_name)):
                fd = big_name(me)
                if not fd or big_name(opp):
                    continue
                if any(t < sdt for t in starts.get(frozenset(S._norm(me)), [])):
                    notes.append(f'{title}: {me} has an earlier league game'); continue                          # another league game comes first; its result decides
                last = R.get(fd)
                if not last or last[1] >= last[2] or (sdt.date() - last[0]).days > 21:
                    continue                          # previous league game wasn't a loss (or results file is stale; 21 days allows for international breaks)
                best, score = None, 0
                for g in P:
                    try:
                        gs = dt.datetime.fromisoformat(g['start'].replace('Z', '+00:00')).replace(tzinfo=None)
                    except Exception:
                        continue
                    if abs((gs - sdt).total_seconds()) <= 36 * 3600:
                        sc = S._sim(h_name, g['home']) + S._sim(a_name, g['away'])
                        if sc > score:
                            best, score = g, sc
                if not best or score < 1.0:
                    notes.append(f'{title}: no Pinnacle match'); continue
                q = best['fair'].get(('ml', side))
                if not q or q <= 0.45:
                    notes.append(f'{title}: {me} not favored enough ({(q or 0)*100:.0f}%)'); continue
                slug = next((ms for ms, k, *_ in S.contracts(e, h_name, a_name) if k == ('ml', side)), None)
                if not slug:
                    notes.append(f'{title}: no Polymarket moneyline found'); continue
                b, a, bs, as_ = S.bbo(slug)
                cap = q - S.FEE(q)                     # pay no more than Pinnacle's fair price after the fee
                bets.append(dict(game=f'{h_name} vs {a_name} ({S.NAMES[lg]})', bet=f'{me} to win', slug=slug, fair=q, cap=cap,
                                 price=a, start=S._pt(e.get('startDate') or ''), buyable=bool(a and a <= cap and as_ >= 5)))
        for t in BIG:
            if t in R and R[t][1] < R[t][2]:
                hit = [e.get('title', '') for e in E if any(big_name(n) == t for n in S.re.split(r'\s+vs\.?\s+', e.get('title', ''), maxsplit=1))]
                notes.append(f"{lg}: {t} lost {R[t][1]}-{R[t][2]} on {R[t][0]}; next on Polymarket: {hit or 'not matched'}")
    return bets, notes
