"""Hourly soccer price check (Polymarket US vs Pinnacle). Phone alert with a printable PDF only when a NEW bet appears.
Public repo rule: nothing about the bets is printed to the log or saved in the repo. The alert goes only to your
private ntfy topic; the 'already alerted' list lives in the Actions cache.
Stake sizes use the BANKROLL secret if set; otherwise the card shows stakes per $100 of bankroll."""
import os, sys, json, argparse, datetime as dt, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import soccer, card_pdf

ap = argparse.ArgumentParser()
ap.add_argument('--quiet', action='store_true', help='no phone alert, nothing marked as alerted')
ap.add_argument('--seed', action='store_true', help='mark current bets as already alerted, no phone alert')
a = ap.parse_args()
try:
    bankroll = float(os.environ.get('BANKROLL') or 100)
except ValueError:
    bankroll = 100.0
state = os.path.join(HERE, 'state'); os.makedirs(state, exist_ok=True)
seen_p = os.path.join(state, 'soccer_alerted.json')
try:
    seen = json.load(open(seen_p))
except Exception:
    seen = {}
now = dt.datetime.utcnow()
seen = {k: v for k, v in seen.items() if (now - dt.datetime.fromisoformat(v.split('|')[0])).days < 10}
sec = soccer.run(bankroll)
key = lambda b: f"{b['game']}|{b['bet']}"
new = [b for b in sec['bets'] if key(b) not in seen]
print(f"soccer check done: {len(new)} new alert(s)")          # counts only; public log


def push(t, data, headers):
    urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{t}", data=data, headers=headers,
                                                  method='PUT' if 'Filename' in headers else 'POST'), timeout=60)


if new and not a.quiet and not a.seed:
    t = os.environ.get('NTFY_TOPIC', '').strip()
    pt = now - dt.timedelta(hours=7)
    pdf = os.path.join(state, f"soccer_{pt:%Y-%m-%d_%H%M}.pdf")
    card_pdf.build(pdf, pt.strftime('%A %b %d, %Y %I:%M %p PT'), bankroll,
                   [dict(sec, bets=new, title='Soccer bet alert — Polymarket vs Pinnacle')])
    title = f"Soccer bet: {len(new)} new"
    msg = "\n".join(f"{b['game']}: {b['bet']} at {b['price']:.0f}c (max {b['max_price']:.0f}c)" for b in new)
    try:
        if not t:
            raise RuntimeError('no NTFY_TOPIC')
        push(t, msg.encode(), {'Title': title, 'Tags': 'soccer', 'Priority': 'high'})
        with open(pdf, 'rb') as f:
            push(t, f.read(), {'Filename': os.path.basename(pdf), 'Title': title + ' (printable PDF)'})
        os.remove(pdf)
    except Exception as e:
        print('::warning title=soccer::phone alert failed'); new = []     # retry next hour
if not a.quiet:
    for b in new:
        seen[key(b)] = now.isoformat()
json.dump(seen, open(seen_p, 'w'))

# ---- Big club right after a league loss: buy automatically (small, capped), ping the phone. Nothing printed about bets.
try:
    sys.path.insert(0, os.path.dirname(HERE)); import bigclub, pmtrade
    try:
        cfg = json.load(open(os.path.join(os.path.dirname(HERE), 'bot.json')))
    except Exception:
        cfg = {}
    stake, day_cap = float(cfg.get('soccer_big_stake', 3)), float(cfg.get('soccer_big_day_cap', 9))
    live = bool(cfg.get('soccer_big_live', True)) and pmtrade.have_keys() and not a.quiet and not a.seed
    found, bnotes = bigclub.find(now)
    print('::notice title=big-club data::' + ' / '.join(bnotes))
    today_key = now.strftime('%Y-%m-%d')
    spent_today = sum(float(v.split('|')[1]) for k, v in seen.items() if k.startswith('bigspent|' + today_key))
    done = 0
    for b in found:
        k = 'big|' + b['slug']
        if k in seen or not b['buyable']:
            continue
        if a.seed:
            seen[k] = now.isoformat(); continue
        dollars = min(b.get('stake') or stake, day_cap - spent_today)
        if dollars < 1 or not live:
            continue
        try:
            r = pmtrade.buy(b['slug'], True, b['cap'], dollars)
        except Exception:
            r = dict(ok=False)
        if r.get('ok'):
            seen[k] = now.isoformat(); spent_today += r['spent']; done += 1
            seen[f'bigspent|{today_key}|{b["slug"]}'] = f"{now.isoformat()}|{r['spent']:.2f}"
            t = os.environ.get('NTFY_TOPIC', '').strip()
            if t:
                push(t, (f"{b['game']}: {b['bet']} at {r['avg_cost']*100:.0f}c, ${r['spent']:.2f}. "
                         f"Big club after a league loss; Pinnacle fair {b['fair']*100:.0f}%.").encode(),
                     {'Title': 'Bot bought: soccer big-club bet', 'Tags': 'soccer,robot', 'Priority': 'high'})
    print(f"::notice title=big-club::{len(found)} candidate(s), {sum(b['buyable'] for b in found)} at a buyable price, {done} bought")
except Exception as e:
    print('::warning title=big-club::check failed ' + type(e).__name__)
json.dump(seen, open(seen_p, 'w'))
