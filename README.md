# Morning Butler

Morning Butler is a lightweight, local dashboard that pulls together your Canvas assignments, weather, news headlines, and email previews into a clean morning briefing.

## Run
- Packaged build: double-click the Morning Butler app/executable.
- Development: run `python butler-fetch.py` from the project folder.

## First Run Setup
- `user_config.json` is created automatically on first launch.
- You enter your Canvas API token and ZIP code in the welcome screen.
- Email accounts (IMAP app passwords) are optional and configured in Preferences.

## Privacy
- No personal data is included in this repository.
- Your tokens, ZIP code, and email credentials are stored locally in `user_config.json`.

## Development
- Python is required for development.
- Packaged builds do not require Python to be installed.

## License
This project is licensed under the GNU General Public License v3.0 (GPL-3.0). See `LICENSE.txt` for details.
