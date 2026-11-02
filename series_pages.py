"""Read-only series detail pages for SHSU RL Records.

Installation:
    from series_pages import series_bp
    app.register_blueprint(series_bp)

No database migrations, importer changes or repository.py edits required.
"""
from collections import defaultdict
from contextlib import closing

from flask import Blueprint, abort, current_app, render_template

import repository as repo


series_bp = Blueprint("series", __name__)


def _has_column(conn, table, name):
    return any(col["name"] == name for col in conn.execute(f"PRAGMA table_info({table})"))


def load_series(conn, series_id):
    """Build a complete SHSU-only series summary from existing SQLite rows."""
    title_column = (
        "COALESCE(NULLIF(TRIM(sr.title), ''), t.name || ' vs. ' || o.name)"
        if _has_column(conn, "series", "title")
        else "t.name || ' vs. ' || o.name"
    )
    series = repo.one(conn.execute(f"""
        SELECT sr.id, sr.season_id, sr.team_season_id, sr.played_on,
               {title_column} AS title, sr.round_label, sr.notes,
               sr.result, sr.official_games_won, sr.official_games_lost,
               sr.ballchasing_group_url,
               s.slug AS season_slug, s.name AS season_name,
               t.slug AS team_slug, t.name AS team_name,
               o.name AS opponent, c.name AS competition
        FROM series sr
        JOIN seasons s ON s.id=sr.season_id
        JOIN team_seasons ts ON ts.id=sr.team_season_id
        JOIN teams t ON t.id=ts.team_id
        JOIN opponents o ON o.id=sr.opponent_id
        JOIN competitions c ON c.id=sr.competition_id
        WHERE sr.id=?
    """, (series_id,)))
    if series is None:
        return None

    games = repo.rows(conn.execute("""
        SELECT g.id, g.game_number, g.status,
               g.team_goals, g.opponent_goals,
               g.ballchasing_replay_url AS replay_url
        FROM games g WHERE g.series_id=?
        ORDER BY g.game_number
    """, (series_id,)))

    taken_col = "pg.demos_taken" if _has_column(conn, "player_game_stats", "demos_taken") else "NULL"
    appearances = repo.rows(conn.execute(f"""
        SELECT v.*, {taken_col} AS demos_taken,
               p.slug AS player_slug, p.display_name
        FROM v_player_game_standouts v
        JOIN player_game_stats pg ON pg.id=v.appearance_id
        JOIN players p ON p.id=v.player_id
        JOIN games g ON g.id=v.game_id
        WHERE g.series_id=?
        ORDER BY g.game_number, p.display_name
    """, (series_id,)))

    appearances_by_game = defaultdict(list)
    appearances_by_player = defaultdict(list)
    player_names = {}
    for row in appearances:
        row["accolades"] = [
            {"icon": cat["icon"], "label": cat["label"]}
            for cat in repo.STANDOUT_TYPES if row[cat["field"]]
        ]
        appearances_by_game[row["game_id"]].append(row)
        appearances_by_player[row["player_slug"]].append(row)
        player_names[row["player_slug"]] = row["display_name"]

    summed_fields = ("score", "goals", "assists", "saves", "shots", "demos")
    complete_appearances = []
    played_games = []
    awarded_wins = awarded_losses = 0
    played_wins = played_losses = 0
    goals_for = goals_against = 0
    incomplete_games = []

    for game in games:
        game["players"] = appearances_by_game.get(game["id"], [])
        game["complete"] = game["status"] == "played" and len(game["players"]) == 3
        game["team_totals"] = None
        if game["status"] == "forfeit_win":
            awarded_wins += 1
            game["result"] = "FW"
        elif game["status"] == "forfeit_loss":
            awarded_losses += 1
            game["result"] = "FL"
        else:
            played_games.append(game)
            if game["team_goals"] > game["opponent_goals"]:
                game["result"] = "W"
                played_wins += 1
            elif game["team_goals"] < game["opponent_goals"]:
                game["result"] = "L"
                played_losses += 1
            else:
                game["result"] = "T"
            goals_for += game["team_goals"]
            goals_against += game["opponent_goals"]
            if game["complete"]:
                game["team_totals"] = {
                    field: sum(p[field] for p in game["players"])
                    for field in summed_fields
                }
                game["team_totals"]["demos_taken"] = (
                    sum(p["demos_taken"] for p in game["players"])
                    if all(p["demos_taken"] is not None for p in game["players"])
                    else None
                )
                game["team_totals"]["standouts"] = sum(p["is_standout"] for p in game["players"])
                game["goals_match"] = game["team_totals"]["goals"] == game["team_goals"]
                complete_appearances.extend(game["players"])
            else:
                incomplete_games.append(game["game_number"])

    count = len(played_games)
    complete_count = count - len(incomplete_games)
    totals = {field: sum(p[field] for p in complete_appearances) for field in summed_fields}
    taken_known = all(p["demos_taken"] is not None for p in complete_appearances)
    taken = sum(p["demos_taken"] for p in complete_appearances) if taken_known else None
    totals["demos_taken"] = taken
    totals["ga"] = totals["goals"] + totals["assists"]
    totals["gas"] = totals["ga"] + totals["saves"]
    averages = {
        field: (round(totals[field] / complete_count, 2) if complete_count else None)
        for field in summed_fields
    }
    averages["demos_taken"] = round(taken / complete_count, 2) if taken is not None and complete_count else None
    shooting_pct = round(100 * totals["goals"] / totals["shots"], 2) if totals["shots"] else None
    if taken is None or (taken == 0 and totals["demos"] == 0):
        demo_ratio = None
    elif taken == 0:
        demo_ratio = "∞:1"
    else:
        demo_ratio = f'{totals["demos"] / taken:.2f}:1'

    player_totals = []
    for slug, rows in appearances_by_player.items():
        info = repo.player_summary(rows)
        player_totals.append({
            "slug": slug,
            "name": player_names[slug],
            "summary": info,
        })
    player_totals.sort(key=lambda x: (-x["summary"]["games"], -x["summary"]["totals"]["score"], x["name"]))

    return {
        "series": series,
        "games": games,
        "player_totals": player_totals,
        "team": {
            "games": count,
            "complete_games": complete_count,
            "played_wins": played_wins,
            "played_losses": played_losses,
            "forfeit_wins": awarded_wins,
            "forfeit_losses": awarded_losses,
            "goals_for": goals_for,
            "goals_against": goals_against,
            "totals": totals,
            "averages": averages,
            "shooting_pct": shooting_pct,
            "demo_ratio": demo_ratio,
            "standout_games": sum(p["is_standout"] for p in complete_appearances),
            "incomplete_games": incomplete_games,
        },
    }


@series_bp.route("/series/<int:series_id>/")
def series_page(series_id):
    # Use the same read-only DB and shared layout as the existing Flask website.
    with closing(repo.open_database(current_app.config["DATABASE"])) as conn:
        detail = load_series(conn, series_id)
    if detail is None:
        abort(404, description="That series has not been imported.")
    return render_template(
        "series.html", active_page="seasons",
        season={"slug": detail["series"]["season_slug"], "name": detail["series"]["season_name"]},
        standout_types=repo.STANDOUT_TYPES,
        **detail,
    )
