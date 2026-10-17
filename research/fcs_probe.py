import sys
sys.stdout = open('research/fcs_probe_log.txt', 'w')
"""Check ESPN's lower-division (FCS) team list used by FrankenClaude (no trading)."""
import sys, os; sys.path.insert(0, os.getcwd())
import types, json, urllib.request
R = types.SimpleNamespace()
def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read())
ns = dict(get=get)
exec('def _is_fcs(sport, comp):\n    """True for a lower-division (FCS) team playing in an FBS game (ESPN marks FBS teams with a top-division conference)."""\n    if sport != \'cfb\':\n        return False\n    if not FCS_IDS:\n        try:\n            sb = get(\'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?groups=81&limit=500\')\n            for lg in sb.get(\'sports\', [{}])[0].get(\'leagues\', [{}]):\n                for t in lg.get(\'teams\', []):\n                    FCS_IDS.add(str(t[\'team\'][\'id\']))\n        except Exception:\n            pass\n        FCS_IDS.add(\'-\')                                                    # don\'t retry every poll\n    return str(comp[\'team\'].get(\'id\')) in FCS_IDS\n\n\nFCS_IDS = set()\n', ns)
R._is_fcs = ns['_is_fcs']; R.FCS_IDS = ns['FCS_IDS']
fake = lambda i: {'team': {'id': i}}
print('Alabama (333) FCS?', R._is_fcs('cfb', fake('333')), '| Ohio State (194) FCS?', R._is_fcs('cfb', fake('194')))
print('Yale (43) FCS?', R._is_fcs('cfb', fake('43')), '| Rhode Island (227) FCS?', R._is_fcs('cfb', fake('227')), '| Montana (149) FCS?', R._is_fcs('cfb', fake('149')))
print('FCS teams found:', len(R.FCS_IDS) - 1)

