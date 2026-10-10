# NFL Live Scores

A desktop scoreboard for the NFL that shows live scores using ESPN's public scoreboard API. It's one Python file built on tkinter, so there's nothing to install beyond Python and no API key needed.

## Features

- **Every game this week** in a grid of cards: live games first, then upcoming, then finals
- **Live game details**: quarter and clock, down and distance, who has the ball, a red zone alert and the last play
- **Upcoming game details**: kickoff in your local time, TV channel, betting line and over/under, and stadium
- **Score changes sidebar** that logs each score with its likely type (field goal, touchdown + PAT, safety…), plus every kickoff and final
- **Gold highlight** on a game for 60 seconds after its score changes
- **Auto-refresh** every 60 seconds with a countdown, or refresh any time with the button or `F5`
- **Summary chips** in the header showing how many games are live, upcoming and final, plus the season, week and teams on bye
- **Team logos and colors**, with the team abbreviation shown if a logo can't load
- Card columns adjust to the window size, and the window opens maximized

## Requirements

- Python 3.7 or newer, with tkinter (included with the python.org installers for Windows and macOS)
- An internet connection

On Debian/Ubuntu Linux, tkinter is a separate package:

```bash
sudo apt install python3-tk
```

## Usage

```bash
python nfl_live_scores.py
```

| Control | Action |
|---|---|
| `F5` or **Refresh now** | Fetch scores immediately |
| **Clear** (sidebar) | Empty the score changes log |
| Mouse wheel | Scroll the games grid or the sidebar, whichever the cursor is over |

The sidebar starts empty and only logs changes that happen while the app is open. The first load sets the starting point.

## Configuration

These constants are at the top of `nfl_live_scores.py`:

| Constant | Default | What it does |
|---|---|---|
| `REFRESH_SECONDS` | `60` | Seconds between automatic refreshes |
| `HIGHLIGHT_SECONDS` | `60` | How long a game stays gold after a score |
| `MAX_LOG_ENTRIES` | `300` | Entries kept in the sidebar before the oldest are dropped |
| `CARD_MIN_WIDTH` | `400` | Minimum card width in pixels, which sets the number of columns |
| `SIDEBAR_WIDTH` | `400` | Sidebar width in pixels |

The color palette and fonts are defined just below them.

## Notes

- Built and tested on Windows. It uses the Segoe UI font and Windows mouse wheel events, so on macOS and Linux the fonts will be substituted and mouse wheel scrolling may not work.
- The ESPN endpoint is public but unofficial and undocumented. It could change or stop working without notice.
- Score types are inferred from the number of points added between refreshes. If a team scores twice within one refresh, the sidebar shows the combined total, e.g. "Scored 10".
