#!/usr/bin/env python3
"""Seed Fall 2026 SHSU player IGNs, Discord handles, and substitute rosters.

Run from any directory:
    python seed_player_aliases.py

Run repeatedly without creating duplicate rows.
Existing database must be initialized with init_db.py first.
"""
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
DB = ROOT / "shsu_records.sqlite3"

# Player slug -> Ballchasing in-game names. Store unique spellings, not
# capitalization variants: SQLite uses COLLATE NOCASE on the alias column.
IGN_ALIASES = {
    "nyx": ["nyx"],
    "coldest": ["cohldest"],
    "tango": ["tango"],  # Also matches TANGO.
    "noizy": ["noizy boy."],
    "tragic": ["Tragiic AC3"],
    "arctic": ["Itz Arxtic"],
    "festah": ["BubbaTran2008", "astrxthun3r"],
    "wind": ["IhaveAnIdeaaaaa", "Ihaveandelea", "Wind", "ash"],
    "lance": ["lancse"],
    "elmblue": ["Elmblue2416"],
}

# Account usernames are NOT used to match Ballchasing player names.
# Tragic left out because the supplied @tragiic*. contains an asterisk,
# which is not a valid current Discord username; confirm exact spelling.
DISCORD_HANDLES = {
    "nyx": "HeyItsNate",
    "coldest": "cxldest",
    "tango": "coletango",
    "noizy": "noizyboy.6",
    "arctic": "bgwilly3",
    "festah": "pillslipper",
    "wind": "wind7716",
    "lance": "iamfasterthanyoubuddy",
    "elmblue": "elmbleu2416",
}

# Roles specifically requested as subs; players retain their home rosters.
SUBSTITUTE_TEAMS = {
    "noizy": ["orange"],
    "tragic": ["orange"],
    "wind": ["white"],
}

if not DB.is_file():
    raise SystemExit(
        f"Missing database {DB}. First run: python init_db.py"
    )

with sqlite3.connect(DB) as conn:
    conn.execute("PRAGMA foreign_keys = ON")
    # Also supports databases created using the original starter schema.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS player_social_accounts (
            id INTEGER PRIMARY KEY,
            player_id INTEGER NOT NULL REFERENCES players(id),
            platform TEXT NOT NULL,
            username TEXT NOT NULL COLLATE NOCASE,
            UNIQUE(player_id, platform),
            UNIQUE(platform, username)
        )
    """)
    players = dict(conn.execute("SELECT slug, id FROM players"))
    expected = set(IGN_ALIASES)
    missing = expected - set(players)
    if missing:
        raise ValueError("Missing players; initialize roster first: " + ", ".join(sorted(missing)))

    # Validate that no IGN belongs to two distinct players, then insert.
    for slug, names in IGN_ALIASES.items():
        player_id = players[slug]
        for alias in names:
            alias = alias.strip()
            match = conn.execute(
                "SELECT player_id FROM player_aliases WHERE alias = ? COLLATE NOCASE",
                (alias,)
            ).fetchone()
            if match is not None and match[0] != player_id:
                raise ValueError(f"IGN {alias!r} already assigned to another player")
            conn.execute(
                "INSERT OR IGNORE INTO player_aliases(player_id, alias) VALUES (?, ?)",
                (player_id, alias)
            )

    # Store Discord separately. One current username per person.
    for slug, username in DISCORD_HANDLES.items():
        player_id = players[slug]
        match = conn.execute("""
            SELECT player_id FROM player_social_accounts
            WHERE platform='discord' AND username=? COLLATE NOCASE
        """, (username,)).fetchone()
        if match is not None and match[0] != player_id:
            raise ValueError(f"Discord username {username!r} already belongs to another player")
        conn.execute("""
            INSERT INTO player_social_accounts(player_id, platform, username)
            VALUES (?, 'discord', ?)
            ON CONFLICT(player_id, platform) DO UPDATE
            SET username=excluded.username
        """, (player_id, username))

    # White players sub for Orange; Wind subs for White.
    for slug, team_slugs in SUBSTITUTE_TEAMS.items():
        for team_slug in team_slugs:
            team_season = conn.execute("""
                SELECT ts.id
                FROM team_seasons ts
                JOIN teams t ON t.id=ts.team_id
                JOIN seasons s ON s.id=ts.season_id
                WHERE s.slug='fall-2026' AND t.slug=?
            """, (team_slug,)).fetchone()
            if team_season is None:
                raise ValueError(f"Missing Fall 2026 team: {team_slug}")
            conn.execute("""
                INSERT INTO roster_memberships(team_season_id, player_id, role)
                VALUES (?, ?, 'substitute')
                ON CONFLICT(team_season_id, player_id)
                DO UPDATE SET role='substitute'
            """, (team_season[0], players[slug]))

    # Lance is a member of Black. The source list did not indicate a reserve
    # designation, so don't infer one from the original seed script.
    conn.execute("""
        UPDATE roster_memberships SET role='member'
        WHERE player_id=? AND team_season_id=(
            SELECT ts.id FROM team_seasons ts
            JOIN seasons s ON s.id=ts.season_id
            JOIN teams t ON t.id=ts.team_id
            WHERE s.slug='fall-2026' AND t.slug='black'
        ) AND role='reserve'
    """, (players["lance"],))
    conn.commit()

    print("Saved Fall 2026 aliases / Discord accounts / substitute memberships.")
    print("\nIGN alias mappings:")
    for name, alias, slug in conn.execute("""
        SELECT p.display_name, a.alias, p.slug
        FROM player_aliases a JOIN players p ON p.id=a.player_id
        ORDER BY p.display_name, a.alias
    """):
        print(f"  {alias!r:<22} -> {name}")
    print("\nFall 2026 rosters:")
    for row in conn.execute("""
        SELECT t.name, p.display_name, rm.role
        FROM roster_memberships rm
        JOIN team_seasons ts ON ts.id=rm.team_season_id
        JOIN teams t ON t.id=ts.team_id
        JOIN seasons s ON s.id=ts.season_id
        JOIN players p ON p.id=rm.player_id
        WHERE s.slug='fall-2026'
        ORDER BY t.name,p.display_name
    """):
        print("  " + " | ".join(row))

print(f"Database: {DB}")
