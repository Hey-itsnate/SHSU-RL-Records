"""SHSU Rocket League Record Book — local Flask preview.

The repository module handles all SQL. Flask handles URLs and renders Jinja.
GitHub Pages will ultimately need pre-rendered HTML, not a running Flask app.
"""
import os
from pathlib import Path
from flask import Flask, abort, g, render_template, request, redirect, url_for

import repository as repo
from series_pages import series_bp

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["DATABASE"] = Path(os.environ.get("SHSU_DB_PATH", ROOT / "shsu_records.sqlite3"))
app.register_blueprint(series_bp)


def db():
    if "db" not in g:
        g.db = repo.open_database(app.config["DATABASE"])
    return g.db


@app.teardown_appcontext
def close_connection(exception=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@app.context_processor
def inject_global_navigation():
    return {"site_seasons": repo.seasons(db()), "standout_types": repo.STANDOUT_TYPES}


def lookup_season(season_slug):
    season_data = repo.season(db(), season_slug)
    if not season_data:
        abort(404, description="That season isn't in the database yet.")
    return season_data


@app.route("/")
def home():
    return render_template("index.html", active_page="home")


@app.route("/seasons/<season_slug>/")
def season_page(season_slug):
    current = lookup_season(season_slug)
    conn = db()
    team_list = repo.team_seasons(conn, current["id"])
    all_team_games = repo.team_game_rows(conn, current["id"])
    for item in team_list:
        team_games = [g for g in all_team_games if g["team_season_id"] == item["id"]]
        item["averages"] = repo.aggregate_games(team_games)
        item["records"] = repo.records_for_games(team_games)
        item["roster"] = repo.roster(conn, item["id"])
        item["series_count"] = len(repo.team_series(conn, item["id"]))

    appearances = repo.individual_games(conn, current["id"])
    leaders = repo.leaderboard(conn, current["id"])
    return render_template(
        "season.html", active_page="seasons", season=current,
        teams=team_list, team_records=repo.records_for_games(all_team_games),
        individual_records=repo.individual_records(appearances),
        personal_bests=repo.personal_best_scores(appearances),
        leaders=leaders, total_series=sum(t["series_count"] for t in team_list),
        games_imported=len(all_team_games), appearances=len(appearances),
        standout_count=sum(g["is_standout"] for g in appearances),
    )


@app.route("/seasons/<season_slug>/teams/<team_slug>/")
def team_page(season_slug, team_slug):
    current = lookup_season(season_slug)
    conn = db()
    team = repo.team_season(conn, current["id"], team_slug)
    if not team:
        abort(404, description="That roster is not registered for this season.")
    games = repo.team_game_rows(conn, current["id"], team["id"])
    members = repo.roster(conn, team["id"])
    for member in members:
        player_games = [row for row in repo.player_games(conn, current["id"], member["id"])
                        if row["team_slug"] == team_slug]
        member["summary"] = repo.player_summary(player_games)

    return render_template(
        "team.html", active_page="seasons", season=current, team=team,
        averages=repo.aggregate_games(games),
        season_records=repo.team_season_records(conn, team["id"]),
        records=repo.records_for_games(games), members=members,
        series_list=repo.team_series(conn, team["id"]),
        games_imported=len(games),
    )


@app.route("/seasons/<season_slug>/players/<player_slug>/")
def player_page(season_slug, player_slug):
    """Existing season links keep their selected season."""
    lookup_season(season_slug)
    return redirect(
        url_for("player_profile_season", player_slug=player_slug, view_slug=season_slug),
        code=302,
    )


@app.route("/players/<player_slug>/")
def player_profile(player_slug):
    """Permanent public player URL; defaults to most recent playing season."""
    # Support ?season=fall-2026 links, but prefer path URLs for GitHub Pages.
    old_filter = request.args.get("season")
    if old_filter:
        return redirect(url_for(
            "player_profile_season", player_slug=player_slug, view_slug=old_filter
        ), code=302)
    return render_player_profile(player_slug, None)


@app.route("/players/<player_slug>/<view_slug>/")
def player_profile_season(player_slug, view_slug):
    """Static-friendly paths: /players/coldest/fall-2026/ and /all/."""
    return render_player_profile(player_slug, view_slug)


def render_player_profile(player_slug, view_slug):
    conn = db()
    p = repo.player(conn, player_slug)
    if not p:
        abort(404, description="Player not found.")

    available_seasons = repo.player_seasons(conn, p["id"])
    if not available_seasons:
        abort(404, description="No seasons found for this player.")

    selected_slug = view_slug or available_seasons[0]["slug"]

    if selected_slug == "all":
        current = None
        games = repo.player_games(conn, None, p["id"])
        season_label = "All Seasons"
    else:
        current = next(
            (s for s in available_seasons if s["slug"] == selected_slug), None
        )
        if current is None:
            abort(404, description="This player is not registered for that season.")
        games = repo.player_games(conn, current["id"], p["id"])
        season_label = current["name"]

    affiliations = repo.player_affiliations(
        conn, p["id"], None if current is None else current["id"]
    )
    p["aliases"] = repo.rows(conn.execute(
        "SELECT alias FROM player_aliases WHERE player_id=? ORDER BY alias", (p["id"],)
    ))
    p["socials"] = repo.rows(conn.execute(
        "SELECT platform, username FROM player_social_accounts WHERE player_id=?",
        (p["id"],)
    ))

    return render_template(
        "player.html", active_page="seasons", player=p,
        season=current, season_label=season_label, selected_slug=selected_slug,
        available_seasons=available_seasons, affiliations=affiliations,
        summary=repo.player_summary(games),
        team_splits=repo.player_team_splits(games, by_season=current is None),
        bests=repo.player_personal_records(games), games=games,
    )


if __name__ == "__main__":
    app.run(debug=True)
