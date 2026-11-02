# SHSU Ballchasing Series → SQLite Importer

This is an **add-on to your existing SHSU record book project**. Do **not** overwrite `shsu_records.sqlite3`.

## Install

Copy `import_series.py` beside your existing `shsu_records.sqlite3`, `app.py`, and `repository.py`.
No extra Python packages: standard library only.

## Import one series (preview first)

Run from the project root (PowerShell example, one line):

```powershell
python import_series.py "week-5-vs-utd-purple-87kg98qmns-players-games.csv" --team black --season fall-2026 --competition necc --opponent "UTD Purple" --title "NECC Week 5 - Black vs UTD Purple" --round "NECC Week 5" --url "https://ballchasing.com/group/week-5-vs-utd-purple-87kg98qmns"
```

**Nothing is written** until you rerun the same command with `--commit` at the end. The preview shows each correctly ordered game, W/L, goals, replay ID and all three SHSU players' complete core stats.

When ready:

```powershell
python import_series.py "week-5-vs-utd-purple-87kg98qmns-players-games.csv" --team black --season fall-2026 --competition necc --opponent "UTD Purple" --title "NECC Week 5 - Black vs UTD Purple" --round "NECC Week 5" --url "https://ballchasing.com/group/week-5-vs-utd-purple-87kg98qmns" --commit
```

The importer:
- Identifies SHSU's **three** players by normalized `player_aliases` (trims whitespace, ignores case), even when Ballchasing calls both sides `SHSU`/other variants.
- Reads both teams' rows, but **stores only the three SHSU player appearances**. Opponent goals, shots and demos are aggregated and saved at game level.
- Uses the replay's UUID to identify a unique played game. Duplicate imports abort the **whole series**; never silently overwrite.
- Associates played games and all three appearances with the new series; records series title, opponent, league, season, date, Ballchasing group URL.
- Calculates team-game totals in the website's existing read-only queries by summing SHSU player-game rows. No `teams-games.csv` is necessary.
- Keeps awarded forfeits as `games.status='forfeit_win'` or `'forfeit_loss'` with **no player statistics**.
- Refuses unknown players, ambiguous game numbering, malformed stats, and mismatched roster memberships.

### Important Ballchasing issue: replay titles vs timestamps

**Do not assume replay timestamps correspond to Game 1, 2, 3...!** The UTD Purple CSV includes out-of-order dates/timestamps. The importer determines sequence from replay titles (`game 1`, `game 2`, ...), even if timestamps are out of order. If titles are ambiguous, it **aborts** and requests a manual `--order` list of comma-separated replay UUIDs in official order. Manually review the preview.

### Awarded forfeit before played games

For White vs TAMU Century, the awarded win was official Game 1, and the six played replays were official Games 2–7. Add `--forfeit-before 1 --forfeit-result win --official-wins 4 --official-losses 3` to that command (and review replay title numbering; use `--order` when needed). Forfeits never create player stats.

### Data changes / schema

**Live DB:** `shsu_records.sqlite3` — never replace it with this add-on.

On the first committed import, the importer adds:
- `games.replay_id` with a unique partial index (each real replay imported once globally);
- `series.title` for a human-readable series name.

These are **small, non-destructive migrations** and occur during the transaction. If you later recreate a database using `schema.sql`, add the same definitions from `migrations/001_series_replay_identity.sql` to the fresh schema or apply the migration after initialization.

**Best practice:** back up the `.sqlite3` before the first `--commit`.

### Query / audit

```sql
SELECT s.title, g.game_number, g.replay_id,
       g.team_goals, g.opponent_goals,
       SUM(p.score) AS team_score, SUM(p.shots) AS team_shots,
       SUM(p.demos) AS team_demos
FROM games g
JOIN series s ON s.id=g.series_id
JOIN player_game_stats p ON p.game_id=g.id
WHERE g.status='played'
GROUP BY g.id
ORDER BY s.id, g.game_number;
```

```sql
SELECT p.display_name, l.games_played, l.standout_games, l.standout_pct
FROM v_player_season_leaderboard l
JOIN players p ON p.id=l.player_id
JOIN seasons s ON s.id=l.season_id
WHERE s.slug='fall-2026'
ORDER BY l.standout_pct DESC;
```

## Limitations and next enhancements

- Designed for complete **3v3** games with three known SHSU players and three opponents per replay. Incomplete/extra-player replays need review.
- It stores the six *core* SHSU box-score metrics. Your 92-column export has many richer telemetry columns; we can add a separate stats-detail table later without losing the CSV originals.
- It doesn't create new seasons, rosters, or players automatically. Seed those first. This protects against misattributing stats to the wrong player.
- It checks replay IDs recorded **after** this importer is used. If an earlier manual insert lacks a replay ID, backfill it before importing the same replay.
- The stored series title is new; your Flask templates can be updated to display `series.title` later.
- Preserve original CSVs for verification/backups. No data is published to GitHub Pages until you build/deploy the static site.
