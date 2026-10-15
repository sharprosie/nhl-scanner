"""Minimal Polymarket US trading client (buy-only). Keys come ONLY from environment variables
POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY (GitHub secrets). Nothing here prints a key.
Auth: Ed25519 signature over f"{timestamp_ms}{METHOD}{path}", headers X-PM-Access-Key / X-PM-Timestamp / X-PM-Signature."""
import base64, json, math, os, time, urllib.request, urllib.error

API = 'https://api.polymarket.us'
GW = 'https://gateway.polymarket.us/v1'


def have_keys():
    return bool(os.environ.get('POLYMARKET_KEY_ID') and os.environ.get('POLYMARKET_SECRET_KEY'))


def _sign(method, path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    ts = str(int(time.time() * 1000))
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['POLYMARKET_SECRET_KEY'])[:32])
    sig = base64.b64encode(key.sign(f'{ts}{method}{path}'.encode())).decode()
    return {'X-PM-Access-Key': os.environ['POLYMARKET_KEY_ID'], 'X-PM-Timestamp': ts, 'X-PM-Signature': sig}


def call(method, path, body=None, timeout=30):
    h = {'Content-Type': 'application/json', 'User-Agent': 'gk-bot/1.0', **_sign(method, path.split('?')[0])}
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b'{}')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'HTTP {e.code}: {e.read()[:300].decode(errors="ignore")}')


def public(path):
    req = urllib.request.Request(GW + path, headers={'User-Agent': 'Mozilla/5.0'})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def amt(x):
    if isinstance(x, dict):
        x = x.get('value')
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def buy(slug, long_side, max_cost, dollars, tick=0.01, dry=False, size_price=None):
    """Buy one side of a market for at most `dollars`, paying no more than `max_cost` (0-1) per contract.
    Immediate-or-cancel: fills what's available at or under the cap right now, cancels the rest.
    Price sent is always the LONG side's price (Polymarket US rule): for the short side it's 1 - cost."""
    cost = math.floor(max_cost / tick + 1e-9) * tick                     # never above the cap
    sp = min(cost, size_price) if size_price else cost                   # size at the real price when known
    qty = math.floor(100 * dollars / (sp + 0.02)) / 100                  # partial contracts (0.01); room for the fee
    if qty < 0.5 or not (0.02 <= cost <= 0.98):
        return dict(ok=False, reason=f'size/price out of range (cost {cost:.2f}, qty {qty})')
    px = cost if long_side else 1 - cost
    body = dict(marketSlug=slug, type='ORDER_TYPE_LIMIT', price=dict(value=f'{px:.3f}', currency='USD'), quantity=qty,
                tif='TIME_IN_FORCE_IMMEDIATE_OR_CANCEL',
                intent='ORDER_INTENT_BUY_LONG' if long_side else 'ORDER_INTENT_BUY_SHORT',
                manualOrderIndicator='MANUAL_ORDER_INDICATOR_AUTOMATIC', synchronousExecution=True, maxBlockTime='5')
    if dry:
        return dict(ok=True, paper=True, qty=qty, filled=qty, avg_cost=cost, spent=qty * cost, body=body)
    try:
        r = call('POST', '/v1/orders', body)
    except RuntimeError as e:                                            # market only takes whole contracts
        if 'HTTP 400' not in str(e) or qty == math.floor(qty) or math.floor(qty) < 1:
            raise
        body['quantity'] = qty = math.floor(qty)
        r = call('POST', '/v1/orders', body)
    ex = r.get('executions') or []
    filled = sum(amt(e.get('lastShares')) or 0 for e in ex if e.get('lastShares'))
    paid_long = sum((amt(e.get('lastShares')) or 0) * (amt(e.get('lastPx')) or 0) for e in ex if e.get('lastShares'))
    o = (ex[-1].get('order') if ex else None) or {}
    if not filled and o.get('cumQuantity'):
        filled = amt(o['cumQuantity']) or 0; paid_long = filled * (amt(o.get('avgPx')) or 0)
    spent = paid_long if long_side else filled - paid_long
    rej = next((e.get('orderRejectReason') or e.get('text') for e in ex if e.get('orderRejectReason')), None)
    return dict(ok=filled > 0, order_id=r.get('id'), qty=qty, filled=filled,
                avg_cost=(spent / filled) if filled else None, spent=spent, reason='' if filled else (rej or 'no fill at or under cap'))
