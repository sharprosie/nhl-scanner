"""Printable card (PDF) from the sections returned by the sport adapters."""
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, KeepTogether

NAVY = colors.HexColor('#1F3864'); GREEN = colors.HexColor('#E3F1E3'); AMBER = colors.HexColor('#FFF1D6'); GREY = colors.HexColor('#F2F2F2')
S = lambda size=9, bold=False, color=colors.black, lead=None: ParagraphStyle('s', fontName='Helvetica-Bold' if bold else 'Helvetica', fontSize=size, leading=lead or size * 1.25, textColor=color)


def money(x):
    return f"${x:,.2f}" if x else '—'


def cents(x):
    return f"{x:.0f}¢" if x not in (None, '') else 'check app'


def build(path, date_label, bankroll, sections, alerts=None):
    doc = SimpleDocTemplate(path, pagesize=landscape(letter), leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.45 * inch, bottomMargin=0.45 * inch,
                            title=f'Super Super Final Unified Betting Model — {date_label}')
    W = landscape(letter)[0] - inch
    el = []
    allb = [b for s in sections for b in s['bets']]
    place = [b for b in allb if b.get('agree') != 'CONFLICT']
    el.append(Paragraph("SUPER SUPER FINAL UNIFIED BETTING MODEL", S(9, True, colors.HexColor('#7F7F7F'))))
    el.append(Paragraph(f"TODAY'S CARD — {date_label}", S(20, True, NAVY, 24)))
    el.append(Paragraph(f"Bankroll {money(bankroll)} · {len(place)} bet(s) to place · total stake {money(sum(b['stake'] or 0 for b in place))} · "
                        f"sports run: {', '.join(s['sport'] for s in sections)}", S(10, False, colors.HexColor('#404040'))))
    for x in (alerts or []):
        el.append(Spacer(1, 3))
        el.append(Paragraph('&#9888; ' + x, S(10.5, True, colors.HexColor('#C00000'))))
    top = [b for b in allb if b.get('agree') == 'AGREE']
    if top:
        el.append(Spacer(1, 4))
        el.append(Paragraph('STRONGEST (both models agree): ' + '; '.join(f"{b['bet']} ({b['game']})" for b in top), S(10, True, colors.HexColor('#1E6B1E'))))
    el.append(Spacer(1, 8))
    cw = [0.3, 1.9, 2.55, 0.65, 0.65, 0.7, W / inch - 6.75]
    for s in sections:
        head = [Paragraph(f"{s['title']}", S(12, True, NAVY))]
        if not s['bets']:
            el.append(KeepTogether(head + [Paragraph(f"No bets — {s.get('scanned') or 0} game(s) checked. Passing is a valid result.", S(9)),
                                           *[Paragraph('• ' + n, S(8, color=colors.HexColor('#555555'))) for n in s['notes']], Spacer(1, 8)]))
            continue
        rows = [[Paragraph(h, S(8, True, colors.white)) for h in ('#', 'Game / time', 'BET — buy exactly this', 'Now', 'Max', 'Stake', 'Why / notes')]]
        style = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#BFBFBF')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                 ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]
        s = dict(s, bets=sorted(s['bets'], key=lambda b: (b.get('agree') == 'CONFLICT', b.get('agree') != 'AGREE', -(b.get('edge') or 0))))
        for i, b in enumerate(s['bets'], 1):
            star = '[BOTH MODELS] ' if b.get('agree') == 'AGREE' else ('[CHECK FIRST] ' if b.get('agree') == 'CHECK' else '')
            betcell = f"<b>{star}{b['bet']}</b>" + (f"<br/><font size=7>{b['alt']}</font>" if b.get('alt') else '')
            why = b['why'] + (f"<br/><font size=7 color='#9C5700'>CHECK: {b['check']}</font>" if b.get('check') else '')
            for wn in (s.get('watch') or {}).get(b['game'], []):
                why += f"<br/><font size=6.5 color='#7F7F7F'>Watch list: {wn}</font>"
            if b.get('agree') == 'CONFLICT':
                why = "<b>Models disagree — PASS (or half stake on the larger edge)</b><br/>" + why
            rows.append([Paragraph(str(i), S(9)), Paragraph(f"{b['game']}<br/><font size=7>{b.get('start','')}</font>", S(9)), Paragraph(betcell, S(10)),
                         Paragraph(cents(b.get('price')), S(9)), Paragraph(f"<b>{cents(b.get('max_price'))}</b>", S(9)), Paragraph(money(b.get('stake')), S(9)),
                         Paragraph(why + f"<br/><font size=7>edge {b['edge']:.1f}{' pts' if s['sport']=='NFL' else ''} · fair {b['fair']:.0f}%</font>", S(8))])
            if b.get('agree') == 'AGREE':
                style.append(('BACKGROUND', (0, i), (-1, i), GREEN))
            elif b.get('agree') in ('CONFLICT', 'CHECK'):
                style.append(('BACKGROUND', (0, i), (-1, i), AMBER))
        t = Table(rows, colWidths=[c * inch for c in cw], repeatRows=1); t.setStyle(TableStyle(style))
        el.extend(head + [Spacer(1, 3), t])
        if s.get('watch'):
            el.append(Paragraph('Watch list (not part of the model, information only): ' + '; '.join(f"{g}: {', '.join(v)}" for g, v in s['watch'].items()), S(7, color=colors.HexColor('#7F7F7F'))))
        for n in s['notes']:
            el.append(Paragraph('• ' + n, S(8, color=colors.HexColor('#555555'))))
        if s.get('board'):
            el.append(Spacer(1, 4))
            el.append(Paragraph(f"First look — every game ({len(s['board'])}), biggest model-vs-Vegas gaps first", S(10, True, NAVY)))
            rows = [[Paragraph(h, S(7.5, True, colors.white)) for h in ('Game / time', 'Vegas', 'Your model', 'Lean', 'Polymarket best contract', 'Code')]]
            stl = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#BFBFBF')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                   ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5)]
            for j, r in enumerate(sorted(s['board'], key=lambda r: -(r.get('gap') or 0)), 1):
                rows.append([Paragraph(f"{r['game']}<br/><font size=6.5>{r.get('start','')}</font>", S(7.5)), Paragraph(r.get('vegas_txt') or '—', S(7.5)),
                             Paragraph(r.get('model_txt') or '—', S(7.5)), Paragraph(f"{r.get('lean') or '—'} ({r.get('strength','')})", S(7.5)),
                             Paragraph(r.get('best') or '—', S(7.5)), Paragraph(f"<b>{r.get('code','')}</b>", S(7.5))])
                if r.get('code') == 'BET':
                    stl.append(('BACKGROUND', (0, j), (-1, j), GREEN))
            tb = Table(rows, colWidths=[2.4 * inch, 1.3 * inch, 1.3 * inch, 1.6 * inch, 2.4 * inch, W - 9.0 * inch], repeatRows=1); tb.setStyle(TableStyle(stl))
            el.append(tb)
        el.append(Spacer(1, 10))
    rules = ["<b>Price:</b> buy at the ask if it is at or below <b>Max</b>; otherwise place a limit 1¢ above the bid. Your Polymarket US app is the true price.",
             "<b>Timing:</b> cancel anything unfilled before the game starts. Prices on this card go stale in about 15 minutes.",
             "<b>Hold to the end.</b> Close a bet only for new information (a starting QB or goalie out) — never because the price swung.",
             "<b>Longshot rule:</b> no moneyline under 25¢ (35¢ hockey). Take the spread or puck line instead.",
             "<b>Never chase:</b> if a line already moved 2+ points toward the pick, the value is gone.",
             "<b>In-game (NFL):</b> if your pick drops 20¢+ below its kickoff price by mid-Q2, your phone alert says buy $5 more."]
    box = Table([[Paragraph('RULES FOR EVERY BET', S(9, True, NAVY))]] + [[Paragraph(r, S(8.5))] for r in rules], colWidths=[W])
    box.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), GREY), ('BOX', (0, 0), (-1, -1), 0.6, NAVY), ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    el.append(KeepTogether([box]))
    el.append(Paragraph('Analysis, not financial advice. Bet only what you can afford to lose.', S(7, False, colors.grey)))
    doc.build(el)
