#!/usr/bin/env python3
"""Manage team/season/competition division labels for the SHSU RL archive.

Run from the project root:
  python scripts/divisions.py init
  python scripts/divisions.py list
  python scripts/divisions.py set --season fall-2026 --team orange --competition ccs --division "Division 3"
  python scripts/divisions.py clear --season fall-2026 --team orange --competition ccs

Free-text division labels also support "Open", "Challenger", "Tier 2", etc.
No game, series, player stat, or highlight records are modified.
"""
import argparse
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = Path(os.environ.get("SHSU_DB_PATH", ROOT / "shsu_records.sqlite3"))
MIGRATION = ROOT / "migrations" / "002_team_competition_divisions.sql"


def require_table(conn):
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_competition_divisions'"
    ).fetchone():
        raise SystemExit("Division table missing. First run: python scripts/divisions.py init")


def target_team_competition(conn, args):
    row = conn.execute("""
        SELECT ts.id AS team_season_id, c.id AS competition_id,
               sn.name AS season_name, t.name AS team_name, c.name AS competition_name
        FROM team_seasons ts
        JOIN seasons sn ON sn.id=ts.season_id
        JOIN teams t ON t.id=ts.team_id
        CROSS JOIN competitions c
        WHERE sn.slug=? AND t.slug=? AND c.slug=?
          AND EXISTS (SELECT 1 FROM series sr
                      WHERE sr.team_season_id=ts.id AND sr.competition_id=c.id)
    """, (args.season, args.team, args.competition)).fetchone()
    if row is None:
        raise SystemExit(
            "No recorded series for that season/team/competition. "
            "Check slugs with: python scripts/divisions.py list"
        )
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE,
                        help="Existing SQLite database path (defaults to SHSU_DB_PATH or root/shsu_records.sqlite3)")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("init", help="Create the table and seed Fall 2026 NECC divisions (safe to re-run)")
    actions.add_parser("list", help="Show recorded competitions and their division/tier labels")
    for action in ("set", "clear"):
        action_parser = actions.add_parser(action, help=f"{action.title()} a recorded division/tier")
        action_parser.add_argument("--season", required=True, help="e.g. fall-2026")
        action_parser.add_argument("--team", required=True, help="e.g. orange")
        action_parser.add_argument("--competition", required=True, help="e.g. necc, ccs, crl, pcl")
        if action == "set":
            action_parser.add_argument("--division", required=True, help='e.g. "Division 4", "Open"')
    args = parser.parse_args()

    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        parser.error(f"Database not found: {db_path}. No blank database will be created.")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        if args.action == "init":
            if not MIGRATION.is_file():
                raise SystemExit(f"Missing migration file: {MIGRATION}")
            conn.executescript(MIGRATION.read_text(encoding="utf-8"))
            count = conn.execute("SELECT COUNT(*) FROM team_competition_divisions").fetchone()[0]
            print(f"Division table ready; {count} placement(s) recorded. Existing labels preserved.")
            return

        require_table(conn)
        if args.action == "list":
            result = conn.execute("""
                SELECT sn.name AS season, t.name AS team, c.name AS competition,
                       d.division, COUNT(*) AS series_count
                FROM series sr
                JOIN team_seasons ts ON ts.id=sr.team_season_id
                JOIN seasons sn ON sn.id=ts.season_id
                JOIN teams t ON t.id=ts.team_id
                JOIN competitions c ON c.id=sr.competition_id
                LEFT JOIN team_competition_divisions d
                  ON d.team_season_id=ts.id AND d.competition_id=c.id
                GROUP BY ts.id, c.id
                ORDER BY substr(sn.slug, -4) DESC,
                         CASE WHEN sn.slug LIKE 'fall-%' THEN 3
                              WHEN sn.slug LIKE 'summer-%' THEN 2
                              WHEN sn.slug LIKE 'spring-%' THEN 1 ELSE 0 END DESC,
                         t.id, c.id
            """).fetchall()
            for entry in result:
                print(f"{entry['season']:14} | {entry['team'].strip():14} | "
                      f"{entry['competition']:5} | {(entry['division'] or '—'):17} | "
                      f"{entry['series_count']} series")
            if not result:
                print("No series have been recorded yet.")
            return

        row = target_team_competition(conn, args)
        if args.action == "set":
            division = args.division.strip()
            if not division:
                parser.error("--division must not be empty or only whitespace")
            conn.execute("""
                INSERT INTO team_competition_divisions
                    (team_season_id, competition_id, division)
                VALUES (?, ?, ?)
                ON CONFLICT(team_season_id, competition_id)
                DO UPDATE SET division=excluded.division
            """, (row["team_season_id"], row["competition_id"], division))
            print(f"Saved: {row['season_name']} · {row['team_name'].strip()} · "
                  f"{row['competition_name']} → {division}")
        else:
            cur = conn.execute("""
                DELETE FROM team_competition_divisions
                WHERE team_season_id=? AND competition_id=?
            """, (row["team_season_id"], row["competition_id"]))
            print("Division cleared." if cur.rowcount else "No division was recorded.")
        conn.commit()


if __name__ == "__main__":
    main()
