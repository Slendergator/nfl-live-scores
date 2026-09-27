import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import requests
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from tqdm import tqdm

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
POLL_INTERVAL = 60
MAX_BACKOFF = 300
REQUEST_TIMEOUT = 10
LOG_LENGTH = 15


@dataclass(frozen=True)
class Game:
    id: str
    home_team: str
    home_score: int
    away_team: str
    away_score: int
    status: str


def parse_game(event):
    competition = event["competitions"][0]
    status = competition["status"]["type"]
    if status["state"] != "in":
        return None
    sides = {c["homeAway"]: c for c in competition["competitors"]}
    home, away = sides["home"], sides["away"]
    return Game(
        id=event["id"],
        home_team=home["team"]["displayName"],
        home_score=int(home.get("score") or 0),
        away_team=away["team"]["displayName"],
        away_score=int(away.get("score") or 0),
        status=status["shortDetail"],
    )


def fetch_live_games(session):
    try:
        response = session.get(SCOREBOARD_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        events = response.json().get("events", [])
    except (requests.RequestException, ValueError):
        return None
    return [game for game in map(parse_game, events) if game is not None]


def record_score_changes(games, previous, log):
    timestamp = datetime.now().strftime("%H:%M:%S")
    for game in games:
        prev = previous.get(game.id)
        if prev is not None:
            for team, old, new in (
                (game.away_team, prev.away_score, game.away_score),
                (game.home_team, prev.home_score, game.home_score),
            ):
                if old != new:
                    log.append(f"{timestamp}  {team} {old} -> {new} (+{new - old})")
        previous[game.id] = game


def score_style(score, other):
    return "bold green" if score > other else ""


def build_table(games):
    table = Table(title="Live NFL Scores", expand=True)
    table.add_column("Away")
    table.add_column("Pts", justify="center")
    table.add_column("Home")
    table.add_column("Pts", justify="center")
    table.add_column("Status")
    for game in games:
        table.add_row(
            Text(game.away_team, style=score_style(game.away_score, game.home_score)),
            str(game.away_score),
            Text(game.home_team, style=score_style(game.home_score, game.away_score)),
            str(game.home_score),
            game.status,
        )
    if not games:
        table.caption = "No games in progress"
    return table


def build_log_panel(log):
    body = Text("\n".join(log)) if log else Text("No scoring events yet", style="dim")
    return Panel(body, title="Scoring Log")


def build_countdown(elapsed, total):
    remaining = total - elapsed
    return Text(
        tqdm.format_meter(
            n=elapsed,
            total=total,
            elapsed=elapsed,
            ncols=70,
            bar_format=f"Next update in {remaining:>3}s |{{bar}}|",
        )
    )


def build_display(games, log, status, elapsed, total):
    return Group(
        build_table(games),
        status,
        build_countdown(elapsed, total),
        build_log_panel(log),
    )


def run():
    games = []
    previous = {}
    log = deque(maxlen=LOG_LENGTH)
    backoff = POLL_INTERVAL

    with requests.Session() as session, Live(console=Console(), refresh_per_second=4) as live:
        while True:
            result = fetch_live_games(session)

            if result is None:
                backoff = min(backoff * 2, MAX_BACKOFF)
                wait = backoff
                status = Text(f"Fetch failed, retrying in {wait}s", style="red")
            else:
                record_score_changes(result, previous, log)
                games = result
                backoff = POLL_INTERVAL
                wait = POLL_INTERVAL
                status = Text(f"Last updated {datetime.now():%H:%M:%S}", style="dim")

            for elapsed in range(wait):
                live.update(build_display(games, log, status, elapsed, wait))
                time.sleep(1)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
