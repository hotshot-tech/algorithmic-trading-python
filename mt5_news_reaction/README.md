# MT5 news reaction

Measures how the main FX pairs (and gold) moved **15 and 60 minutes after** each economic
event the Market Brief bots covered this week, using your broker's MetaTrader 5 prices,
and posts the table to Discord. It's meant to run on Saturday morning, after the cloud
weekly review.

Events covered are the same ones as the Discord briefs: all High-impact events for
USD, EUR, GBP, JPY, AUD, NZD, CAD and CHF, plus Medium-impact USD and GBP events.

## One-time setup (Windows)

**Quickest:** install Python (step 1), then double-click `setup.bat` and follow the prompts. It does steps 2–5 and the scheduling below for you.

1. Install Python 3.10+ from https://www.python.org/downloads/ (tick **"Add python.exe to PATH"**).
2. Open **Command Prompt** in this folder and run:
   ```
   pip install -r requirements.txt
   ```
3. Copy `config.example.ini` to `config.ini` and paste your Discord webhook URL into it.
   If your broker's symbols have a suffix (e.g. `EURUSD.a`), set `symbol_suffix`.
4. Open MetaTrader 5 and log in. Make sure EURUSD, GBPUSD, USDJPY, XAUUSD etc. exist
   in Market Watch (right-click → Show All).
5. Test it without posting:
   ```
   python news_reaction.py --dry-run
   ```
   Check that the first line shows a sensible broker offset (usually `UTC+2` or `UTC+3`).

## Run it automatically every Saturday at 10:00

In Command Prompt (replace the path with where this folder is):

```
schtasks /Create /SC WEEKLY /D SAT /ST 10:00 /TN "MT5 News Reaction" /TR "cmd /c cd /d C:\path\to\mt5_news_reaction && python news_reaction.py >> run.log 2>&1"
```

The PC must be on and MetaTrader 5 open and logged in at that time. Output goes to `run.log`.
To remove the schedule: `schtasks /Delete /TN "MT5 News Reaction" /F`.

## Reading the output

```
EUR/USD  15m   -8.2p  60m  -12.5p  range   21.0p
```
- **15m / 60m**: price change from just before the release to 15 / 60 minutes after (p = pips, $ = gold dollars).
- **range**: high-to-low distance during the hour after the release, i.e. how violent the spike was.

## Troubleshooting

- `MT5 initialize() failed`: open MetaTrader 5 and log in, or set `terminal_path` in `config.ini`.
- `no data` for a pair: the symbol name differs at your broker; set `symbol_suffix`, or the pair isn't offered.
- Times look shifted by an hour: set `server_utc_offset` in `config.ini` to your broker's offset.
