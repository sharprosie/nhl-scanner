"""Check how to spot lower-division (FCS) teams in ESPN's top-division scoreboard (no trading)."""
import sys, json, urllib.request
sys.stdout = open('research/fcs_probe_log.txt', 'w')
get = lambda u: json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read())
seen = {}
for d in ('20261017', '20260905', '20260912'):
    sb = get(f'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?dates={d}&groups=80&limit=300')
    for ev in sb['events']:
        for c in ev['competitions'][0]['competitors']:
            t = c['team']; seen[t.get('abbreviation')] = (t.get('conferenceId'), t.get('displayName'))
from collections import Counter
print('teams', len(seen)); print('conferenceIds', Counter(v[0] for v in seen.values()).most_common(40))
for k in ('ALA', 'OSU', 'YALE', 'URI', 'MONT', 'ND', 'UCONN', 'ARMY'):
    print(k, seen.get(k))
