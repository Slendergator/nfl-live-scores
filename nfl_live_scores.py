"""NFL Live Scoreboard - a tkinter app backed by ESPN's public scoreboard API.

- Opens maximized
- Refreshes every 60 seconds (or click "Refresh now")
- Logs every score change, kickoff and final in the sidebar
- Games that just changed glow gold until the next update

Run:  python nfl_scoreboard.py
No API key or third-party packages needed.
"""

import base64
import json
import queue
import threading
import time
import tkinter as tk
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from tkinter import ttk

API_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
LOGO_URL = "https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500-dark/{abbr}.png&h=48&w=48"
REFRESH_SECONDS = 60
HIGHLIGHT_SECONDS = 60
MAX_LOG_ENTRIES = 300
CARD_MIN_WIDTH = 400
SIDEBAR_WIDTH = 400

# Palette
BG = "#0d1117"
PANEL = "#151b23"
CARD = "#1b222c"
CARD_LIVE = "#1f2731"
BORDER = "#2a3441"
TEXT = "#e6edf3"
MUTED = "#8b98a5"
DIM = "#5c6773"
RED = "#ff5c5c"
GREEN = "#3fb950"
GOLD = "#f2cc60"
BLUE = "#58a6ff"

FONT = "Segoe UI"
F_TITLE = (FONT, 24, "bold")
F_SUB = (FONT, 11)
F_TEAM = (FONT, 15, "bold")
F_SCORE = (FONT, 28, "bold")
F_SMALL = (FONT, 9)
F_SMALL_BOLD = (FONT, 9, "bold")
F_BODY = (FONT, 10)
F_BODY_BOLD = (FONT, 10, "bold")
F_HEAD = (FONT, 13, "bold")

SEASON_TYPES = {1: "Preseason", 2: "Regular Season", 3: "Postseason", 4: "Off Season"}
STATE_ORDER = {"in": 0, "pre": 1, "post": 2}


# --------------------------------------------------------------------------- data

def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (NFL Scoreboard)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def brightness(hex_color):
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, TypeError):
        return 0
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def team_color(team):
    """Pick a team color that will actually show up on a dark background."""
    for key in ("color", "alternateColor"):
        c = team.get(key)
        if c and brightness(c) > 0.2:
            return "#" + c
    return MUTED


def local_kickoff(iso):
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).astimezone()
    except ValueError:
        return iso
    return f"{dt.strftime('%a %b')} {dt.day} · {dt.strftime('%I:%M %p').lstrip('0')}"


def parse_team(comp, possession_id):
    t = comp.get("team", {})
    records = comp.get("records") or []
    return {
        "id": t.get("id"),
        "abbr": t.get("abbreviation", "?"),
        "name": t.get("shortDisplayName") or t.get("name", ""),
        "location": t.get("location", ""),
        "record": records[0].get("summary", "") if records else "",
        "score": int(comp.get("score") or 0),
        "winner": bool(comp.get("winner")),
        "color": team_color(t),
        "possession": possession_id is not None and t.get("id") == possession_id,
    }


def parse_games(data):
    games = []
    for e in data.get("events", []):
        c = e["competitions"][0]
        status = e.get("status", {})
        stype = status.get("type", {})
        sit = c.get("situation") or {}
        poss = sit.get("possession")
        teams = {comp.get("homeAway"): parse_team(comp, poss) for comp in c.get("competitors", [])}
        if "home" not in teams or "away" not in teams:
            continue

        broadcast = ""
        for b in c.get("broadcasts") or []:
            if b.get("names"):
                broadcast = " / ".join(b["names"])
                break

        odds = ""
        for o in c.get("odds") or []:
            parts = []
            if o.get("details"):
                parts.append(o["details"])
            if o.get("overUnder"):
                parts.append(f"O/U {o['overUnder']}")
            odds = "  ·  ".join(parts)
            break

        games.append({
            "id": e["id"],
            "state": stype.get("state", "pre"),
            "detail": stype.get("shortDetail") or stype.get("detail", ""),
            "kickoff": local_kickoff(e.get("date", "")),
            "date": e.get("date", ""),
            "broadcast": broadcast,
            "odds": odds,
            "venue": (c.get("venue") or {}).get("fullName", ""),
            "down": sit.get("downDistanceText") or "",
            "redzone": bool(sit.get("isRedZone")),
            "last_play": (sit.get("lastPlay") or {}).get("text", ""),
            "away": teams["away"],
            "home": teams["home"],
        })
    games.sort(key=lambda g: (STATE_ORDER.get(g["state"], 3), g["date"]))
    return games


def describe_points(diff):
    return {
        1: "Extra point",
        2: "Safety / 2-pt conversion",
        3: "Field goal",
        6: "Touchdown",
        7: "Touchdown + PAT",
        8: "Touchdown + 2-pt",
    }.get(diff, f"Scored {diff}" if diff > 0 else "Score corrected")


# --------------------------------------------------------------------------- UI

class ScrollFrame(tk.Frame):
    """A vertically scrolling frame whose inner frame always matches the canvas width."""

    def __init__(self, master, bg, on_resize=None):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                 style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        self._on_resize = on_resize

    def _resize(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        if self._on_resize:
            self._on_resize(event.width)

    def scroll(self, units):
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(units, "units")


class ScoreboardApp:
    def __init__(self, root):
        self.root = root
        self.games = []
        self.prev = {}            # game id -> {"away": int, "home": int, "state": str}
        self.first_load = True
        self.highlight = {}       # game id -> (expiry timestamp, set of sides that scored)
        self.logos = {}           # abbr -> PhotoImage or None
        self.cards = []
        self.cols = 0
        self.log_count = 0
        self.fetching = False
        self.remaining = 0
        self.results = queue.Queue()

        root.title("NFL Scoreboard")
        root.configure(bg=BG)
        root.minsize(900, 600)
        self._maximize()
        self._style()
        self._build_layout()

        root.bind_all("<MouseWheel>", self._on_wheel)
        root.bind("<F5>", lambda e: self.refresh_now())

        self.refresh_now()
        self._tick()
        self._poll_results()

    # ---- setup ------------------------------------------------------------

    def _maximize(self):
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Dark.Vertical.TScrollbar", background=BORDER, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED, lightcolor=BORDER, darkcolor=BORDER,
                        gripcount=0)
        style.map("Dark.Vertical.TScrollbar", background=[("active", DIM)])

    def _build_layout(self):
        # Header
        header = tk.Frame(self.root, bg=PANEL)
        header.pack(fill="x")
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")

        left = tk.Frame(header, bg=PANEL)
        left.pack(side="left", padx=24, pady=14)
        title_row = tk.Frame(left, bg=PANEL)
        title_row.pack(anchor="w")
        tk.Label(title_row, text="NFL", font=F_TITLE, fg=RED, bg=PANEL).pack(side="left")
        tk.Label(title_row, text=" SCOREBOARD", font=F_TITLE, fg=TEXT, bg=PANEL).pack(side="left")
        self.subtitle = tk.Label(left, text="Loading…", font=F_SUB, fg=MUTED, bg=PANEL)
        self.subtitle.pack(anchor="w")

        right = tk.Frame(header, bg=PANEL)
        right.pack(side="right", padx=24, pady=14)
        self.refresh_btn = tk.Button(
            right, text="⟳  Refresh now", font=F_BODY_BOLD, fg=TEXT, bg=BORDER,
            activebackground=DIM, activeforeground=TEXT, relief="flat", bd=0,
            padx=14, pady=6, cursor="hand2", command=self.refresh_now)
        self.refresh_btn.pack(side="right", padx=(16, 0))
        status_box = tk.Frame(right, bg=PANEL)
        status_box.pack(side="right")
        self.updated_lbl = tk.Label(status_box, text="", font=F_BODY, fg=TEXT, bg=PANEL, anchor="e")
        self.updated_lbl.pack(anchor="e")
        self.countdown_lbl = tk.Label(status_box, text="", font=F_SMALL, fg=MUTED, bg=PANEL, anchor="e")
        self.countdown_lbl.pack(anchor="e")

        self.summary = tk.Frame(header, bg=PANEL)
        self.summary.pack(side="left", padx=20)

        # Body: games grid + change log sidebar
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)

        sidebar = tk.Frame(body, bg=PANEL, width=SIDEBAR_WIDTH)
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)
        tk.Frame(body, bg=BORDER, width=1).pack(side="right", fill="y")

        side_head = tk.Frame(sidebar, bg=PANEL)
        side_head.pack(fill="x", padx=16, pady=(16, 8))
        self.log_title = tk.Label(side_head, text="SCORE CHANGES", font=F_HEAD, fg=TEXT, bg=PANEL)
        self.log_title.pack(side="left")
        tk.Button(side_head, text="Clear", font=F_SMALL, fg=MUTED, bg=PANEL, activebackground=BORDER,
                  activeforeground=TEXT, relief="flat", bd=0, padx=8, cursor="hand2",
                  command=self.clear_log).pack(side="right")

        self.log = ScrollFrame(sidebar, bg=PANEL)
        self.log.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        self.log_placeholder = tk.Label(
            self.log.inner, text="Watching for scoring plays…\nChanges will show up here.",
            font=F_BODY, fg=DIM, bg=PANEL, justify="center")
        self.log_placeholder.pack(pady=40)

        self.board = ScrollFrame(body, bg=BG, on_resize=self._on_board_resize)
        self.board.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)

    # ---- fetching ---------------------------------------------------------

    def refresh_now(self):
        if self.fetching:
            return
        self.fetching = True
        self.refresh_btn.configure(state="disabled", text="⟳  Updating…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            data = json.loads(fetch(API_URL))
            games = parse_games(data)
            missing = {g[s]["abbr"] for g in games for s in ("away", "home")} - set(self.logos)

            def get_logo(abbr):
                try:
                    return abbr, fetch(LOGO_URL.format(abbr=abbr.lower()), timeout=10)
                except Exception:
                    return abbr, None

            with ThreadPoolExecutor(max_workers=8) as pool:
                logo_bytes = dict(pool.map(get_logo, missing))
            self.results.put(("ok", data, games, logo_bytes))
        except Exception as ex:
            self.results.put(("error", str(ex)))

    def _poll_results(self):
        try:
            while True:
                self._handle_result(self.results.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self._poll_results)

    def _handle_result(self, result):
        self.fetching = False
        self.remaining = REFRESH_SECONDS
        self.refresh_btn.configure(state="normal", text="⟳  Refresh now")
        now = datetime.now().strftime("%I:%M:%S %p").lstrip("0")

        if result[0] == "error":
            self.updated_lbl.configure(text=f"Couldn't reach ESPN — retrying", fg=RED)
            self.countdown_lbl.configure(text=result[1][:80])
            return

        _, data, games, logo_bytes = result
        for abbr, raw in logo_bytes.items():
            try:
                self.logos[abbr] = tk.PhotoImage(data=base64.b64encode(raw)) if raw else None
            except tk.TclError:
                self.logos[abbr] = None

        week = (data.get("week") or {}).get("number")
        season = data.get("season") or {}
        parts = [str(season.get("year", "")), SEASON_TYPES.get(season.get("type"), "")]
        if week:
            parts.append(f"Week {week}")
        byes = [t.get("abbreviation") for t in (data.get("week") or {}).get("teamsOnBye", [])]
        self.subtitle.configure(text="  ·  ".join(p for p in parts if p))
        self.byes = byes

        self._detect_changes(games)
        self.games = games
        self.updated_lbl.configure(text=f"Last updated {now}", fg=TEXT)
        self._render_summary()
        self._render_games()

    # ---- change tracking --------------------------------------------------

    def _detect_changes(self, games):
        expiry = time.time() + HIGHLIGHT_SECONDS
        if not self.first_load:
            for g in games:
                old = self.prev.get(g["id"])
                if old is None:
                    continue
                scored = set()
                for side in ("away", "home"):
                    diff = g[side]["score"] - old[side]
                    if diff:
                        scored.add(side)
                        self._log_score(g, side, diff)
                if scored:
                    self.highlight[g["id"]] = (expiry, scored)
                if old["state"] == "pre" and g["state"] == "in":
                    self._log_event(g, "Kickoff", GREEN)
                    self.highlight.setdefault(g["id"], (expiry, set()))
                elif old["state"] != "post" and g["state"] == "post":
                    self._log_event(g, "Final", MUTED)
                    self.highlight.setdefault(g["id"], (expiry, set()))
        self.first_load = False
        self.prev = {g["id"]: {"away": g["away"]["score"], "home": g["home"]["score"],
                               "state": g["state"]} for g in games}

    def _log_score(self, g, side, diff):
        team = g[side]
        sign = "+" if diff > 0 else ""
        self._add_log_entry(
            stripe=team["color"],
            title=f"{team['abbr']}  {describe_points(diff)}",
            badge=f"{sign}{diff}",
            g=g,
            highlight_side=side,
        )

    def _log_event(self, g, label, color):
        if label == "Final":
            a, h = g["away"], g["home"]
            if a["score"] == h["score"]:
                label = "Final · Tie"
            else:
                winner = a if a["score"] > h["score"] else h
                label = f"Final · {winner['abbr']} wins"
        self._add_log_entry(stripe=color, title=label, badge="", g=g, highlight_side=None)

    def _add_log_entry(self, stripe, title, badge, g, highlight_side):
        if self.log_placeholder.winfo_ismapped():
            self.log_placeholder.pack_forget()

        entry = tk.Frame(self.log.inner, bg=CARD)
        tk.Frame(entry, bg=stripe, width=4).pack(side="left", fill="y")
        content = tk.Frame(entry, bg=CARD)
        content.pack(side="left", fill="both", expand=True, padx=12, pady=8)

        top = tk.Frame(content, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text=title, font=F_BODY_BOLD, fg=TEXT, bg=CARD).pack(side="left")
        if badge:
            tk.Label(top, text=badge, font=F_BODY_BOLD, fg=BG, bg=GOLD, padx=6).pack(side="right")
        tk.Label(top, text=datetime.now().strftime("%I:%M %p").lstrip("0"),
                 font=F_SMALL, fg=DIM, bg=CARD).pack(side="right", padx=8)

        line = tk.Frame(content, bg=CARD)
        line.pack(fill="x", pady=(2, 0))
        for side, sep in (("away", "  "), ("home", "")):
            t = g[side]
            fg = GOLD if side == highlight_side else MUTED
            tk.Label(line, text=f"{t['abbr']} {t['score']}", font=F_BODY_BOLD if side == highlight_side else F_BODY,
                     fg=fg, bg=CARD).pack(side="left")
            if sep:
                tk.Label(line, text=" @ ", font=F_BODY, fg=DIM, bg=CARD).pack(side="left")
        tk.Label(line, text=g["detail"], font=F_SMALL, fg=DIM, bg=CARD).pack(side="right")

        if g["last_play"] and highlight_side:
            tk.Label(content, text=g["last_play"], font=F_SMALL, fg=MUTED, bg=CARD,
                     wraplength=SIDEBAR_WIDTH - 70, justify="left", anchor="w").pack(fill="x", pady=(4, 0))

        # pack_slaves() is in on-screen order (newest first); winfo_children() is creation order
        children = [w for w in self.log.inner.pack_slaves() if w is not self.log_placeholder]
        if children:
            entry.pack(fill="x", padx=(8, 12), pady=4, before=children[0])
        else:
            entry.pack(fill="x", padx=(8, 12), pady=4)

        self.log_count += 1
        self.log_title.configure(text=f"SCORE CHANGES  ({self.log_count})")
        if len(children) + 1 > MAX_LOG_ENTRIES:
            children[-1].destroy()
        self.log.canvas.yview_moveto(0)

    def clear_log(self):
        for w in self.log.inner.winfo_children():
            if w is not self.log_placeholder:
                w.destroy()
        self.log_count = 0
        self.log_title.configure(text="SCORE CHANGES")
        self.log_placeholder.pack(pady=40)

    # ---- rendering --------------------------------------------------------

    def _render_summary(self):
        for w in self.summary.winfo_children():
            w.destroy()
        counts = {s: sum(1 for g in self.games if g["state"] == s) for s in ("in", "pre", "post")}
        for state, label, color in (("in", "LIVE", RED), ("pre", "UPCOMING", BLUE), ("post", "FINAL", MUTED)):
            chip = tk.Frame(self.summary, bg=BORDER)
            chip.pack(side="left", padx=4)
            tk.Label(chip, text=str(counts[state]), font=F_BODY_BOLD, fg=color, bg=BORDER,
                     padx=6).pack(side="left", pady=3)
            tk.Label(chip, text=label, font=F_SMALL_BOLD, fg=MUTED, bg=BORDER).pack(side="left", padx=(0, 8))

    def _render_games(self):
        for w in self.board.inner.winfo_children():
            w.destroy()
        self.cards = [self._build_card(self.board.inner, g) for g in self.games]

        if not self.games:
            self.cards.append(tk.Label(self.board.inner, text="No games on the scoreboard right now.",
                                       font=F_HEAD, fg=MUTED, bg=BG, pady=60))
        if getattr(self, "byes", None):
            self.cards.append(tk.Label(self.board.inner, text="On bye:  " + "   ".join(self.byes),
                                       font=F_BODY, fg=DIM, bg=BG, anchor="w", pady=10))
        self._layout_cards()

        # Clear the gold glow once highlights expire
        pending = [exp for exp, _ in self.highlight.values() if exp > time.time()]
        if pending:
            delay = int((min(pending) - time.time()) * 1000) + 100
            self.root.after(max(delay, 100), self._expire_highlights)

    def _expire_highlights(self):
        now = time.time()
        expired = [gid for gid, (exp, _) in self.highlight.items() if exp <= now]
        if expired:
            for gid in expired:
                del self.highlight[gid]
            self._render_games()

    def _on_board_resize(self, width):
        cols = max(1, width // CARD_MIN_WIDTH)
        if cols != self.cols:
            self.cols = cols
            self._layout_cards()

    def _layout_cards(self):
        cols = max(1, self.cols)
        inner = self.board.inner
        for c in range(12):
            inner.grid_columnconfigure(c, weight=1 if c < cols else 0, uniform="col" if c < cols else "")
        game_cards = [c for c in self.cards if isinstance(c, tk.Frame)]
        for i, card in enumerate(game_cards):
            card.grid(row=i // cols, column=i % cols, padx=(0, 12), pady=(0, 12), sticky="nsew")
        row = (len(game_cards) + cols - 1) // cols
        for extra in (c for c in self.cards if not isinstance(c, tk.Frame)):
            extra.grid(row=row, column=0, columnspan=cols, sticky="ew", padx=4)
            row += 1

    def _build_card(self, parent, g):
        live, final = g["state"] == "in", g["state"] == "post"
        hl_exp, hl_sides = self.highlight.get(g["id"], (0, set()))
        glowing = hl_exp > time.time()
        bg = CARD_LIVE if live else CARD
        border = GOLD if glowing else (RED if live else BORDER)

        card = tk.Frame(parent, bg=bg, highlightthickness=2, highlightbackground=border,
                        highlightcolor=border)

        # Status line
        head = tk.Frame(card, bg=bg)
        head.pack(fill="x", padx=16, pady=(12, 6))
        if live:
            tk.Label(head, text="●  LIVE", font=F_SMALL_BOLD, fg=RED, bg=bg).pack(side="left")
            tk.Label(head, text=g["detail"], font=F_SMALL_BOLD, fg=TEXT, bg=bg).pack(side="left", padx=(10, 0))
        elif final:
            tk.Label(head, text=g["detail"].upper(), font=F_SMALL_BOLD, fg=MUTED, bg=bg).pack(side="left")
        else:
            tk.Label(head, text=g["kickoff"], font=F_SMALL_BOLD, fg=BLUE, bg=bg).pack(side="left")
        if g["broadcast"]:
            tk.Label(head, text=g["broadcast"], font=F_SMALL, fg=DIM, bg=bg).pack(side="right")

        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=16)

        for side in ("away", "home"):
            self._team_row(card, g, side, bg, side in hl_sides and glowing)

        # Footer
        footer_lines = []
        if live:
            if g["down"]:
                footer_lines.append((("RED ZONE  ·  " if g["redzone"] else "") + g["down"],
                                     RED if g["redzone"] else TEXT, F_SMALL_BOLD))
            if g["last_play"]:
                footer_lines.append((g["last_play"], MUTED, F_SMALL))
        elif not final:
            if g["odds"]:
                footer_lines.append((g["odds"], MUTED, F_SMALL))
            if g["venue"]:
                footer_lines.append((g["venue"], DIM, F_SMALL))
        elif g["venue"]:
            footer_lines.append((g["venue"], DIM, F_SMALL))

        if footer_lines:
            tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(6, 0))
            foot = tk.Frame(card, bg=bg)
            foot.pack(fill="x", padx=16, pady=(6, 12))
            labels = []
            for text, fg, font in footer_lines:
                lbl = tk.Label(foot, text=text, font=font, fg=fg, bg=bg, justify="left", anchor="w",
                               wraplength=CARD_MIN_WIDTH - 50)
                lbl.pack(fill="x")
                labels.append(lbl)
            card.bind("<Configure>", lambda e, ls=labels: [l.configure(wraplength=max(e.width - 40, 100)) for l in ls])
        else:
            tk.Frame(card, bg=bg, height=8).pack()
        return card

    def _team_row(self, card, g, side, bg, just_scored):
        t = g[side]
        live, final, pre = g["state"] == "in", g["state"] == "post", g["state"] == "pre"
        lost = final and not t["winner"] and g["away"]["score"] != g["home"]["score"]

        row = tk.Frame(card, bg=bg)
        row.pack(fill="x", padx=16, pady=5)
        tk.Frame(row, bg=t["color"], width=5, height=48).pack(side="left", fill="y")

        logo = self.logos.get(t["abbr"])
        if logo:
            tk.Label(row, image=logo, bg=bg).pack(side="left", padx=(10, 8))
        else:
            tk.Label(row, text=t["abbr"], font=F_BODY_BOLD, fg=t["color"], bg=bg, width=5).pack(side="left", padx=(6, 4))

        names = tk.Frame(row, bg=bg)
        names.pack(side="left", fill="x", expand=True)
        name_row = tk.Frame(names, bg=bg)
        name_row.pack(anchor="w")
        tk.Label(name_row, text=t["name"], font=F_TEAM, fg=DIM if lost else TEXT, bg=bg).pack(side="left")
        if t["possession"] and live:
            tk.Label(name_row, text=" ●", font=F_BODY_BOLD, fg=GOLD, bg=bg).pack(side="left")
        sub = t["location"] + (f"  ·  {t['record']}" if t["record"] else "")
        tk.Label(names, text=sub, font=F_SMALL, fg=DIM if lost else MUTED, bg=bg).pack(anchor="w")

        if not pre:
            if just_scored:
                fg = GOLD
            elif lost:
                fg = DIM
            else:
                fg = TEXT
            tk.Label(row, text=str(t["score"]), font=F_SCORE, fg=fg, bg=bg).pack(side="right")
            if final and t["winner"]:
                tk.Label(row, text="◀", font=F_SMALL, fg=GREEN, bg=bg).pack(side="right", padx=(0, 6))

    # ---- misc -------------------------------------------------------------

    def _tick(self):
        if not self.fetching:
            self.remaining -= 1
            if self.remaining <= 0:
                self.refresh_now()
            elif self.updated_lbl.cget("fg") != RED:
                self.countdown_lbl.configure(text=f"Next update in {self.remaining}s  ·  F5 to refresh")
        self.root.after(1000, self._tick)

    def _on_wheel(self, event):
        w = self.root.winfo_containing(event.x_root, event.y_root)
        while w is not None:
            if w is self.board.canvas:
                self.board.scroll(int(-event.delta / 120))
                return
            if w is self.log.canvas:
                self.log.scroll(int(-event.delta / 120))
                return
            w = w.master


def main():
    try:  # crisp text on high-DPI Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    ScoreboardApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
