# SHSU RL Highlights Integration

## Files
- `001_add_highlights.sql`: one-time migration; preserves existing records.
- `scripts/import_highlight.py`: preview-first CLI; only writes with `--commit`.
- `repository.py`: replacement for repository.py (now selects highlight counts and provides clip lookup helpers).
- `app.py`: replacement for app.py; adds highlight access to template context (no new URL route required).
- `templates/player.html`: game-log filtering for any clip, clips featuring the player, any standout, and specific standout category; filters can be combined.
- `templates/series.html`: game-specific clip links and a top-of-page jump list.

## Install into existing project
1. **Back up** `shsu_records.sqlite3`, repository.py, app.py, and both templates.
2. If not already migrated, apply the SQL using Python or SQLite CLI (foreign keys enabled):
   `sqlite3 shsu_records.sqlite3 < 001_add_highlights.sql`
   The script is designed to create missing tables/indexes/triggers.
3. Replace project-root `repository.py` and `app.py`.
4. Replace `templates/player.html` and `templates/series.html`.
5. Copy `import_highlight.py` to `scripts/import_highlight.py`.
6. Preview and commit a clip. Run the static export normally:
   `python scripts/export_static.py`
   then publish the regenerated `docs/` content.

**NOTE**: Your app imports `series_pages.py` and this file wasn't among the uploads. This implementation uses a Flask context processor to expose the new read-only lookup functions directly to templates; it does not require editing `series_pages.py` as long as it renders your existing `series.html` with `series` (including `series.id`) and `games` (including each `g.id`).

## Example commands (run from project root)

By game ID:
```
python scripts/import_highlight.py --game-id 1 --title "Zero-second save" --url "https://youtu.be/example" --players "Coldest,Nyx"
```

By series ID + game number:
```
python scripts/import_highlight.py --series-id 1 --game-number 2 --title "Passing play" --url "https://clips.twitch.tv/example" --players "Tango" --commit
```

By unique partial series title + game number:
```
python scripts/import_highlight.py --series-title "Long Beach State" --game-number 1 --title "Overtime goal" --url "https://example.com/clip"
```

- All commands **preview** first unless `--commit` is included.
- `--players` is optional. Supply exact player display names, slugs or aliases. Player associations are restricted to SHSU players with a recorded appearance in the selected game.
- Duplicate game + URL is rejected.
- One game may have multiple highlights; one clip may tag multiple players.
- Game log "Games with any highlight" includes a game regardless of which player is featured. "Clips featuring this player" only includes clips whose junction row tags this player.
- You can combine any-highlight/featured/standout game filters with a particular Standout category.
- No clip data is included by default. No live database has been overwritten.

## Tests performed
- Python syntax checks for app, repository, importer.
- Jinja parser checks for both templates.
- On a **temporary copy** of the migrated database: dry-run produced no row, commit inserted a clip and its tagged player, duplicate URL was rejected, and repository retrieval/record queries succeeded.

## Compatibility notes
- The updated `player_games` query requires the highlights migration to be run first.
- Any importer edits to the database still require rerunning the static export for GitHub Pages.
