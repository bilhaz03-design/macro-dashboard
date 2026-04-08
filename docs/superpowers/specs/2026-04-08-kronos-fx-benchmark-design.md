# Kronos FX Benchmark — Design Spec
_Datum: 2026-04-08 | Status: Godkänd_

## Syfte

Avgöra om Kronos-small (24.7M params) levererar signal för FX-prediktion jämfört med naive baseline (random walk). Resultatet bestämmer om Kronos ska integreras i fx-systemet eller förkastas.

## Scope

Ett enda Python-script: `scripts/kronos_fx_benchmark.py`

In-scope:
- Walk-forward benchmark: 20 fönster, EURUSD daglig
- Kronos-small inference (CPU)
- Naive baseline (random walk)
- MAE-evaluering per horisont (dag 1, 3, 5)
- Resultat: tabell i terminalen + PNG-chart

Out-of-scope:
- Finetuning
- Andra valutapar
- Integrering med fx-brief
- Realtidsinference

## Arkitektur

```
scripts/kronos_fx_benchmark.py
├── fetch_data()        yfinance EURUSD=X, daglig, 3 år, returnerar DataFrame [open,high,low,close,volume]
├── walk_forward()      itererar 20 fönster med stride=10 handelsdagar över sista 12 månaderna
│   ├── kronos_predict()    400-bars kontext → 5 bars OHLC via KronosPredictor.predict()
│   └── naive_predict()     close[t+1..5] = close[t] (random walk baseline)
├── evaluate()          MAE(kronos_close, actual_close) vs MAE(naive_close, actual_close) per horisont
└── report()            tabell + matplotlib chart → data/kronos_eurusd_benchmark.png
```

## Komponenter

### fetch_data()
- `yfinance.download("EURUSD=X", period="3y", interval="1d")`
- Droppar rader med NaN close
- Returnerar DataFrame med kolumnerna `open, high, low, close, volume`

### walk_forward()
- Test-period: sista 12 månader av data (ca 252 handelsdagar)
- Fönster: 20 st, stride = 10 handelsdagar
- Per fönster: `x = data[i-400:i]`, `y_actual = data[i:i+5]`
- Kör both kronos_predict() och naive_predict()

### kronos_predict()
- Laddar modell en gång utanför loopen (ingen HuggingFace-last per iteration)
- `KronosPredictor(model, tokenizer, device="cpu", max_context=512)`
- `y_timestamp` genereras som `pd.bdate_range(start=x_ts.iloc[-1] + pd.Timedelta(days=1), periods=5)` — business days räcker för EURUSD (24h-marknad, inga börsstängningar)
- Anropar `.predict(df=x_df, x_timestamp=x_ts, y_timestamp=y_ts, pred_len=5, T=0.6, top_p=0.9, sample_count=1)`
- Returnerar `pred_close` (5 värden, index 0-4)

### naive_predict()
- `[close[-1]] * 5` — random walk

### evaluate()
- Per horisont h ∈ {1, 3, 5}: `MAE_kronos[h]`, `MAE_naive[h]`
- Konfidensintervall: mean ± std över 20 fönster
- Relativ förbättring: `(MAE_naive - MAE_kronos) / MAE_naive * 100`

### report()
- Terminalutskrift: tabell med MAE per horisont + relativ förbättring
- Chart: MAE Kronos vs Naive per horisont, sparas till `data/kronos_eurusd_benchmark.png`

## Beroenden och förutsättningar

### Steg 0 — obligatoriskt innan körning
Kronos har inget setup.py — det installeras inte via pip. Repot måste klonas:
```bash
git clone https://github.com/shiyu-coder/Kronos ~/Desktop/Kronos
pip install -r ~/Desktop/Kronos/requirements.txt
pip install yfinance
```

Scriptet letar efter repot i `~/Desktop/Kronos` som default, eller via `KRONOS_PATH`-env:
```bash
KRONOS_PATH=/annan/path python scripts/kronos_fx_benchmark.py
```

Om repot saknas: tydligt felmeddelande med exakt clone-kommando, ingen tyst krasch.

### Paket
```
yfinance       # pip install yfinance
torch          # via Kronos requirements.txt
einops==0.8.1  # via Kronos requirements.txt
huggingface_hub # via Kronos requirements.txt
matplotlib     # via Kronos requirements.txt
pandas         # via Kronos requirements.txt
```

### yfinance kolumnhantering
yfinance 0.2.x returnerar MultiIndex-kolumner `('Close', 'EURUSD=X')`.
Scriptet normaliserar alltid till flata gemena kolumnnamn direkt efter download:
```python
df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
```

## Framgångskriterium

- Om Kronos MAE (horisont dag 1) < Naive MAE med >10% förbättring: signalvärde finns, fortsätt integration.
- Om <5% förbättring eller sämre: förkasta Kronos för detta use case.
- 5-10%: borderline — utvärdera dag 3 och dag 5 horizont.

## Output-filer

- `data/kronos_eurusd_benchmark.png` — MAE-chart
- Terminaloutput med tabell (ingen fil-output av råresultat i denna version)

## Körning

```bash
cd ~/Desktop/Finans\ Projects
pip install yfinance  # om ej installerat
python scripts/kronos_fx_benchmark.py
```

Estimerad körtid: 5-15 min på CPU (20 × inference på Kronos-small).
