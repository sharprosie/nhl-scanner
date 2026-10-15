"""In-game near-lock BOT: buys a near-lock automatically when the scanner sees it, with hard limits.
Rules (bot.json can turn it off or change sizes):
  - moneylines and puck lines only; real chance 85%+; price at least 6c under the real chance; never over 97c
  - the gap must show on two checks in a row (~2 seconds apart) with the same score
  - if the price just fell 10c+ in the last minute, wait until that drop is a minute old (same reason)
  - $2 a trade, $4 a game, $10 a night; one buy per market per night; buy-only, immediate-or-cancel
  - stops for the night after 3 losses in a row, $8 net loss, 3 order errors, or a rejected key
Public repo rule: nothing about trades is printed or committed. State lives in bot_state/ (Actions cache only);
each trade goes to your phone."""
import json, os, time, urllib.request, datetime as dt
try:
    import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); import quiethours  # 10 PM-7 AM pings are silent
except Exception:
    pass
from zoneinfo import ZoneInfo
import pmtrade

HERE = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(HERE, 'bot_state'); os.makedirs(SD, exist_ok=True)
PT = ZoneInfo('America/Los_Angeles')
DEF = dict(live=False, per_trade=2.0, per_game=4.0, per_night=10.0, min_gap=0.06, min_fair=0.85, max_cost=0.97,
           hold_secs=1.5, poll_secs=2, drop=0.10, drop_wait=60, max_losses_row=3, max_night_loss=8.0, max_errors=3)


class Bot:
    def __init__(self, today):
        try:
            self.c = {**DEF, **json.load(open(os.path.join(HERE, 'bot.json')))}
        except Exception:
            self.c = dict(DEF)
        self.today = today; self.path = os.path.join(SD, f'trades_{today}.json')
        try:
            self.s = json.load(open(self.path))
        except Exception:
            self.s = dict(trades=[], errors=0, halted='')
        self.seen = {}      # (slug, side) -> (first time gap seen, score)
        self.hist = {}      # (slug, side) -> [(t, cost)]
        self.enabled = bool(self.c['live']) and pmtrade.have_keys()

    def save(self):
        json.dump(self.s, open(self.path, 'w'))

    def ping(self, title, msg, prio='high'):
        t = os.environ.get('NTFY_TOPIC', '').strip()
        if not t:
            return
        try:
            urllib.request.urlopen(urllib.request.Request(f'https://ntfy.sh/{t}', data=msg.encode(),
                                   headers={'Title': title, 'Priority': prio, 'Tags': 'robot'}), timeout=20)
        except Exception:
            pass

    def halt(self, why):
        if not self.s['halted']:
            self.s['halted'] = why; self.save(); self.ping('Bot stopped for tonight', why, 'max')

    def grade(self, finals):
        """finals: {game: (away, home, away_score, home_score)} for finished games. Updates results, checks stop rules."""
        for tr in self.s['trades']:
            if tr.get('result') or tr['game'] not in finals:
                continue
            A, H, a, h = finals[tr['game']]
            marg = (h - a) if tr['team'] == H else (a - h)
            tr['result'] = 'W' if marg + tr['handicap'] > 0 else 'L'
            tr['pnl'] = round(tr['filled'] - tr['spent'], 2) if tr['result'] == 'W' else -round(tr['spent'], 2)
        done = [t for t in self.s['trades'] if t.get('result')]
        row = 0
        for t in done:
            row = row + 1 if t['result'] == 'L' else 0
        net = sum(t['pnl'] for t in done)
        if row >= self.c['max_losses_row']:
            self.halt(f'{row} losses in a row.')
        elif net <= -self.c['max_night_loss']:
            self.halt(f'Down ${-net:.2f} tonight.')
        self.save()

    def consider(self, game, m, side, team, handicap, fair, cost, score, label):
        """Called every scan for a near-lock side. Returns True if the bot bought it (so skip the manual alert)."""
        if not self.enabled or self.s['halted'] or m['kind'] != 'side':
            return False
        c, now, key = self.c, time.time(), (m['slug'], side)
        h = [x for x in self.hist.get(key, []) if now - x[0] <= 90] + [(now, cost)]
        self.hist[key] = h
        gap = fair - cost
        if fair < c['min_fair'] or gap < c['min_gap'] or cost > c['max_cost']:
            self.seen.pop(key, None); return False
        first = self.seen.get(key)
        if not first or first[1] != score:
            self.seen[key] = (now, score); return False
        if now - first[0] < c['hold_secs']:
            return False
        recent = [x for x in h if now - x[0] <= c['drop_wait']]
        if recent and max(x[1] for x in recent) - cost >= c['drop']:
            return False                                         # price just crashed: let the scoreboard catch up
        T = self.s['trades']
        if any(t['slug'] == m['slug'] and t['side'] == side for t in T):
            return True                                          # already bought this one tonight
        spent_game = sum(t['spent'] for t in T if t['game'] == game)
        spent_night = sum(t['spent'] for t in T)
        dollars = min(c['per_trade'], c['per_game'] - spent_game, c['per_night'] - spent_night)
        if dollars < 0.5:
            return False
        cap = min(fair - c['min_gap'], c['max_cost'])
        try:
            r = pmtrade.buy(m['slug'], side == 'long', cap, dollars, size_price=cost)
        except Exception as e:
            self.s['errors'] += 1; self.save()
            if 'HTTP 401' in str(e) or 'HTTP 403' in str(e):
                self.halt('Polymarket rejected the API key.')
            elif self.s['errors'] >= c['max_errors']:
                self.halt(f"{self.s['errors']} order errors.")
            return False
        if not r.get('ok'):
            return False                                         # nothing at or under the cap: price moved; keep alerting
        T.append(dict(time=dt.datetime.now(PT).isoformat(timespec='seconds'), game=game, slug=m['slug'], side=side,
                      team=team, handicap=handicap, label=label, fair=round(fair, 3), cap=round(cap, 3),
                      filled=r['filled'], spent=round(r['spent'], 2), avg=round(r['avg_cost'], 3), order=r.get('order_id')))
        self.save()
        self.ping(f'Bot bought: {label}', f"{score}. {label} at {r['avg_cost']*100:.0f}c, ${r['spent']:.2f} "
                                          f"(real chance {fair*100:.0f}%). Tonight: ${spent_night + r['spent']:.2f} of ${c['per_night']:.0f}.")
        return True

    def summary(self):
        T = self.s['trades']
        if not T or self.s.get('summarized'):
            return
        self.s['summarized'] = 1; self.save()
        w = sum(t.get('result') == 'W' for t in T); l = sum(t.get('result') == 'L' for t in T)
        net = sum(t.get('pnl', 0) for t in T)
        self.ping('Bot night summary', f"{len(T)} trade(s): {w}W {l}L, {len(T)-w-l} open. Net ${net:+.2f}.", 'default')
