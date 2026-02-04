# China Fundamental Health Index (CFHI) — Working Draft

## Purpose
Simple, repeatable fundamental outlook for China. Output: one of
- Går starkt
- Ser bra ut
- Neutral / saktar till
- Oroligt
- Dåligt

## Blocks (no weights)
Each block gets a status: Bra / Neutral / Svag, based on latest 2–3 official releases.

1) Consumption (Retail sales)
- Bra: > 5% YoY for 2 consecutive months
- Neutral: 2–5% YoY or mixed (one month up, one down)
- Svag: < 2% YoY for 2 consecutive months or negative

2) Industry (Industrial production)
- Bra: > 6% YoY for 2 consecutive months
- Neutral: 3–6% YoY
- Svag: < 3% YoY for 2 consecutive months

3) Property (objective rules)
Use both metrics:
- Real estate investment (YoY)
- New home prices in 70 cities (MoM or YoY)

Rules:
- Bra: investment decline narrows for 2 consecutive periods AND prices stabilize (flat or improving)
- Neutral: mixed signals (one improves, one worsens)
- Svag: investment decline deepens for 2 consecutive periods AND prices falling broadly

4) Credit (TSF / credit growth)
- Bra: TSF growth accelerates vs prior period and is above trend
- Neutral: flat vs prior period
- Svag: TSF growth decelerates meaningfully

5) Inflation (CPI)
- Bra: 1–2% YoY and rising
- Neutral: 0–1% YoY
- Svag: <= 0% YoY (deflation risk)

## Overall Label (majority rule)
- 3+ Bra -> "Ser bra ut" (4–5 Bra -> "Går starkt")
- 3+ Svag -> "Oroligt" (4–5 Svag -> "Dåligt")
- Otherwise -> "Neutral / saktar till"

## Global Risk Filter (one-step downgrade)
If global risk is clearly negative, downgrade the overall label by one step.
Example triggers:
- Global manufacturing PMI < 50
- USD liquidity tightening signal (e.g., sharp rise in DXY + widening credit spreads)

## Backtest-Light (sanity check)
- Review last 12–24 months and note whether status shifts align with known macro turns.
- This is not a full backtest; it is a reasonableness check.

## Notes
- Use official sources (NBS, PBOC) and reputable secondary sources for release calendars.
- Always include dates for the latest data points.
- If any block has missing data, mark it Neutral and note the gap.
