# Bedroom climate automation

Python prototype to read bedroom temperature from an original Flair Puck, fetch outdoor conditions from Open-Meteo, and decide when to cool the bedroom with a Vornado Transom. Readings are stored in a local SQLite database. The controller **currently runs in dry-run mode**: it **does not send IR commands**.

## Requirements

- macOS for background collection via `launchd` (manual Python runs can work on other OSes)
- Python 3.10+ (`python3 --version`)
- Git (required to pull the Flair Python library and clone/push the repository)
- Flair API Client ID and Client Secret from Flair Developer Settings
- Internet access for Flair/Open-Meteo data

These setup files are designed to sit alongside your **existing working** `controller.py`, `check_cooling.py`, `database.py`, `test_flair.py`, and `test_weather.py`. They **do not replace those Python files**.

## Quick setup on a Mac

From the project directory:

```bash
chmod +x setup.sh
./setup.sh
```

The script installs Python dependencies in `.venv/`, creates `data/` and `logs/`, and (if necessary) copies `.env.example` to `.env`. It never overwrites an existing `.env`.

Edit `.env` locally, using your own Flair credentials:

```dotenv
FLAIR_CLIENT_ID=your-real-client-id
FLAIR_CLIENT_SECRET=your-real-client-secret
WEATHER_LOCATION=Indianapolis
```

**Never add `.env` to Git or share its contents.** Run a dry-run test:

```bash
./setup.sh --run-once
```

Or activate the environment for interactive Python commands:

```bash
source .venv/bin/activate
python controller.py
```

A setup subprocess cannot activate its parent Terminal shell, so interactive activation is a separate `source` command. The background service uses `.venv/bin/python` directly and needs no activation.

## Push your existing project to the private repository

Before proceeding, merge these files into the root of your current `flair-automation/` folder. **If you already have a `.gitignore` or README, merge their content rather than losing existing customizations.**

```bash
cd /path/to/flair-automation

# Only if this project is not already a local Git repository:
git init

git branch -M main

# Only if origin does not yet exist; replace with your private repo URL:
git remote add origin git@github.com:YOUR_ACCOUNT/YOUR_PRIVATE_REPO.git

# Inspect exclusions and staged changes before committing:
git status --short --ignored
git check-ignore -v .env data/bedroom.sqlite3 .venv/bin/python

git add .
git diff --cached --name-only
```

Stop if the staged list contains `.env`, files under `data/`, `logs/`, or `.venv/`. If `.env` was previously tracked, remove it from the Git index without deleting the file:

```bash
git rm --cached .env
```

If credentials were ever pushed to a remote, **rotate your Flair secret**; removing it in a later commit does not erase older commits.

After verifying the staged files:

```bash
git commit -m "Add bedroom automation and Mac setup"
git push -u origin main
```

If `origin` already exists, inspect it with `git remote -v` instead of running `git remote add origin`. If the private remote was initialized with its own README, the first push may be rejected because remote history already exists. **Do not force-push**; reconcile/pull remote history first.

GitHub SSH URLs require an authorized SSH key. HTTPS remote URLs are also fine; use your provider's recommended credential manager/token mechanism (not an account password).

## Clone onto the Mac mini

```bash
git clone git@github.com:YOUR_ACCOUNT/YOUR_PRIVATE_REPO.git ~/flair-automation
cd ~/flair-automation
./setup.sh
```

Fill in a **separate local `.env` on the mini**. Do not copy `.env` into Git. Test manually:

```bash
./setup.sh --run-once
```

Then install the every-5-minute launchd collector (once you want continuous logging):

```bash
./setup.sh --install-launchd
./setup.sh --status
```

The background collector runs `controller.py` with no `--at` argument, so it records real sensor readings to `data/bedroom.sqlite3` every five minutes, even outside the fan's 22:00–07:30 operating hours. It currently only prints fan decisions; **it does not control hardware**.

Inspect the output and any errors:

```bash
tail -f logs/collector.stdout.log
tail -f logs/collector.stderr.log
```

To stop and remove the service without deleting data or credentials:

```bash
./setup.sh --uninstall-launchd
```

### Important Mac mini settings

- **System Settings → Energy → Prevent automatic sleeping when the display is off**: enable it; the display may sleep, but the computer must stay awake for 5-minute runs.
- This installs a **user LaunchAgent**, not a boot-time system daemon. Its user must remain logged in; after a restart it resumes when that account logs in.
- To run without anyone logged in, a system LaunchDaemon is possible later, but needs additional permission and security configuration.
- Run the collector on one Mac (preferably the mini). Code syncs via Git, but the SQLite history remains **local** to whichever Mac records it.

## Existing controller settings

At this stage, the project uses:

| Setting | Value |
|---|---|
| Cooling schedule | 22:00–07:30 in `America/Indiana/Indianapolis` |
| Target | 60°F |
| Restart threshold | 64°F |
| Minimum outdoor cooling advantage | 5°F |
| Collection interval | 300 seconds |
| Storage | Local SQLite at `data/bedroom.sqlite3` |

Future phases: BroadLink RM4 Pro IR/RF control, variable Transom speed (low preferred), optional power-monitoring smart plug, temperature-trend diagnostics, and experimental exhaust warm-air recovery using hallway temperature. **Those features are planned, not implemented.**

## Troubleshooting

- **`python3` too old:** install Python 3.10+ (e.g., via Homebrew) and rerun setup with `PYTHON_BIN=/path/to/new/python3 ./setup.sh`.
- **`pip install` fails cloning Flair:** check Git is installed, network works, and your remote can reach `github.com`.
- **`--install-launchd` rejects credentials:** ensure `.env` has real, nonempty Flair credentials and `WEATHER_LOCATION`.
- **No periodic logs:** make sure the Mac isn't sleeping and the user stays logged in. Use `./setup.sh --status` and inspect `logs/collector.stderr.log`.
- **Unexpected readings:** confirm `WEATHER_LOCATION=Indianapolis` and compare the Flair bedroom reading to the Puck/App. Sensor freshness checks are still to be implemented.
- **Never commit the database:** keep `data/` ignored. To back up data, copy or use SQLite's `.backup` command while the database is open for use.

