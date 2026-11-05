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



@app.route("/methodology/")
def methodology_page():
    """Static snapshot of standout rarity from archived six-player replays."""
    return render_template(
        "methodology.html", active_page="methodology",
    )

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

    # Season-wide player statistics: one row per SHSU player, across all
    # roster appearances including substitutions. Reuse exactly the same
    # summary function as the individual player profiles.
    player_stats = []
    for leader in leaders:
        player_games = repo.player_games(conn, current["id"], leader["player_id"])
        summary = repo.player_summary(player_games)
        played_teams = {game["team_slug"] for game in player_games}
        rostered_teams = {
            team["slug"] for team in team_list
            if any(member["id"] == leader["player_id"] for member in team["roster"])
        }
        team_names = [
            team["name"] for team in team_list
            if team["slug"] in played_teams | rostered_teams
        ]
        player_stats.append({
            "slug": leader["slug"],
            "display_name": leader["display_name"],
            "team_names": ", ".join(team_names) or "—",
            "summary": summary,
        })
    player_stats.sort(
        key=lambda row: (
            -(row["summary"]["games"] if row["summary"] else 0),
            row["display_name"].casefold(),
        )
    )
    return render_template(
        "season.html", active_page="seasons", season=current,
        teams=team_list, team_records=repo.records_for_games(all_team_games),
        individual_records=repo.individual_records(appearances),
        personal_bests=repo.personal_best_scores(appearances),
        leaders=leaders, player_stats=player_stats,
        total_series=sum(t["series_count"] for t in team_list),
        games_imported=len(all_team_games), appearances=len(appearances),
        standout_count=sum(g["is_standout"] for g in appearances),
    )


def _labelled_games(games, season_data):
    """Attach historical season labels without modifying repository rows."""
    return [
        {**game, "season_name": season_data["name"],
         "season_slug": season_data["slug"]}
        for game in games
    ]


@app.route("/all-seasons/")
def all_seasons_page():
    """All recorded seasons; SHSU appearances only, excluding unplayed forfeits."""
    conn = db()
    history = repo.seasons(conn)
    all_team_games = []
    appearances = []
    rostered_players = {}
    team_data = {}

    for historical_season in history:
        sid = historical_season["id"]
        all_team_games.extend(_labelled_games(
            repo.team_game_rows(conn, sid), historical_season
        ))
        appearances.extend(_labelled_games(
            repo.individual_games(conn, sid), historical_season
        ))
        for leader in repo.leaderboard(conn, sid):
            rostered_players[leader["player_id"]] = {
                "player_id": leader["player_id"], "slug": leader["slug"],
                "display_name": leader["display_name"]
            }
        for roster_team in repo.team_seasons(conn, sid):
            key = roster_team["slug"]
            item = team_data.setdefault(key, {
                "slug": key, "name": roster_team["name"],
                "series_count": 0, "roster_ids": set(), "games": []
            })
            item["series_count"] += len(repo.team_series(conn, roster_team["id"]))
            item["roster_ids"].update(
                member["id"] for member in repo.roster(conn, roster_team["id"])
            )
            item["games"].extend(
                _labelled_games(
                    repo.team_game_rows(conn, sid, roster_team["id"]),
                    historical_season,
                )
            )

    # Include players who played even if their roster membership was not
    # registered (e.g. one-off substitutes).
    for appearance in appearances:
        rostered_players.setdefault(appearance["player_id"], {
            "player_id": appearance["player_id"],
            "slug": appearance["player_slug"],
            "display_name": appearance["display_name"],
        })
        if appearance["team_slug"] in team_data:
            team_data[appearance["team_slug"]]["roster_ids"].add(
                appearance["player_id"]
            )

    team_list = []
    for item in sorted(
        team_data.values(),
        key=lambda t: ({"orange": 0, "white": 1, "black": 2}.get(t["slug"], 99), t["name"]),
    ):
        item["averages"] = repo.aggregate_games(item["games"])
        item["records"] = repo.records_for_games(item["games"])
        item["roster"] = [{"id": pid} for pid in item["roster_ids"]]
        team_list.append(item)

    # Exactly one player row across all seasons, including substitutes.
    # Reuse the same per-game summary as each player's existing /all/ profile.
    leaders = []
    player_stats = []
    for identity in rostered_players.values():
        player_games = repo.player_games(conn, None, identity["player_id"])
        summary = repo.player_summary(player_games)
        accolades = {
            cat["field"]: (summary["accolades"][i]["count"] if summary else 0)
            for i, cat in enumerate(repo.STANDOUT_TYPES)
        }
        leaders.append({
            **identity,
            "games_played": summary["games"] if summary else 0,
            "standout_games": summary["standouts"] if summary else 0,
            "standout_pct": summary["standout_pct"] if summary else None,
            **accolades,
            "season_names": ", ".join(
                season["name"] for season in reversed(repo.player_seasons(
                    conn, identity["player_id"]
                ))
            ),
        })
        affiliations = repo.player_affiliations(conn, identity["player_id"])
        player_stats.append({
            "slug": identity["slug"],
            "display_name": identity["display_name"],
            "team_names": ", ".join(
                f'{team["name"]} · {team["season_name"]}'
                for team in reversed(affiliations)
            ) or "—",
            "summary": summary,
        })
    leaders.sort(key=lambda p: (
        not bool(p["games_played"]),
        -(p["standout_pct"] or 0),
        -p["standout_games"],
        p["display_name"].casefold(),
    ))
    player_stats.sort(key=lambda p: (
        -(p["summary"]["games"] if p["summary"] else 0),
        p["display_name"].casefold(),
    ))

    # Reuse the four-tab season template, preserving identical sorting/filtering.
    return render_template(
        "season.html", active_page="all_seasons", all_time=True,
        season={"name": "All Seasons", "slug": "all"},
        recorded_seasons=list(reversed(history)),
        teams=team_list, team_records=repo.records_for_games(all_team_games),
        individual_records=repo.individual_records(appearances),
        personal_bests=repo.personal_best_scores(appearances),
        leaders=leaders, player_stats=player_stats,
        total_series=sum(team["series_count"] for team in team_list),
        games_imported=len(all_team_games), appearances=len(appearances),
        standout_count=sum(game["is_standout"] for game in appearances),
    )


def _combined_team_records(seasonal_records):
    """Add official, played, and forfeit W/L without counting forfeits as play."""
    fields = (
        "series", "wins", "losses", "ties",
        "official_wins", "official_losses",
        "played_wins", "played_losses",
        "forfeit_wins", "forfeit_losses",
    )

    def empty(competition):
        return {"competition": competition, **{field: 0 for field in fields}}

    total = empty("All competitions")
    grouped = {}
    for season_record in seasonal_records:
        for field in fields:
            total[field] += season_record["total"][field]
        for comp in season_record["by_competition"]:
            target = grouped.setdefault(comp["competition"], empty(comp["competition"]))
            for field in fields:
                target[field] += comp[field]

    return {
        "total": total,
        "by_competition": [
            grouped[name] for name in sorted(grouped, key=lambda name: (
                {"NECC": 0, "CCS": 1, "CRL": 2, "PCL": 3}.get(name.upper(), 99),
                name.casefold(),
            ))
        ],
    }


@app.route("/all-seasons/teams/<team_slug>/")
def all_seasons_team_page(team_slug):
    """One historical SHSU team page per color, with season-labelled records."""
    conn = db()
    periods = []
    games = []
    results = []
    season_records = []
    members_by_id = {}
    for historical_season in repo.seasons(conn):
        team = repo.team_season(conn, historical_season["id"], team_slug)
        if not team:
            continue
        periods.append({"slug": historical_season["slug"], "name": historical_season["name"]})
        games.extend(_labelled_games(
            repo.team_game_rows(conn, historical_season["id"], team["id"]),
            historical_season,
        ))
        results.extend(_labelled_games(
            repo.team_series(conn, team["id"]), historical_season
        ))
        season_records.append(repo.team_season_records(conn, team["id"]))
        for member in repo.roster(conn, team["id"]):
            members_by_id.setdefault(member["id"], {
                **member, "seasons": set(),
            })["seasons"].add(historical_season["name"])
        # Substitute appearances are relevant even without a roster membership.
        for appearance in repo.individual_games(conn, historical_season["id"]):
            if appearance["team_slug"] != team_slug:
                continue
            pid = appearance["player_id"]
            members_by_id.setdefault(pid, {
                "id": pid, "slug": appearance["player_slug"],
                "display_name": appearance["display_name"],
                "role": "substitute", "discord": None, "seasons": set(),
            })["seasons"].add(historical_season["name"])

    if not periods:
        abort(404, description="That team has no recorded history.")

    members = []
    for member in members_by_id.values():
        member["summary"] = repo.player_summary([
            g for g in repo.player_games(conn, None, member["id"])
            if g["team_slug"] == team_slug
        ])
        member["season_names"] = ", ".join(
            name for name in reversed([s["name"] for s in periods])
            if name in member["seasons"]
        )
        members.append(member)
    members.sort(key=lambda member: member["display_name"].casefold())
    results.sort(key=lambda result: (
        result["played_on"] or "", result["id"]
    ), reverse=True)
    historical_team = {"slug": team_slug, "name": team["name"]}

    return render_template(
        "team.html", active_page="all_seasons", all_time=True,
        season={"name": "All Seasons", "slug": "all"},
        periods=periods, team=historical_team,
        averages=repo.aggregate_games(games),
        season_records=_combined_team_records(season_records),
        records=repo.records_for_games(games), members=members,
        series_list=results, games_imported=len(games),
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
        "player.html", active_page="all_seasons" if current is None else "seasons", player=p,
        season=current, season_label=season_label, selected_slug=selected_slug,
        available_seasons=available_seasons, affiliations=affiliations,
        summary=repo.player_summary(games),
        team_splits=repo.player_team_splits(games, by_season=current is None),
        bests=repo.player_personal_records(games), games=games,
    )


if __name__ == "__main__":
    app.run(debug=True)
