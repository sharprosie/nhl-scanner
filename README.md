# NHL near-lock scanner
Watches every live NHL game in the 3rd period about every 20 seconds and sends a phone alert (ntfy) when a
near-certain side (real chance 85%+) is selling on Polymarket US at least 5c under its real chance.
Covers moneylines, puck lines and game totals. Skips power plays, overtime and the 1st/2nd periods.

- `nearlock.py` — the scanner and the late-game scoring model
- `.github/workflows/scan.yml` — runs on game nights (free: public repo)
- `out/nearlock_log.csv` — every alert, graded automatically after the game
- `out/nearlock_snapshots.csv` — every price 3c+ under the real chance (research)

**Acting on an alert:** buy up to the price in the alert, $5 test stake, hold to the end. Skip it if a penalty
was just called or the score changed. Paying 85c to win 15c means one freak comeback costs about 6 wins.

Settings: repository secret `NTFY_TOPIC`; optional repository variable `NL_MODE` = `PAPER` to log without alerts.
