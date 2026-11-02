#!/usr/bin/env python3
"""Create or open SHSU Records SQLite database and seed roster identities."""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "shsu_records.sqlite3"

with sqlite3.connect(DB) as conn:
    conn.execute("PRAGMA foreign_keys = ON")    # Enable foreign key constraints
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))   # Create tables if they don't exist

    # Insert season for Fall 2026 and teams, competitions, players, and roster memberships
    conn.execute("INSERT OR IGNORE INTO seasons(slug,name) VALUES(?,?)",
                 ("fall-2026", "Fall 2026"))    
    for slug, name in [("orange","SHSU Orange"),("white","SHSU White"),("black","SHSU Black")]:
        conn.execute("INSERT OR IGNORE INTO teams(slug,name) VALUES(?,?)", (slug,name))
        conn.execute("""INSERT OR IGNORE INTO team_seasons(season_id,team_id)
                        SELECT s.id,t.id FROM seasons s CROSS JOIN teams t
                        WHERE s.slug=? AND t.slug=?""",("fall-2026",slug))
    for slug, name in [("necc","NECC"),("ccs","CCS"),("crl","CRL")]:
        conn.execute("INSERT OR IGNORE INTO competitions(slug,name) VALUES(?,?)",(slug,name))
    roster = {"orange":["Nyx","Coldest","Tango"],
              "white":["Noizy","Arctic","Tragic"],
              "black":["Festah","Wind","Elmblue","Lance"]}
    for team, names in roster.items():
        for name in names:
            slug=name.lower()
            conn.execute("INSERT OR IGNORE INTO players(slug,display_name) VALUES(?,?)",
                         (slug,name))
            conn.execute("""INSERT OR IGNORE INTO roster_memberships(team_season_id,player_id,role)
                            SELECT ts.id,p.id,? FROM team_seasons ts
                            JOIN teams t ON t.id=ts.team_id
                            JOIN seasons s ON s.id=ts.season_id
                            CROSS JOIN players p
                            WHERE s.slug=? AND t.slug=? AND p.slug=?""",
                         ("reserve" if name=="Lance" else "starter","fall-2026",team,slug))
    conn.commit()

print(f"Ready: {DB}")
print("Season and roster identities seeded. No game statistics have been fabricated.")
