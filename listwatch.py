"""Listing watcher (Oct 16, 2026): when does Polymarket US first post next week's NFL and college football games?
Opening lines are softest, so the card bots should bet as soon as games are listed. Every 30 minutes this checks every
NFL/college game in the next 16 days (ESPN schedule) and records the first time its Polymarket market exists, plus the
opening moneyline price. Market data only; nothing about trades. Output: out/listings.csv"""
import csv, os, datetime as dt
import record_all as R

P = os.path.join(R.OUT, 'listings.csv')
COLS = ['first_seen_utc', 'first_seen_pt', 'sport', 'game', 'kickoff_pt', 'slug', 'away_bid', 'away_ask']


def main():
    seen = set()
    if os.path.exists(P):
        seen = {(r['sport'], r['game'], r['kickoff_pt'][:10]) for r in csv.DictReader(open(P))}
    now = dt.datetime.now(dt.timezone.utc); new = []
    for sp in ('nfl', 'cfb'):
        for g in R.games(sp, now, ahead=16).values():
            k = (sp, f"{g['a']}@{g['h']}", g['start'].astimezone(R.PT).isoformat()[:10])
            if g['state'] != 'pre' or k in seen:
                continue
            ev = R.event(g)
            if not ev:
                continue
            bid = ask = ''
            for m in ev.get('event', ev).get('markets', []):
                t = m.get('sportsMarketType') or ''
                if 'full_game' in t and 'winner' in t:
                    bid, ask = R.pmtrade_amt(m.get('bestBidQuote')), R.pmtrade_amt(m.get('bestAskQuote')); break
            new.append([now.isoformat(timespec='minutes'), now.astimezone(R.PT).strftime('%a %Y-%m-%d %H:%M'), sp, k[1],
                        g['start'].astimezone(R.PT).isoformat(timespec='minutes'), R.SLUG.get((g['sport'], g['a'], g['h'], g['start'].date()), ''), bid, ask])
    if new:
        first = not os.path.exists(P)
        with open(P, 'a', newline='') as f:
            w = csv.writer(f)
            if first:
                w.writerow(COLS)
            w.writerows(new)
    print(f'{len(new)} newly listed games')


if __name__ == '__main__':
    main()
