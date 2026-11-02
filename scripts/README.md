# 🏆 SHSU Series Importer — Quick Guide

Run all commands from the **project root** in PowerShell.

### 1. Preview a series

```powershell
python scripts/import_series.py "PATH_TO_CSV" --team orange --season fall-2026 --competition ccs --opponent "Opponent Name" --title "CCS Week 1 vs Opponent" --round "CCS Nonconference Week 1" --url "https://ballchasing.com/group/GROUP_ID"
```

- `--team`: `orange`, `white`, or `black`
- `--competition`: `ccs`, `necc`, or `crl`
- `--season`: e.g., `fall-2026`
- `--round`: Optional competition/week label
- **Preview first!** Check game order, scores, player names, and replay IDs.

### 2. Commit the import

Rerun the **same command**, adding `--commit` at the end.

This saves the series, games, and player statistics to `shsu_records.sqlite3`.

### 3. ⚠️ Ambiguous replay titles

If you get:

`ERROR: Unexpected replay title numbers... supply --order`

Add:

```powershell
--order "UUID_GAME1,UUID_GAME2,UUID_GAME3"
```

List the actual **replay IDs in official game order**, separated by commas.

**Important:** Replay timestamps may be out of order, and titles like `v2` can confuse automatic game numbering. Always verify the preview before committing.

### 4. Awarded forfeits

For an awarded Game 1 win, add:

```powershell
--forfeit-before 1 --forfeit-result win --official-wins 4 --official-losses 3
```

Adjust the official series score as needed. Forfeited games do not create player statistics.

### 5. Reminders

- CSVs go under `data/replays/season/team/competition/`.
- Duplicate replay IDs are rejected automatically.
- Unknown player aliases must be added before importing.
- No separate team-game CSV is needed.
- Keep original CSVs and back up the SQLite database periodically.
- **Never replace your living `.sqlite3` file with a starter database.**