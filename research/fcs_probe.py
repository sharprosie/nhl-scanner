"""Check ESPN's lower-division (FCS) team list used by FrankenClaude (no trading)."""
import sys, os; sys.path.insert(0, os.getcwd())
import record_all as R
fake = lambda i: {'team': {'id': i}}
print('Alabama (333) FCS?', R._is_fcs('cfb', fake('333')), '| Ohio State (194) FCS?', R._is_fcs('cfb', fake('194')))
print('Yale (43) FCS?', R._is_fcs('cfb', fake('43')), '| Rhode Island (227) FCS?', R._is_fcs('cfb', fake('227')), '| Montana (149) FCS?', R._is_fcs('cfb', fake('149')))
print('FCS teams found:', len(R.FCS_IDS) - 1)
