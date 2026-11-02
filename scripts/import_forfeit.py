#!/usr/bin/env python3
"""Preview or record a complete awarded-forfeit series in the SHSU SQLite record book.

Standard library only. Run from the project root:
    python scripts/import_forfeit.py --team orange --season fall-2026 \
        --competition necc --opponent "University of New Mexico" \
        --title "NECC vs University of New Mexico" --round "NECC Week 1" \
        --played-on 2026-09-24 --result win

Append --commit after checking the preview. No player-game stats are created.
"""
import argparse
import sqlite3
from datetime import date
from pathlib import Path

DB_DEFAULT = Path(__file__).resolve().parent.parent / "shsu_records.sqlite3"


def arguments():
    parser = argparse.ArgumentParser(
        description="Record one full-series win/loss awarded by forfeit (preview by default)."
    )
    parser.add_argument("--db", type=Path, default=DB_DEFAULT, help="Existing SQLite database path")
    parser.add_argument("--season", default="fall-2026")
    parser.add_argument("--team", required=True, choices=("orange", "white", "black"))
    parser.add_argument("--competition", required=True, choices=("necc", "ccs", "crl"))
    parser.add_argument("--opponent", required=True, help="Opponent name; (FF) is appended automatically")
    parser.add_argument("--title", required=True, help="Series title shown on the website")
    parser.add_argument("--round", default="", help="Optional round/week label")
    parser.add_argument("--played-on", required=True, help="Official match date, YYYY-MM-DD")
    parser.add_argument("--result", required=True, choices=("win", "loss"))
    parser.add_argument("--games", type=int, default=4, help="Awarded game count (default: 4)")
    parser.add_argument("--url", default="", help="Optional genuine Ballchasing group URL")
    parser.add_argument("--notes", default="", help="Optional additional notes")
    parser.add_argument("--commit", action="store_true", help="Actually save; default is preview")
    return parser.parse_args()


def main():
    args = arguments()
    if not args.db.is_file():
        raise ValueError(f"Database not found: {args.db} (will not create a new database)")
    if args.games <= 0:
        raise ValueError("--games must be at least 1")
    try:
        played_on = date.fromisoformat(args.played_on)
        if played_on.isoformat() != args.played_on:
            raise ValueError
    except ValueError:
        raise ValueError("--played-on must be a valid YYYY-MM-DD date") from None
    raw_opponent = args.opponent.strip()
    if not raw_opponent or not args.title.strip():
        raise ValueError("--opponent and --title must not be empty")
    opponent_name = (
        raw_opponent if raw_opponent.casefold().endswith("(ff)")
        else f"{raw_opponent} (FF)"
    )
    if args.url and not args.url.startswith("https://ballchasing.com/"):
        raise ValueError("--url, if supplied, must be a real ballchasing.com HTTPS link")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        season = conn.execute(
            "SELECT id FROM seasons WHERE slug=?", (args.season,)
        ).fetchone()
        if not season:
            raise ValueError(f"Unknown season: {args.season}")
        team = conn.execute("""
            SELECT ts.id FROM team_seasons ts
            JOIN teams t ON t.id=ts.team_id
            WHERE ts.season_id=? AND t.slug=?
        """, (season["id"], args.team)).fetchone()
        if not team:
            raise ValueError(f"Roster {args.team} is not registered for {args.season}")
        competition = conn.execute(
            "SELECT id FROM competitions WHERE slug=?", (args.competition,)
        ).fetchone()
        if not competition:
            raise ValueError(f"Unknown competition: {args.competition}")

        # No replay IDs exist for full forfeits. Match identity by roster,
        # competition, opponent name with (FF), and official match date.
        duplicates = conn.execute("""
            SELECT sr.id, sr.result, sr.official_games_won, sr.official_games_lost
            FROM series sr
            JOIN opponents o ON o.id=sr.opponent_id
            WHERE sr.season_id=? AND sr.team_season_id=? AND sr.competition_id=?
              AND LOWER(o.name)=LOWER(?) AND sr.played_on=?
        """, (season["id"], team["id"], competition["id"],
              opponent_name, args.played_on)).fetchall()
        if duplicates:
            raise ValueError(
                f"Forfeit series already exists for this team/opponent/date "
                f"(series id {duplicates[0]['id']}); refusing duplicate import"
            )

        is_win = args.result == "win"
        official_wins = args.games if is_win else 0
        official_losses = 0 if is_win else args.games
        status = "forfeit_win" if is_win else "forfeit_loss"
        print(f"\nSERIES: {args.title.strip()}")
        print(f"Team: {args.team.upper()} | {args.competition.upper()} | {args.season}")
        print(f"Opponent: {opponent_name} | Date: {args.played_on}")
        print(f"Result: {'WIN' if is_win else 'LOSS'} by forfeit "
              f"| Official games: {official_wins}-{official_losses}")
        print(f"Games: {args.games} × {status} | Played replays: 0 | Player stats: 0")
        if args.round:
            print(f"Round: {args.round}")
        if args.url:
            print(f"Source URL: {args.url}")
        if not args.commit:
            print("\nPREVIEW ONLY. Nothing written. Add --commit to save this series.")
            return

        with conn:
            series_cols = {
                row["name"] for row in conn.execute("PRAGMA table_info(series)")
            }
            if "title" not in series_cols:
                conn.execute("ALTER TABLE series ADD COLUMN title TEXT")
            conn.execute("INSERT OR IGNORE INTO opponents(name) VALUES (?)",
                         (opponent_name,))
            opponent = conn.execute("SELECT id FROM opponents WHERE name=?",
                                    (opponent_name,)).fetchone()
            notes = "Entire series awarded by forfeit; no games played."
            if args.notes.strip():
                notes += " " + args.notes.strip()
            cur = conn.execute("""
                INSERT INTO series (
                    season_id, team_season_id, competition_id, opponent_id,
                    played_on, round_label, result, official_games_won,
                    official_games_lost, ballchasing_group_url, notes, title
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (season["id"], team["id"], competition["id"], opponent["id"],
                  args.played_on, args.round.strip(),
                  "W" if is_win else "L", official_wins, official_losses,
                  args.url.strip() or None, notes, args.title.strip()))
            series_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO games(series_id, game_number, status) VALUES (?, ?, ?)",
                [(series_id, number, status) for number in range(1, args.games + 1)]
            )
        print(f"\nCOMMITTED series_id={series_id}: {args.games} awarded games, "
              "0 replay IDs, 0 player-game appearances.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, sqlite3.Error) as exc:
        raise SystemExit(f"ERROR: {exc}")
