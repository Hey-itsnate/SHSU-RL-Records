#!/usr/bin/env python3
"""Preview (default) or commit a game highlight and featured SHSU players.

Real Example:
    python scripts/import_highlight.py --series-title "NECC Placements - Orange vs UNM Esports (1-1)" --game-number 3 --title "BBL Double" --url "https://medal.tv/games/rocket-league/clips/nAv4BBHHf6k0YNC70?invite=cr-MSw1bHAsMjY5NTE1NDg" --players "Nyx" 


Examples:
  python scripts/import_highlight.py --game-id 42 --title "Zero-second save" \
    --url "https://www.youtube.com/watch?v=..." --players "Nyx,Coldest"
  python scripts/import_highlight.py --series-id 12 --game-number 3 \
    --title "Overtime winner" --url "https://clips.twitch.tv/..." --players "Tango" --commit
  python scripts/import_highlight.py --series-title "TCU" --game-number 4 \
    --title "Team play" --url "https://youtu.be/..." --players "Nyx,Tango"
Run from project root. No writes unless --commit.

"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_DB = Path(__file__).resolve().parent.parent / "shsu_records.sqlite3"

def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--game-id", type=int)
    target.add_argument("--series-id", type=int)
    target.add_argument("--series-title", help="Full or partial series title; must match exactly one series")
    p.add_argument("--game-number", type=int, help="Required when selecting a series")
    p.add_argument("--title", required=True, help="Descriptive highlight title")
    p.add_argument("--url", required=True, help="Public HTTPS clip URL")
    p.add_argument("--players", default="", help="Comma-separated SHSU display names, slugs, or aliases; optional")
    p.add_argument("--commit", action="store_true", help="Insert highlight after showing preview")
    return p

def get_one(conn, query, args=()):
    return conn.execute(query, args).fetchone()

def resolve_game(conn, args):
    if args.game_id is not None:
        if args.game_number is not None:
            raise ValueError("--game-number cannot be combined with --game-id")
        game = get_one(conn, "SELECT * FROM games WHERE id=?", (args.game_id,))
        if game is None:
            raise ValueError(f"Unknown game ID {args.game_id}")
        return game
    if args.game_number is None or args.game_number < 1:
        raise ValueError("--game-number must be a positive integer when selecting a series")
    if args.series_id is not None:
        sr = get_one(conn, "SELECT * FROM series WHERE id=?", (args.series_id,))
        if not sr:
            raise ValueError(f"Unknown series ID {args.series_id}")
    else:
        search = args.series_title.strip()
        if not search:
            raise ValueError("--series-title must not be blank")
        matches = conn.execute(
            "SELECT id, title FROM series WHERE title LIKE ? COLLATE NOCASE ORDER BY id",
            ("%" + search + "%",)
        ).fetchall()
        if not matches:
            raise ValueError(f"No series title containing {search!r}")
        if len(matches) != 1:
            options = "; ".join(f"{r['id']}: {r['title']}" for r in matches[:20])
            raise ValueError(f"Ambiguous series title ({len(matches)} matches). Use --series-id. Matches: {options}")
        sr = matches[0]
    game = get_one(conn, "SELECT * FROM games WHERE series_id=? AND game_number=?",
                   (sr["id"], args.game_number))
    if game is None:
        raise ValueError(f"Series {sr['id']} has no game number {args.game_number}")
    return game

def resolve_players(conn, csv_text, game_id):
    names = [v.strip() for v in csv_text.split(",") if v.strip()]
    if len(names) != len(set(n.casefold() for n in names)):
        raise ValueError("Duplicate player name supplied in --players")
    available = conn.execute("""
        SELECT p.id, p.slug, p.display_name
        FROM player_game_stats pg JOIN players p ON p.id=pg.player_id
        WHERE pg.game_id=? ORDER BY p.display_name
    """, (game_id,)).fetchall()
    aliases = conn.execute("""
        SELECT a.player_id, a.alias FROM player_aliases a
        JOIN player_game_stats pg ON pg.player_id=a.player_id
        WHERE pg.game_id=?
    """, (game_id,)).fetchall()
    by_id = {r["id"]: r for r in available}
    lookup = {}
    for r in available:
        for word in (r["slug"], r["display_name"]):
            lookup.setdefault(word.casefold(), set()).add(r["id"])
    for r in aliases:
        lookup.setdefault(r["alias"].casefold(), set()).add(r["player_id"])
    selected = []
    for name in names:
        matches = lookup.get(name.casefold(), set())
        if not matches:
            options = ", ".join(r["display_name"] for r in available) or "(none)"
            raise ValueError(f"Player {name!r} is not a recognized SHSU participant in this game. Available: {options}")
        if len(matches) != 1:
            raise ValueError(f"Player name {name!r} is ambiguous; use the exact player slug")
        player_id = next(iter(matches))
        if player_id in [v["id"] for v in selected]:
            raise ValueError(f"Multiple --players entries refer to {by_id[player_id]['display_name']}")
        selected.append(by_id[player_id])
    return selected, available

def main():
    args = parser().parse_args()
    if not args.db.is_file():
        raise ValueError(f"Database does not exist: {args.db}")
    title = args.title.strip()
    if not title:
        raise ValueError("--title must not be blank")
    url = args.url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ValueError("--url must be a full public HTTPS URL")
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        required_tables = {"highlights", "highlight_players"}
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required_tables.issubset(existing):
            raise ValueError("Highlight tables are missing. Apply 001_add_highlights.sql first.")
        game = resolve_game(conn, args)
        if game["status"] != "played":
            raise ValueError(f"Game {game['id']} is {game['status']}; highlights require a played game")
        info = get_one(conn, """
            SELECT sr.id AS series_id, sr.title AS series_title, sr.played_on,
                   sr.round_label, o.name AS opponent, t.name AS team_name,
                   c.name AS competition, se.name AS season_name
            FROM series sr JOIN opponents o ON o.id=sr.opponent_id
            JOIN team_seasons ts ON ts.id=sr.team_season_id
            JOIN teams t ON t.id=ts.team_id
            JOIN competitions c ON c.id=sr.competition_id
            JOIN seasons se ON se.id=sr.season_id
            WHERE sr.id=?
        """, (game["series_id"],))
        selected, available = resolve_players(conn, args.players, game["id"])
        duplicate = get_one(conn,
            "SELECT id, title FROM highlights WHERE game_id=? AND url=?",
            (game["id"], url))
        if duplicate:
            raise ValueError(f"URL already registered for this game (highlight #{duplicate['id']}: {duplicate['title']})")
        print("\n=== HIGHLIGHT IMPORT PREVIEW ===")
        print(f"Season:      {info['season_name']}")
        print(f"Series:      #{info['series_id']} — {info['series_title']}")
        print(f"Match:       {info['team_name']} vs {info['opponent']} [{info['competition']}]")
        print(f"Date / round:{' ' if info['played_on'] else ''}{info['played_on'] or 'unknown'} / {info['round_label'] or '—'}")
        print(f"Game:        #{game['id']} — Game {game['game_number']} "
              f"({game['team_goals']}–{game['opponent_goals']})")
        print(f"Highlight:   {title}")
        print(f"URL:         {url}")
        print(f"Featured:    {', '.join(p['display_name'] for p in selected) if selected else '(none tagged)'}")
        print(f"Players in game: {', '.join(p['display_name'] for p in available)}")
        print(f"Mode:        {'COMMIT' if args.commit else 'PREVIEW ONLY (no changes)'}")
        if not args.commit:
            print("\nTo save, repeat this command with --commit.")
            return
        # BEGIN IMMEDIATE prevents a concurrent duplicate slipping between check and INSERT.
        conn.execute("BEGIN IMMEDIATE")
        if get_one(conn, "SELECT id FROM highlights WHERE game_id=? AND url=?", (game["id"], url)):
            raise ValueError("Highlight URL was added concurrently; refusing duplicate")
        cur = conn.execute("INSERT INTO highlights(game_id,title,url) VALUES (?,?,?)",
                           (game["id"], title, url))
        highlight_id = cur.lastrowid
        for p in selected:
            conn.execute("INSERT INTO highlight_players(highlight_id,player_id) VALUES (?,?)",
                         (highlight_id, p["id"]))
        conn.commit()
        print(f"\nCOMMITTED: highlight #{highlight_id} with {len(selected)} player association(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
