#!/usr/bin/env bash
# Setup and migration for the canonical bedroom climate automation codebase.
# Usage:
#   ./setup.sh                    Set up the Python environment
#   ./setup.sh --run-once         Run the dry-run controller once
#   ./setup.sh --test             Run offline regression tests
#   ./setup.sh --install-launchd  Install/start 5-minute macOS background collector
#   ./setup.sh --status           Inspect the background collector
#   ./setup.sh --uninstall-launchd Stop/remove the background collector
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(pwd -P)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
LAUNCHD_LABEL="local.shannon.bedroom-climate.collector"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/$LAUNCHD_LABEL.plist"
INTERVAL_SECONDS=300

usage() {
    cat <<'USAGE'
Usage: ./setup.sh [option]

No option           Install/update local Python venv and dependencies
--run-once          Set up if needed and run controller.py once (dry-run)
--test              Run offline regression tests
--install-launchd   Set up and install an every-5-minutes LaunchAgent (macOS)
--install-launchd-live Install commissioned unattended control at the configured interval
--status            Show LaunchAgent status (macOS)
--uninstall-launchd Stop/remove LaunchAgent (keeps .env and database)
--help              Show this help

Code releases overwrite application files but preserve .env, .venv, data/, logs/.

The shell cannot be activated by a child script. To activate interactively:
  source .venv/bin/activate
USAGE
}

require_macos() {
    if [[ "$(uname -s)" != "Darwin" ]]; then
        echo "This operation requires macOS." >&2
        exit 1
    fi
}

check_env() {
    "$VENV_PYTHON" - <<'PY'
from pathlib import Path
from dotenv import dotenv_values

path = Path('.env')
if not path.is_file():
    raise SystemExit('Missing .env; create it from .env.example.')
values = dotenv_values(path)
for name in ('FLAIR_CLIENT_ID', 'FLAIR_CLIENT_SECRET', 'WEATHER_LOCATION'):
    value = (values.get(name) or '').strip()
    if not value or value.lower().startswith(('your-', 'replace-', 'example-')):
        raise SystemExit(f'Missing/placeholder {name} in .env (do not share its value).')
print('Local credentials/configuration found (values not printed).')
PY
}

remove_obsolete_files() {
    # Known filenames from the pre-canonical preview. Remove only these code
    # artifacts; never delete .env, .venv, SQLite files, or log files.
    local old_file
    for old_file in controller_next.py README-NEXT.md test_climate_policy.py test_control_store.py control_store.py tests/test_control_store.py; do
        if [[ -f "$PROJECT_DIR/$old_file" ]]; then
            rm -- "$PROJECT_DIR/$old_file"
            echo "Removed superseded $old_file"
        fi
    done
}

install_deps() {
    remove_obsolete_files
    if ! command -v "${PYTHON_BIN:-python3}" >/dev/null 2>&1; then
        echo "Python 3 not found. Install Python 3.10+ and retry." >&2
        exit 1
    fi
    "${PYTHON_BIN:-python3}" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f'Python 3.10+ required; found {sys.version.split()[0]}')
PY
    if [[ ! -x "$VENV_PYTHON" ]]; then
        echo "Creating virtual environment..."
        "${PYTHON_BIN:-python3}" -m venv "$PROJECT_DIR/.venv"
    fi
    echo "Installing dependencies into .venv..."
    "$VENV_PYTHON" -m pip install -r "$PROJECT_DIR/requirements.txt"

    if [[ ! -f "$PROJECT_DIR/.env" ]]; then
        cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
        echo "Created .env template; enter your Flair credentials locally."
    fi
    # Append only NEW non-secret defaults; never change user-entered values or credentials.
    "$VENV_PYTHON" - <<'PYENV'
from pathlib import Path
from dotenv import dotenv_values

existing = Path('.env')
template = Path('.env.example')
known = dotenv_values(existing)
lines = [line for line in template.read_text().splitlines()
         if line.startswith(('CLIMATE_', 'BROADLINK_', 'SHELLY_'))
         and line.split('=', 1)[0] not in known
         and line.split('=', 1)[0] not in ('CLIMATE_HIGH_AT_F',)]
if lines:
    with existing.open('a') as out:
        out.write('\n# Missing project settings (added by setup; edit locally as needed)\n')
        out.write('\n'.join(lines) + '\n')
    print(f'Appended {len(lines)} new climate settings to .env without changing existing values.')
PYENV
    chmod 600 "$PROJECT_DIR/.env"
    mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/logs"
    echo "Ready. For an interactive environment: source .venv/bin/activate"
}

install_launchd() {
    require_macos
    if [[ ! -f "$PROJECT_DIR/controller.py" ]]; then
        echo "Missing controller.py. Place this setup package beside your existing code." >&2
        exit 1
    fi
    install_deps
    check_env
    if [[ "${1:-dry-run}" == "unattended" ]]; then
        "$VENV_PYTHON" - <<'PYREADY'
import os
from settings import load_delivery_settings
from unattended import load_commissioning
if not load_delivery_settings().enabled or os.getenv('UNATTENDED_CONTROL_ENABLED', 'false').lower() != 'true':
    raise SystemExit('Enable hardware and unattended control explicitly before installing live scheduling.')
load_commissioning()
PYREADY
    fi
    INTERVAL_SECONDS="$("$VENV_PYTHON" - <<'PYINTERVAL'
from dotenv import dotenv_values
value = dotenv_values('.env').get('COLLECTOR_INTERVAL_SECONDS', '300')
if value is None or not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 2147483647:
    raise SystemExit('COLLECTOR_INTERVAL_SECONDS must be a whole number from 1 to 2147483647.')
print(int(value))
PYINTERVAL
)"
    mkdir -p "$(dirname "$LAUNCHD_PLIST")" "$PROJECT_DIR/logs"

    # plistlib properly escapes macOS paths, even if the path contains spaces.
    "$VENV_PYTHON" - "$LAUNCHD_PLIST" "$LAUNCHD_LABEL" "$PROJECT_DIR" "$INTERVAL_SECONDS" "${1:-dry-run}" <<'PY'
import plistlib
import sys
from pathlib import Path

plist_path = Path(sys.argv[1])
label = sys.argv[2]
project = Path(sys.argv[3])
interval = int(sys.argv[4])
plist = {
    'Label': label,
    'ProgramArguments': [str(project / '.venv/bin/python'), str(project / 'controller.py')],
    'WorkingDirectory': str(project),
    'StartInterval': interval,
    'RunAtLoad': True,
    'StandardOutPath': str(project / 'logs/collector.stdout.log'),
    'StandardErrorPath': str(project / 'logs/collector.stderr.log'),
    'EnvironmentVariables': {'PYTHONUNBUFFERED': '1'},
}
if sys.argv[5] == 'unattended':
    plist['ProgramArguments'].append('--unattended')
with plist_path.open('wb') as f:
    plistlib.dump(plist, f)
PY
    plutil -lint "$LAUNCHD_PLIST"
    local domain="gui/$(id -u)"
    launchctl bootout "$domain/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
    launchctl bootstrap "$domain" "$LAUNCHD_PLIST"
    echo "Installed $LAUNCHD_LABEL (runs every $INTERVAL_SECONDS seconds, and on loading)."
    echo "View stdout: tail -f '$PROJECT_DIR/logs/collector.stdout.log'"
    echo "View errors: tail -f '$PROJECT_DIR/logs/collector.stderr.log'"
    echo "Note: LaunchAgents require a logged-in user session; the Mac itself must stay awake."
    if [[ "${1:-dry-run}" == "unattended" ]]; then
        echo "Commissioned unattended control is LIVE. Pause before using the remote/app."
    else
        echo "Current controller is DRY RUN: it logs readings/decisions, not IR commands."
    fi
}

status_launchd() {
    require_macos
    local service="gui/$(id -u)/$LAUNCHD_LABEL"
    if launchctl print "$service"; then
        printf '\nLog files: %s\n' "$PROJECT_DIR/logs/"
    else
        echo "LaunchAgent not loaded: $service" >&2
        exit 1
    fi
}

uninstall_launchd() {
    require_macos
    launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1 || true
    if [[ -f "$LAUNCHD_PLIST" ]]; then
        rm "$LAUNCHD_PLIST"
        echo "Removed $LAUNCHD_PLIST"
    else
        echo "No LaunchAgent plist found."
    fi
    echo "Preserved .env, local database, and log files."
}

case "${1:-}" in
    "") install_deps ;;
    --run-once)
        install_deps
        check_env
        "$VENV_PYTHON" "$PROJECT_DIR/controller.py"
        ;;
    --test)
        install_deps
        "$VENV_PYTHON" -m unittest discover -s tests -v
        ;;
    --install-launchd) install_launchd ;;
    --install-launchd-live) install_launchd unattended ;;
    --status) status_launchd ;;
    --uninstall-launchd) uninstall_launchd ;;
    --help|-h) usage ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
esac
