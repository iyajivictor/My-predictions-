# Sports Predictions Engine

Automated football predictions engine covering Corners, Goals Over/Under, and GG markets.
Sends accumulator bets via Telegram. Logs all predictions to SQLite for future ML training.

## Leagues Covered
- Premier League, La Liga, Bundesliga, Serie A, Ligue 1, Eredivisie
- UEFA Champions League, Europa League, Conference League

## Acca Schedule
| Acca | Day | Markets |
|---|---|---|
| Weekend Corners + Goals | Thursday | Top 5 + Eredivisie (Sat/Sun fixtures) |
| UCL Corners + Goals + GG | Tuesday | UCL only |
| UEL/Conf Corners + Goals + GG | Thursday | UEL + Conference |

---

## Deployment — PythonAnywhere (Free)

### 1. Create account
Sign up at [pythonanywhere.com](https://pythonanywhere.com) (free tier)

### 2. Upload files
Go to **Files** tab → upload all `.py` files and `requirements.txt`

### 3. Open a Bash console and install dependencies
```bash
pip3 install --user httpx requests beautifulsoup4 apscheduler python-dotenv
```

### 4. Create your .env file
In the Bash console:
```bash
cat > .env << EOF
API_SPORTS_KEY=your_key_here
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
DB_PATH=predictions.db
EOF
```

### 5. Initialise the database
```bash
python3 main.py status
```

### 6. Scrape FBRef corners (first time + weekly)
```bash
python3 main.py scrape
```

### 7. Set up Scheduled Tasks
Go to **Tasks** tab on PythonAnywhere → Add two tasks:

| Time (UTC) | Command |
|---|---|
| 07:00 | `python3 /home/yourusername/main.py daily` |
| 21:00 | `python3 /home/yourusername/main.py results` |

That's it — engine runs automatically.

---

## Manual CLI Commands

```bash
python3 main.py daily              # Run today's job manually
python3 main.py results            # Update today's results
python3 main.py predict 2025-05-10 # Predictions for a specific date
python3 main.py acca weekend       # Send weekend acca now
python3 main.py acca ucl           # Send UCL acca now
python3 main.py acca uel           # Send UEL/Conf acca now
python3 main.py scrape             # Refresh FBRef corners data
python3 main.py status             # Show DB stats
```

### Manual corners entry (after match)
```bash
python3 updater.py corners <fixture_id> <actual_corners>
```

### Export ML dataset
```bash
python3 updater.py export
```

---

## Files
| File | Purpose |
|---|---|
| `main.py` | Entry point + day router |
| `database.py` | SQLite schema |
| `fetcher.py` | api-sports.io data fetcher |
| `fbref_scraper.py` | FBRef corners scraper |
| `models.py` | Corners + GG + Goals prediction engines |
| `accumulator.py` | Acca builder + sender |
| `telegram.py` | Telegram notifications |
| `updater.py` | Results updater + ML export |

---

## Data Sources
- **api-sports.io** — fixtures, team stats, H2H, results (100 req/day free)
- **FBRef** — corners data (scraped weekly, no API key needed)
