"""Read-only queries for the SHSU record book.

All queries use the existing starter schema. The public site never writes to the DB.
These functions return plain dictionaries, so they can also be reused by a static
site generator when deploying to GitHub Pages.
"""
import sqlite3
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "shsu_records.sqlite3"

STANDOUT_TYPES = [
    {"field": "score_550", "label": "550+ Score", "icon": "💯", "rule": "550+ points"},
    {"field": "hat_trick", "label": "Hat Trick", "icon": "⚽", "rule": "3+ goals"},
    {"field": "playmaker", "label": "Playmaker", "icon": "🤝", "rule": "3+ assists"},
    {"field": "offensive_engine", "label": "Offensive Engine", "icon": "🔥", "rule": "2+ goals AND 2+ assists"},
    {"field": "anchor", "label": "Anchor", "icon": "⚓", "rule": "5+ saves"},
    {"field": "defensive_problem", "label": "Defensive Problem", "icon": "🚌", "rule": "4+ saves AND (1+ G+A OR 3+ demos)"},
    {"field": "two_way", "label": "Two-Way", "icon": "🛡️⚔️", "rule": "3+ saves AND 2+ G+A"},
    {"field": "elite_disruptor", "label": "Elite Disruptor", "icon": "💥", "rule": "4+ demos AND 3+ G+A+S"},
    {"field": "all_around", "label": "All-Around", "icon": "👑", "rule": "7+ G+A+S"},
]

RECORD_METRICS = [
    {"field": "score", "label": "Highest Score", "icon": "🏆"},
    {"field": "goals", "label": "Most Goals", "icon": "⚽"},
    {"field": "assists", "label": "Most Assists", "icon": "🤝"},
    {"field": "saves", "label": "Most Saves", "icon": "🧤"},
    {"field": "shots", "label": "Most Shots", "icon": "🎯"},
    {"field": "ga", "label": "Most G+A", "icon": "🔥"},
    {"field": "gas", "label": "Most G+A+S", "icon": "⭐"},
    {"field": "demos", "label": "Most Demolitions", "icon": "💥"},
]
TEAM_RECORD_METRICS = RECORD_METRICS + [
    {"field": "goal_diff", "label": "Largest Goal Differential", "icon": "📈"}
]


def open_database(db_path=DEFAULT_DATABASE):
    """Never use sqlite3.connect with an absent path: it silently makes a new DB."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(
            f"SQLite database not found: {path}. Copy your existing "
            "shsu_records.sqlite3 beside app.py; do not initialize a blank copy."
        )
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def one(cursor):
    r = cursor.fetchone()
    return dict(r) if r else None


def seasons(conn):
    return rows(conn.execute("SELECT * FROM seasons ORDER BY id DESC"))


def season(conn, slug):
    return one(conn.execute("SELECT * FROM seasons WHERE slug = ?", (slug,)))


def team_seasons(conn, season_id):
    return rows(conn.execute("""
        SELECT ts.id, t.id AS team_id, t.slug, t.name
        FROM team_seasons ts JOIN teams t ON t.id=ts.team_id
        WHERE ts.season_id=? ORDER BY CASE t.slug
            WHEN 'orange' THEN 1 WHEN 'white' THEN 2 WHEN 'black' THEN 3 ELSE 4 END, t.name
    """, (season_id,)))


def team_season(conn, season_id, team_slug):
    return one(conn.execute("""
        SELECT ts.id, ts.season_id, t.slug, t.name
        FROM team_seasons ts JOIN teams t ON t.id=ts.team_id
        WHERE ts.season_id=? AND t.slug=?
    """, (season_id, team_slug)))


def roster(conn, team_season_id):
    return rows(conn.execute("""
        SELECT p.id, p.slug, p.display_name, rm.role,
          (SELECT username FROM player_social_accounts sa
           WHERE sa.player_id=p.id AND sa.platform='discord' LIMIT 1) AS discord,
          (SELECT GROUP_CONCAT(alias, ', ') FROM player_aliases a
           WHERE a.player_id=p.id) AS aliases
        FROM roster_memberships rm
        JOIN players p ON p.id=rm.player_id
        WHERE rm.team_season_id=?
        ORDER BY CASE rm.role WHEN 'starter' THEN 0
              WHEN 'member' THEN 1 WHEN 'reserve' THEN 2 ELSE 3 END, p.display_name
    """, (team_season_id,)))


def team_competitions(conn, team_season_ids):
    """All competitions a roster actually entered, with its season-specific division.

    One division/tier label per (team-season, competition). A NULL division
    means no verified placement has been recorded; never infer one.
    """
    ids = sorted(set(int(i) for i in team_season_ids))
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    return rows(conn.execute(f"""
        SELECT ts.id AS team_season_id, sn.slug AS season_slug,
               sn.name AS season_name, t.slug AS team_slug,
               c.id AS competition_id, c.slug AS competition_slug,
               c.name AS competition, d.division AS division,
               COUNT(sr.id) AS series_count
        FROM series sr
        JOIN team_seasons ts ON ts.id=sr.team_season_id
        JOIN seasons sn ON sn.id=ts.season_id
        JOIN teams t ON t.id=ts.team_id
        JOIN competitions c ON c.id=sr.competition_id
        LEFT JOIN team_competition_divisions d
          ON d.team_season_id=ts.id AND d.competition_id=c.id
        WHERE sr.team_season_id IN ({marks})
        GROUP BY ts.id, c.id
        ORDER BY substr(sn.slug, -4) DESC,
                 CASE WHEN sn.slug LIKE 'fall-%' THEN 3
                      WHEN sn.slug LIKE 'summer-%' THEN 2
                      WHEN sn.slug LIKE 'spring-%' THEN 1 ELSE 0 END DESC,
                 CASE c.slug WHEN 'necc' THEN 0 WHEN 'ccs' THEN 1
                             WHEN 'crl' THEN 2 WHEN 'pcl' THEN 3 ELSE 4 END,
                 c.name
    """, ids))


def division_for_series(conn, series_id):
    """Division/tier registered for this specific series' team and season."""
    row = conn.execute("""
        SELECT d.division
        FROM series sr
        LEFT JOIN team_competition_divisions d
          ON d.team_season_id=sr.team_season_id AND d.competition_id=sr.competition_id
        WHERE sr.id=?
    """, (series_id,)).fetchone()
    return row["division"] if row else None


def team_series(conn, team_season_id):
    return rows(conn.execute("""
        SELECT sr.id, sr.played_on, sr.round_label, sr.result,
               sr.official_games_won, sr.official_games_lost,
               sr.ballchasing_group_url, sr.notes,
               o.name AS opponent, c.name AS competition,
               d.division AS division,
               (SELECT COUNT(*) FROM games g
                WHERE g.series_id=sr.id AND g.status='played') AS games_played
        FROM series sr
        JOIN opponents o ON o.id=sr.opponent_id
        JOIN competitions c ON c.id=sr.competition_id
        LEFT JOIN team_competition_divisions d
          ON d.team_season_id=sr.team_season_id AND d.competition_id=sr.competition_id
        WHERE sr.team_season_id=?
        ORDER BY sr.played_on DESC, sr.id DESC
    """, (team_season_id,)))


def team_season_records(conn, team_season_id):
    """Official W/L for recorded series, plus played games and awarded forfeits.

    Series results are kept separate from physical games: an awarded forfeit
    counts in official standings but has no invented score or player appearances.
    Counts include only series already imported for this roster/season.
    """
    series_rows = rows(conn.execute("""
        SELECT c.name AS competition, c.slug AS competition_slug,
               d.division AS division,
               sr.result, sr.official_games_won, sr.official_games_lost,
               (SELECT COUNT(*) FROM games g
                WHERE g.series_id=sr.id AND g.status='played'
                  AND g.team_goals>g.opponent_goals) AS played_wins,
               (SELECT COUNT(*) FROM games g
                WHERE g.series_id=sr.id AND g.status='played'
                  AND g.team_goals<g.opponent_goals) AS played_losses,
               (SELECT COUNT(*) FROM games g
                WHERE g.series_id=sr.id AND g.status='forfeit_win') AS forfeit_wins,
               (SELECT COUNT(*) FROM games g
                WHERE g.series_id=sr.id AND g.status='forfeit_loss') AS forfeit_losses
        FROM series sr
        JOIN competitions c ON c.id=sr.competition_id
        LEFT JOIN team_competition_divisions d
          ON d.team_season_id=sr.team_season_id AND d.competition_id=sr.competition_id
        WHERE sr.team_season_id=?
        ORDER BY sr.played_on, sr.id
    """, (team_season_id,)))

    def empty_stats(name=None, division=None):
        return {
            "competition": name, "division": division,
            "series": 0, "wins": 0, "losses": 0,
            "ties": 0, "official_wins": 0, "official_losses": 0,
            "played_wins": 0, "played_losses": 0,
            "forfeit_wins": 0, "forfeit_losses": 0,
        }

    total = empty_stats("All competitions")
    by_competition = {}
    for game_series in series_rows:
        key = game_series["competition_slug"]
        group = by_competition.setdefault(
            key, empty_stats(game_series["competition"], game_series["division"])
        )
        for stat in (total, group):
            stat["series"] += 1
            if game_series["result"] == "W": stat["wins"] += 1
            elif game_series["result"] == "L": stat["losses"] += 1
            elif game_series["result"] == "T": stat["ties"] += 1
            for target, source in (
                ("official_wins", "official_games_won"),
                ("official_losses", "official_games_lost"),
                ("played_wins", "played_wins"),
                ("played_losses", "played_losses"),
                ("forfeit_wins", "forfeit_wins"),
                ("forfeit_losses", "forfeit_losses"),
            ):
                stat[target] += game_series[source]
    order = {"necc": 0, "ccs": 1, "crl": 2}
    breakdown = [
        by_competition[key]
        for key in sorted(by_competition, key=lambda key: (order.get(key, 99), key))
    ]
    return {"total": total, "by_competition": breakdown}


def team_game_rows(conn, season_id, team_season_id=None):
    """One row per played game with all 3 SHSU player rows. Partial games excluded."""
    params = [season_id]
    team_clause = ""
    if team_season_id is not None:
        team_clause = "AND sr.team_season_id=?"
        params.append(team_season_id)
    result = rows(conn.execute(f"""
        SELECT g.id AS game_id, g.game_number, sr.id AS series_id,
               sr.team_season_id, t.slug AS team_slug, t.name AS team_name,
               sr.played_on, o.name AS opponent, c.name AS competition,
               g.team_goals AS goals, g.opponent_goals,
               g.team_goals-g.opponent_goals AS goal_diff,
               SUM(pg.score) AS score, SUM(pg.assists) AS assists,
               SUM(pg.saves) AS saves, SUM(pg.shots) AS shots,
               SUM(pg.demos) AS demos,
               SUM(pg.goals+pg.assists) AS ga,
               SUM(pg.goals+pg.assists+pg.saves) AS gas,
               g.ballchasing_replay_url AS replay_url
        FROM games g JOIN series sr ON sr.id=g.series_id
        JOIN team_seasons ts ON ts.id=sr.team_season_id
        JOIN teams t ON t.id=ts.team_id
        JOIN opponents o ON o.id=sr.opponent_id
        JOIN competitions c ON c.id=sr.competition_id
        JOIN player_game_stats pg ON pg.game_id=g.id
        WHERE sr.season_id=? AND g.status='played' {team_clause}
        GROUP BY g.id
        HAVING COUNT(pg.id)=3
        ORDER BY sr.played_on DESC, sr.id DESC, g.game_number DESC
    """, params))
    return result


def aggregate_games(games):
    """Team averages use actual complete 3-player game data, never forfeits."""
    fields = ("score", "goals", "assists", "saves", "shots", "demos", "ga", "gas")
    if not games:
        return None
    return {
        "games": len(games),
        "win": sum(g["goals"] > g["opponent_goals"] for g in games),
        "loss": sum(g["goals"] < g["opponent_goals"] for g in games),
        **{field: round(sum(g[field] for g in games) / len(games), 2) for field in fields},
        "goals_against": round(sum(g["opponent_goals"] for g in games) / len(games), 2),
        "goal_diff": round(sum(g["goal_diff"] for g in games) / len(games), 2),
    }


def records_for_games(games, metrics=TEAM_RECORD_METRICS):
    if not games:
        return []
    out = []
    for metric in metrics:
        highest = max(g[metric["field"]] for g in games)
        out.append({**metric, "value": highest,
                    "winners": [g for g in games if g[metric["field"]] == highest]})
    return out


def individual_games(conn, season_id):
    return rows(conn.execute("""
        SELECT v.*, p.slug AS player_slug, p.display_name,
               g.game_number, g.ballchasing_replay_url AS replay_url,
               sr.played_on, sr.id AS series_id,
               o.name AS opponent, c.name AS competition,
               t.name AS team_name, t.slug AS team_slug
        FROM v_player_game_standouts v
        JOIN players p ON p.id=v.player_id
        JOIN games g ON g.id=v.game_id
        JOIN series sr ON sr.id=g.series_id
        JOIN opponents o ON o.id=sr.opponent_id
        JOIN competitions c ON c.id=sr.competition_id
        JOIN team_seasons ts ON ts.id=sr.team_season_id
        JOIN teams t ON t.id=ts.team_id
        WHERE v.season_id=?
        ORDER BY sr.played_on DESC, sr.id DESC, g.game_number DESC
    """, (season_id,)))


def individual_records(games, metrics=RECORD_METRICS):
    return records_for_games(games, metrics)


def personal_best_scores(games):
    """Best game by score for each player, with ties represented by earliest winner."""
    out = {}
    for g in games:
        pid = g["player_id"]
        if pid not in out or g["score"] > out[pid]["score"]:
            out[pid] = g
    return sorted(out.values(), key=lambda g: (-g["score"], g["display_name"]))


def leaderboard(conn, season_id):
    """All rostered season players, including those with zero imported games."""
    return rows(conn.execute("""
        SELECT p.id AS player_id, p.slug, p.display_name,
               COALESCE(l.games_played,0) AS games_played,
               COALESCE(l.standout_games,0) AS standout_games,
               l.standout_pct,
               COALESCE(l.score_550,0) AS score_550,
               COALESCE(l.hat_trick,0) AS hat_trick,
               COALESCE(l.playmaker,0) AS playmaker,
               COALESCE(l.offensive_engine,0) AS offensive_engine,
               COALESCE(l.anchor,0) AS anchor,
               COALESCE(l.defensive_problem,0) AS defensive_problem,
               COALESCE(l.two_way,0) AS two_way,
               COALESCE(l.elite_disruptor,0) AS elite_disruptor,
               COALESCE(l.all_around,0) AS all_around
        FROM players p
        LEFT JOIN v_player_season_leaderboard l
           ON l.player_id=p.id AND l.season_id=?
        WHERE EXISTS (
          SELECT 1 FROM roster_memberships rm
          JOIN team_seasons ts ON ts.id=rm.team_season_id
          WHERE rm.player_id=p.id AND ts.season_id=?
        ) OR EXISTS (
          SELECT 1 FROM v_player_game_standouts v
          WHERE v.player_id=p.id AND v.season_id=?
        )
        ORDER BY CASE WHEN l.games_played IS NULL THEN 1 ELSE 0 END,
                 l.standout_pct DESC, l.standout_games DESC, p.display_name
    """, (season_id, season_id, season_id)))


def player(conn, slug):
    return one(conn.execute("SELECT * FROM players WHERE slug=?", (slug,)))



def player_seasons(conn, player_id):
    """Seasons this player joined a roster or appeared in a recorded game.

    Preserves a stable player identity even before a new season's games
    are imported. Seasons are sorted chronologically, independent of ID.
    """
    result = rows(conn.execute("""
        SELECT DISTINCT s.id, s.slug, s.name
        FROM seasons s
        WHERE EXISTS (
            SELECT 1 FROM roster_memberships rm
            JOIN team_seasons ts ON ts.id=rm.team_season_id
            WHERE ts.season_id=s.id AND rm.player_id=?
        )
        OR EXISTS (
            SELECT 1 FROM v_player_game_standouts v
            WHERE v.season_id=s.id AND v.player_id=?
        )
    """, (player_id, player_id)))

    term_order = {"spring": 1, "summer": 2, "fall": 3, "winter": 0}

    def chronological(season_row):
        match = re.fullmatch(r"([a-z]+)-(\d{4})", season_row["slug"])
        if match:
            return (int(match.group(2)), term_order.get(match.group(1), -1))
        return (0, 0)

    return sorted(result, key=chronological, reverse=True)


def player_affiliations(conn, player_id, season_id=None):
    """Teams represented or rostered, with season-specific links."""
    season_sql = ""
    if season_id is not None:
        season_sql = "AND s.id=?"

    # Include actual appearances even when no membership row exists;
    # this can occur when a player substitutes for a roster.
    return rows(conn.execute(f"""
        SELECT DISTINCT ts.id AS team_season_id, t.slug, t.name,
               s.slug AS season_slug, s.name AS season_name
        FROM team_seasons ts
        JOIN seasons s ON s.id=ts.season_id
        JOIN teams t ON t.id=ts.team_id
        WHERE (
          EXISTS (SELECT 1 FROM roster_memberships rm WHERE rm.team_season_id=ts.id AND rm.player_id=?)
          OR EXISTS (SELECT 1 FROM v_player_game_standouts v WHERE v.team_season_id=ts.id AND v.player_id=?)
        )
          {season_sql}
        ORDER BY s.id DESC, t.name
    """, (player_id, player_id, *([season_id] if season_id is not None else []))))

def player_games(conn, season_id, player_id):
    # Old databases may not yet have been migrated. In that case, don't fabricate
    # zeros: show demos-taken and its ratio as unavailable until migration/backfill.
    has_demos_taken = any(
        column["name"] == "demos_taken"
        for column in conn.execute("PRAGMA table_info(player_game_stats)")
    )
    taken_projection = "pg.demos_taken" if has_demos_taken else "NULL"
    return rows(conn.execute(f"""
        SELECT v.*, {taken_projection} AS demos_taken,
               (SELECT COUNT(*) FROM highlights h WHERE h.game_id=g.id) AS highlight_count,
               (SELECT COUNT(*) FROM highlights h JOIN highlight_players hp ON hp.highlight_id=h.id
                WHERE h.game_id=g.id AND hp.player_id=v.player_id) AS featured_highlight_count,
               sr.played_on, sr.id AS series_id, g.game_number,
               g.team_goals, g.opponent_goals, g.ballchasing_replay_url AS replay_url,
               o.name AS opponent, c.name AS competition,
               t.name AS team_name, t.slug AS team_slug,
               s.name AS season_name, s.slug AS season_slug
        FROM v_player_game_standouts v
        JOIN player_game_stats pg ON pg.id=v.appearance_id
        JOIN games g ON g.id=v.game_id
        JOIN series sr ON sr.id=g.series_id
        JOIN seasons s ON s.id=sr.season_id
        JOIN opponents o ON o.id=sr.opponent_id
        JOIN competitions c ON c.id=sr.competition_id
        JOIN team_seasons ts ON ts.id=sr.team_season_id
        JOIN teams t ON t.id=ts.team_id
        WHERE v.player_id=?
          AND (? IS NULL OR sr.season_id=?)
        ORDER BY sr.played_on DESC, sr.id DESC, g.game_number DESC
    """, (player_id, season_id, season_id)))


def player_summary(games):
    if not games:
        return None
    fields = ("score", "goals", "assists", "saves", "shots", "demos", "ga", "gas")
    totals = {field: sum(g[field] for g in games) for field in fields}
    standouts = sum(g["is_standout"] for g in games)
    shooting_pct = round(100 * totals["goals"] / totals["shots"], 2) if totals["shots"] else None

    # "Demos" is the old column containing *inflicted* demolitions.
    # Ratio = total inflicted / total taken, not average of per-game ratios.
    # An old game with unknown demos_taken makes the full-season ratio unknown.
    all_taken_known = all(g.get("demos_taken") is not None for g in games)
    demos_taken = sum(g["demos_taken"] for g in games) if all_taken_known else None
    if demos_taken is None or (demos_taken == 0 and totals["demos"] == 0):
        demo_ratio = None
    elif demos_taken == 0:
        demo_ratio = "∞:1"
    else:
        demo_ratio = f'{totals["demos"] / demos_taken:.2f}:1'

    averages = {field: round(totals[field]/len(games), 2) for field in fields}
    averages["demos_taken"] = round(demos_taken/len(games), 2) if demos_taken is not None else None

    return {
        "games": len(games), "standouts": standouts,
        "standout_pct": round(100 * standouts / len(games), 2),
        "totals": totals,
        "averages": averages,
        "shooting_pct": shooting_pct,
        "demos_taken": demos_taken,
        "demo_ratio": demo_ratio,
        "accolades": [{**cat, "count": sum(g[cat["field"]] for g in games)}
                      for cat in STANDOUT_TYPES],
    }


def player_team_splits(games, by_season=False):
    """Group appearances by team, or by season AND team for all-time view."""
    grouped = {}
    for game in games:
        key = (game["season_slug"], game["team_slug"]) if by_season else game["team_slug"]
        grouped.setdefault(key, []).append(game)
    return [
        {"team_slug": group[0]["team_slug"],
         "team_name": group[0]["team_name"],
         "season_name": group[0]["season_name"],
         "summary": player_summary(group)}
        for group in grouped.values()
    ]


def player_personal_records(games):
    if not games:
        return []
    return records_for_games(games, RECORD_METRICS)


def highlights_for_game(conn, game_id):
    """Public clips and tagged SHSU participants for a single played game."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='highlights'").fetchone():
        return []
    clips = rows(conn.execute("""
        SELECT h.id, h.game_id, h.title, h.url
        FROM highlights h WHERE h.game_id=? ORDER BY h.id
    """, (game_id,)))
    for clip in clips:
        clip["players"] = rows(conn.execute("""
            SELECT p.id, p.slug, p.display_name
            FROM highlight_players hp JOIN players p ON p.id=hp.player_id
            WHERE hp.highlight_id=? ORDER BY p.display_name
        """, (clip["id"],)))
    return clips


def series_highlight_games(conn, series_id):
    """Distinct game numbers with clips; for the series page jump navigation."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='highlights'").fetchone():
        return []
    return rows(conn.execute("""
        SELECT g.id AS game_id, g.game_number, COUNT(h.id) AS highlight_count
        FROM games g JOIN highlights h ON h.game_id=g.id
        WHERE g.series_id=?
        GROUP BY g.id, g.game_number ORDER BY g.game_number
    """, (series_id,)))


def team_series_activity(conn, series_ids):
    """Counts per series for team pages.

    A standout is ONE SHSU player-game with is_standout=1, even if that
    performance earns multiple categories. Highlights are clip records.
    No played game => no standout/highlight data (forfeits remain 0).
    """
    ids = sorted(set(int(sid) for sid in series_ids))
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    standout_rows = rows(conn.execute(f"""
        SELECT g.series_id, COUNT(*) AS standout_count
        FROM v_player_game_standouts v
        JOIN games g ON g.id=v.game_id
        WHERE g.series_id IN ({placeholders}) AND v.is_standout=1
        GROUP BY g.series_id
    """, ids))
    activity = {sid: {"standout_count": 0, "highlight_count": 0,
                      "highlight_game_count": 0} for sid in ids}
    for row in standout_rows:
        activity[row["series_id"]]["standout_count"] = row["standout_count"]

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='highlights'"
    ).fetchone():
        highlight_rows = rows(conn.execute(f"""
            SELECT g.series_id, COUNT(h.id) AS highlight_count,
                   COUNT(DISTINCT g.id) AS highlight_game_count
            FROM highlights h JOIN games g ON g.id=h.game_id
            WHERE g.series_id IN ({placeholders})
            GROUP BY g.series_id
        """, ids))
        for row in highlight_rows:
            activity[row["series_id"]]["highlight_count"] = row["highlight_count"]
            activity[row["series_id"]]["highlight_game_count"] = row["highlight_game_count"]
    return activity


def team_individual_games(conn, team_season_ids):
    """Player-game appearances restricted to team-season IDs.

    Includes substitutes and transfers when they appeared for this team.
    Uses the exact same record metric definitions as season-level records.
    """
    ids = sorted(set(int(tid) for tid in team_season_ids))
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    return rows(conn.execute(f"""
        SELECT v.*, p.slug AS player_slug, p.display_name,
               g.game_number, g.ballchasing_replay_url AS replay_url,
               sr.played_on, sr.id AS series_id,
               o.name AS opponent, c.name AS competition,
               t.name AS team_name, t.slug AS team_slug,
               s.name AS season_name, s.slug AS season_slug
        FROM v_player_game_standouts v
        JOIN players p ON p.id=v.player_id
        JOIN games g ON g.id=v.game_id
        JOIN series sr ON sr.id=g.series_id
        JOIN seasons s ON s.id=sr.season_id
        JOIN opponents o ON o.id=sr.opponent_id
        JOIN competitions c ON c.id=sr.competition_id
        JOIN team_seasons ts ON ts.id=sr.team_season_id
        JOIN teams t ON t.id=ts.team_id
        WHERE v.team_season_id IN ({placeholders})
        ORDER BY sr.played_on DESC, sr.id DESC, g.game_number DESC
    """, ids))
