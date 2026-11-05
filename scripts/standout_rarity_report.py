#!/usr/bin/env python3
"""Observed SHSU Rocket League standout frequency across Ballchasing CSV replays.

Reads CSVs recursively, including opponent player rows. Does not modify SQLite.
Deduplicates entire games by Ballchasing replay ID. Requires six valid player
rows (three per side); problematic replays are excluded and listed in the report.

Usage from the SHSU-RL-Records repository root:
    python scripts/standout_rarity_report.py data/replays   # scan data/replays recursively for CSVs, use auto-detected SQLite DB if present
    python scripts/standout_rarity_report.py data/replays --output standout_rarity_report.txt   # write report to a different TXT file
    python scripts/standout_rarity_report.py data/replays --db shsu_records.sqlite3 # explicitly specify the SQLite DB for player aliases (read-only)
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

REQUIRED = {
    "replay id", "player name", "team name",
    "score", "goals", "assists", "saves", "shots", "demos inflicted",
}
STATS = ("score", "goals", "assists", "saves", "shots", "demos")
CATEGORIES = (
    ("550+ Score", "Score >= 550"),
    ("Hat Trick", "Goals >= 3"),
    ("Playmaker", "Assists >= 3"),
    ("Offensive Engine", "Goals >= 2 AND assists >= 2"),
    ("Anchor", "Saves >= 5"),
    ("Defensive Problem", "Saves >= 4 AND (goals + assists >= 1 OR demos >= 3)"),
    ("Two-Way", "Saves >= 3 AND goals + assists >= 2"),
    ("Elite Disruptor", "Demos >= 4 AND goals + assists + saves >= 3"),
    ("All-Around", "Goals + assists + saves >= 7"),
)
CATEGORY_NAMES = tuple(label for label, _ in CATEGORIES)
SHSU_LABEL = re.compile(r"\bshsu\b|\bsam\s+houston\b|\bbearkats?\b|\bshs\b", re.I)


def clean(value: object) -> str:
    """Match the repository importer's normalization for player aliases."""
    return " ".join(str(value or "").split()).casefold()


def read_number(value: object, field: str) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}={value!r}") from exc
    if number < 0:
        raise ValueError(f"negative {field}={number}")
    return number


@dataclass(frozen=True)
class PlayerGame:
    name: str
    side: str  # "SHSU" or "Opponent"
    score: int
    goals: int
    assists: int
    saves: int
    shots: int
    demos: int

    def categories(self) -> tuple[str, ...]:
        ga = self.goals + self.assists
        gas = ga + self.saves
        flags = (
            self.score >= 550,
            self.goals >= 3,
            self.assists >= 3,
            self.goals >= 2 and self.assists >= 2,
            self.saves >= 5,
            self.saves >= 4 and (ga >= 1 or self.demos >= 3),
            self.saves >= 3 and ga >= 2,
            self.demos >= 4 and gas >= 3,
            gas >= 7,
        )
        return tuple(label for (label, _), yes in zip(CATEGORIES, flags) if yes)


@dataclass(frozen=True)
class Replay:
    replay_id: str
    title: str
    date: str
    source: str
    players: tuple[PlayerGame, ...]

    def statistical_signature(self) -> tuple:
        """Ignore harmless display-name/team-label differences in duplicate exports."""
        return tuple(sorted(
            (p.side, *(getattr(p, stat) for stat in STATS))
            for p in self.players
        ))


def locate_database(explicit: Path | None) -> Path | None:
    if explicit is not None:
        resolved = explicit.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Alias database not found: {resolved}")
        return resolved
    here = Path(__file__).resolve()
    candidates = (
        Path.cwd() / "shsu_records.sqlite3",
        here.parent.parent / "shsu_records.sqlite3",  # installed under scripts/
        here.parent / "shsu_records.sqlite3",
    )
    return next((p.resolve() for p in candidates if p.is_file()), None)


def load_aliases(db_path: Path | None) -> set[str]:
    if db_path is None:
        return set()
    # True read-only SQLite connection: this script cannot write to the DB.
    uri = db_path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        alias_rows = connection.execute("SELECT alias FROM player_aliases").fetchall()
        player_rows = connection.execute("SELECT display_name FROM players").fetchall()
    return {clean(value) for (value,) in alias_rows + player_rows if clean(value)}


def identify_shsu(rows: list[dict[str, str]], aliases: set[str]) -> tuple[set[int], str | None]:
    """Identify one SHSU trio; never guess when labels/names are inconclusive."""
    names = [clean(row["player name"]) for row in rows]
    labels = [clean(row["team name"]) for row in rows]
    matched = {i for i, name in enumerate(names) if name in aliases}

    if len(matched) == 3:
        # Player aliases are more reliable than malformed Ballchasing team labels.
        shsu_labels = {labels[i] for i in matched}
        warning = None
        if len(shsu_labels) != 1 or any(labels[j] in shsu_labels for j in range(6) if j not in matched):
            warning = "SHSU team labels inconsistent; identified by 3 known player aliases"
        return matched, warning

    by_label: dict[str, set[int]] = defaultdict(set)
    for i, label in enumerate(labels):
        by_label[label].add(i)

    # A recognized teammate or a recognizable SHSU label can establish the
    # three-person side even when a substitute's game alias is not registered.
    valid_sides = [members for members in by_label.values() if len(members) == 3]
    if matched:
        fitting = [members for members in valid_sides if matched.issubset(members)]
        if len(fitting) == 1:
            return set(fitting[0]), (
                f"only {len(matched)} of 3 SHSU aliases recognized; third player inferred from team label"
            )

    shsu_hint_sides = [
        members for label, members in by_label.items()
        if len(members) == 3 and SHSU_LABEL.search(label)
    ]
    if len(shsu_hint_sides) == 1:
        side = shsu_hint_sides[0]
        if matched and not matched.issubset(side):
            raise ValueError("SHSU label conflicts with known player aliases")
        return set(side), ("identified by SHSU team label (no matching aliases)" if not matched else None)

    raise ValueError(
        f"cannot safely identify SHSU's three players "
        f"({len(matched)} known aliases, team labels={dict(Counter(labels))})"
    )


def parse_replay(replay_id: str, rows: list[dict[str, str]],
                 aliases: set[str], source: str) -> tuple[Replay, str | None]:
    if len(rows) != 6:
        raise ValueError(f"expected 6 player rows, found {len(rows)}")
    normalized_names = [clean(row["player name"]) for row in rows]
    if any(not name for name in normalized_names) or len(set(normalized_names)) != 6:
        raise ValueError("empty or duplicate player names among six rows")

    own, warning = identify_shsu(rows, aliases)
    if len(own) != 3:
        raise ValueError(f"SHSU side has {len(own)} players instead of 3")

    players = []
    for i, row in enumerate(rows):
        numbers = {
            field: read_number(row["demos inflicted"] if field == "demos" else row[field], field)
            for field in STATS
        }
        players.append(PlayerGame(
            name=str(row["player name"]).strip(),
            side="SHSU" if i in own else "Opponent",
            **numbers,
        ))
    return Replay(
        replay_id=replay_id,
        title=str(rows[0].get("replay title") or "").strip(),
        date=str(rows[0].get("date") or "").strip(),
        source=source,
        players=tuple(players),
    ), warning


def scan(root: Path, aliases: set[str]):
    csv_files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".csv")
    occurrences: dict[str, list[Replay]] = defaultdict(list)
    problems: list[str] = []
    notices: list[str] = []
    matching_files = 0
    skipped_files = 0
    for path in csv_files:
        relative = path.relative_to(root).as_posix()
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = {clean(x) for x in (reader.fieldnames or []) if x}
                if not REQUIRED.issubset(columns):
                    skipped_files += 1
                    continue
                # Ballchasing uses lowercase headers; normalize them for safety.
                grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
                for row in reader:
                    normalized = {clean(k): v for k, v in row.items() if k}
                    replay_id = str(normalized.get("replay id") or "").strip()
                    if not replay_id:
                        problems.append(f"{relative}: CSV row without a replay ID (ignored)")
                        continue
                    grouped[replay_id].append(normalized)
            matching_files += 1
            for replay_id, rows in grouped.items():
                try:
                    replay, warning = parse_replay(replay_id, rows, aliases, relative)
                    occurrences[replay_id].append(replay)
                    if warning:
                        notices.append(f"{relative} | {replay_id}: {warning}")
                except ValueError as exc:
                    problems.append(f"{relative} | {replay_id}: {exc} (excluded from this export)")
        except (OSError, UnicodeError, csv.Error) as exc:
            problems.append(f"{relative}: cannot read CSV: {exc}")

    unique: list[Replay] = []
    conflicting = 0
    duplicate_copies = 0
    for replay_id, copies in occurrences.items():
        duplicate_copies += len(copies) - 1
        if len({r.statistical_signature() for r in copies}) != 1:
            conflicting += 1
            sources = ", ".join(r.source for r in copies)
            problems.append(
                f"REPLAY CONFLICT {replay_id}: incompatible SHSU/opponent stats "
                f"across exports [{sources}] (whole replay excluded)"
            )
        else:
            # Player-game observations are counted ONE time for each replay ID.
            unique.append(copies[0])

    unique.sort(key=lambda g: (g.date, g.replay_id))
    return unique, {
        "csv_files": len(csv_files),
        "matching_files": matching_files,
        "skipped_files": skipped_files,
        "replay_occurrences": sum(map(len, occurrences.values())),
        "duplicates_removed": duplicate_copies,
        "conflicting_ids": conflicting,
        "problems": problems,
        "notices": notices,
    }


def ratio(n: int, d: int) -> str:
    return f"{100 * n / d:.2f}%" if d else "N/A"


def analyze(replays: list[Replay]):
    category_sides = {name: Counter() for name in CATEGORY_NAMES}
    exclusive_sides = {name: Counter() for name in CATEGORY_NAMES}
    any_sides = Counter()
    exclusivity_sides = Counter()
    number_of_categories = Counter()
    players_by_lobby = Counter({i: 0 for i in range(7)})
    category_awards_by_lobby = Counter()
    shsu_by_lobby = Counter({i: 0 for i in range(4)})
    opponent_by_lobby = Counter({i: 0 for i in range(4)})

    events: list[dict] = []
    games: list[dict] = []

    for replay in replays:
        count = Counter()
        awards_in_lobby = 0
        for player in replay.players:
            names = player.categories()
            count[player.side] += bool(names)
            number_of_categories[len(names)] += 1
            if names:
                any_sides[player.side] += 1
                exclusivity_sides[(player.side, "single" if len(names) == 1 else "multiple")] += 1
                for name in names:
                    category_sides[name][player.side] += 1
                    if len(names) == 1:
                        exclusive_sides[name][player.side] += 1
                awards_in_lobby += len(names)
                events.append({
                    "replay_id": replay.replay_id,
                    "date": replay.date,
                    "replay_title": replay.title,
                    "side": player.side,
                    "player": player.name,
                    **{stat: getattr(player, stat) for stat in STATS},
                    "category_count": len(names),
                    "categories": " | ".join(names),
                    "source_csv": replay.source,
                })
        total = count["SHSU"] + count["Opponent"]
        players_by_lobby[total] += 1
        shsu_by_lobby[count["SHSU"]] += 1
        opponent_by_lobby[count["Opponent"]] += 1
        category_awards_by_lobby[awards_in_lobby] += 1
        games.append({
            "replay_id": replay.replay_id,
            "date": replay.date,
            "replay_title": replay.title,
            "shsu_standout_players": count["SHSU"],
            "opponent_standout_players": count["Opponent"],
            "total_standout_players": total,
            "category_awards": awards_in_lobby,
            "source_csv": replay.source,
        })
    return {
        "category_sides": category_sides,
        "exclusive_sides": exclusive_sides,
        "any_sides": any_sides,
        "exclusivity_sides": exclusivity_sides,
        "number_of_categories": number_of_categories,
        "players_by_lobby": players_by_lobby,
        "shsu_by_lobby": shsu_by_lobby,
        "opponent_by_lobby": opponent_by_lobby,
        "category_awards_by_lobby": category_awards_by_lobby,
        "events": events,
        "games": games,
    }


def report_text(root: Path, db_path: Path | None, replays: list[Replay],
                metadata: dict, stats: dict) -> str:
    n = len(replays)
    slots = n * 6
    side_slots = n * 3
    any_sides = stats["any_sides"]
    shsu = any_sides["SHSU"]
    opp = any_sides["Opponent"]
    any_total = shsu + opp
    hist = stats["players_by_lobby"]
    award_counts = stats["category_awards_by_lobby"]
    individual_count = stats["number_of_categories"]
    overall_awards = sum(k * v for k, v in award_counts.items())

    lines = [
        "SHSU ROCKET LEAGUE — OBSERVED STANDOUT FREQUENCY",
        "=" * 72,
        "Scope: all eligible 3v3 player-games in the scanned Ballchasing exports.",
        "Includes BOTH SHSU and opposing players (one game = six player slots).",
        "This is a descriptive sample of recorded SHSU matches, not a random",
        "sample of every collegiate/Rocket League match.",
        "",
        "SAMPLE / DATA QUALITY",
        "-" * 72,
        f"CSV root: {root}",
        f"Read-only player-alias database: {db_path if db_path else 'not found — team labels only'}",
        f"CSVs found: {metadata['csv_files']}",
        f"Ballchasing players-games CSVs recognized: {metadata['matching_files']}",
        f"Other CSVs ignored: {metadata['skipped_files']}",
        f"Valid replay occurrences across CSVs: {metadata['replay_occurrences']}",
        f"Duplicate replay copies removed: {metadata['duplicates_removed']}",
        f"Replay IDs excluded for conflicting exports: {metadata['conflicting_ids']}",
        f"Unique eligible complete 3v3 replays analyzed: {n:,}",
        f"Total player-game observations: {slots:,}",
        f"SHSU player-game observations: {side_slots:,}",
        f"Opponent player-game observations: {side_slots:,}",
        f"Invalid / rejected entries: {len(metadata['problems']):,}",
        "",
        "OVERALL STANDOUT FREQUENCY",
        "-" * 72,
        f"Any standout (all players): {any_total:,} / {slots:,} = {ratio(any_total, slots)}",
        f"Any standout (SHSU):        {shsu:,} / {side_slots:,} = {ratio(shsu, side_slots)}",
        f"Any standout (opponents):   {opp:,} / {side_slots:,} = {ratio(opp, side_slots)}",
        f"Games with 1+ standout player: {n - hist[0]:,} / {n:,} = {ratio(n-hist[0],n)}",
        f"Games with 0 standout players: {hist[0]:,} / {n:,} = {ratio(hist[0],n)}",
        f"Average standout players per game: {any_total/n:.3f}" if n else "Average standout players per game: N/A",
        f"Total awarded categories (overlapping): {overall_awards:,}",
        "",
        "NUMBER OF STANDOUT PLAYERS IN EACH REPLAY (0 THROUGH 6)",
        "-" * 72,
        "A player with several accolades counts as ONE standout player here.",
    ]
    for count in range(7):
        lines.append(
            f"  {count} player{'s' if count != 1 else ' '}: "
            f"{hist[count]:>6,} games ({ratio(hist[count], n):>7})"
        )
    lines += [
        f"  TOTAL:     {sum(hist.values()):>6,} games",
        "",
        "SHSU / OPPONENT STANDOUT PLAYER DISTRIBUTIONS (0 THROUGH 3)",
        "-" * 72,
        "                  SHSU games     Opponent games",
    ]
    for count in range(4):
        lines.append(
            f"  {count} of 3 players:  {stats['shsu_by_lobby'][count]:>6,} "
            f"({ratio(stats['shsu_by_lobby'][count],n):>7})   "
            f"{stats['opponent_by_lobby'][count]:>6,} "
            f"({ratio(stats['opponent_by_lobby'][count],n):>7})"
        )

    lines += [
        "",
        "CATEGORY-BY-CATEGORY OBSERVED FREQUENCY",
        "-" * 104,
        "Each percentage uses PLAYER-GAME observations, not number of matches.",
        f"{'Category':<22} {'All':>5} {'All rate':>9} {'SHSU':>6} {'SHSU %':>9} "
        f"{'Opp':>6} {'Opp %':>9} {'Single-only':>12}",
        "-" * 104,
    ]
    for name in CATEGORY_NAMES:
        by = stats["category_sides"][name]
        exclusive = stats["exclusive_sides"][name]
        total = by["SHSU"] + by["Opponent"]
        singles = exclusive["SHSU"] + exclusive["Opponent"]
        lines.append(
            f"{name:<22} {total:>5,} {ratio(total, slots):>9} "
            f"{by['SHSU']:>6,} {ratio(by['SHSU'],side_slots):>9} "
            f"{by['Opponent']:>6,} {ratio(by['Opponent'],side_slots):>9} "
            f"{singles:>12,}"
        )

    single = stats["exclusivity_sides"][("SHSU", "single")] + stats["exclusivity_sides"][("Opponent", "single")]
    multi = stats["exclusivity_sides"][("SHSU", "multiple")] + stats["exclusivity_sides"][("Opponent", "multiple")]
    lines += [
        "",
        "SINGLE-CATEGORY VS MULTI-CATEGORY PLAYER-GAMES",
        "-" * 72,
        "A 'single-only' standout is a player-game meeting EXACTLY ONE of",
        "the nine category rules (not one category across the entire lobby).",
        f"Standout player-games with exactly ONE category: {single:,} / {slots:,} = {ratio(single,slots)}",
        f"Standout player-games with 2+ categories:       {multi:,} / {slots:,} = {ratio(multi,slots)}",
        f"Total qualifying player-games (single + multi): {single+multi:,}",
        f"  SHSU single-only:     {stats['exclusivity_sides'][('SHSU','single')]:,}",
        f"  Opponent single-only: {stats['exclusivity_sides'][('Opponent','single')]:,}",
        "",
        "  Unique single-only awards by category (these sum to the single-only total):",
        f"  {'Category':<23} {'SHSU':>7} {'Opp':>7} {'Total':>7}",
    ]
    for name in CATEGORY_NAMES:
        by = stats["exclusive_sides"][name]
        lines.append(
            f"  {name:<23} {by['SHSU']:>7,} {by['Opponent']:>7,} {by['SHSU']+by['Opponent']:>7,}"
        )
    lines += [
        "",
        "NUMBER OF CATEGORY AWARDS ON ONE PLAYER-GAME",
        "-" * 72,
        "If a player meets 3 criteria in one game, that counts as 3 awards",
        "but only 1 qualifying player-game.",
    ]
    for count in range(10):
        lines.append(f"  {count} categories: {individual_count[count]:>6,} player-games ({ratio(individual_count[count], slots)})")

    lines += [
        "",
        "CATEGORY AWARDS PER FULL SIX-PLAYER LOBBY",
        "-" * 72,
        f"Games with exactly ONE category award total: {award_counts[1]:,} / {n:,} = {ratio(award_counts[1],n)}",
        "Unlike one standout player, this requires only one accolade anywhere",
        "in the lobby (one player earning 2 accolades does NOT qualify).",
        "",
        "CRITERIA (EXACT SAME THRESHOLDS AS SHSU RECORD BOOK)",
        "-" * 72,
    ]
    for name, definition in CATEGORIES:
        lines.append(f"  {name:<22} {definition}")
    lines += [
        "",
        "METHODOLOGY / LIMITATIONS",
        "-" * 72,
        "1. Every replay ID is counted once, even if present in many CSVs.",
        "2. Only complete six-player games with 3 SHSU and 3 opponents are included.",
        "3. Games are classified using SHSU player aliases from the read-only",
        "   SQLite database when available, then reliable CSV team labels.",
        "4. Each player can qualify for multiple categories, but counts once",
        "   toward 'Any Standout' and 0-6 per-game counts.",
        "5. Rates describe these archived matches; they are not universal",
        "   odds, independent-player probabilities, or skill-adjusted estimates.",
        "6. Unplayed forfeits have no six-player stats, so are not sampled.",
        "7. Invalid/replay-conflicting exports are excluded and shown below.",
        "",
        "DATA-QUALITY EXCLUSIONS",
        "-" * 72,
    ]
    if metadata["problems"]:
        lines.extend(f"  ! {issue}" for issue in metadata["problems"])
    else:
        lines.append("  None.")
    lines += [
        "",
        "NON-FATAL IDENTITY WARNINGS",
        "-" * 72,
        f"Total identity warnings (across CSV copies): {len(metadata['notices']):,}",
    ]
    if metadata["notices"]:
        lines.extend(f"  * {msg}" for msg in metadata["notices"])
    else:
        lines.append("  None.")
    lines += ["", "END OF REPORT", ""]
    return "\n".join(lines)


def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", type=Path, default=Path("data/replays"),
                        help="Root folder to scan recursively (default: data/replays)")
    parser.add_argument("--db", type=Path, help="Optional read-only SHSU SQLite DB for player aliases; auto-detected when present")
    parser.add_argument("--output", type=Path, default=Path("standout_rarity_report.txt"),
                        help="TXT summary output (default: standout_rarity_report.txt)")
    args = parser.parse_args()

    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"CSV folder does not exist: {root}")

    try:
        db_path = locate_database(args.db)
        aliases = load_aliases(db_path)
        replays, metadata = scan(root, aliases)
        if metadata["matching_files"] == 0:
            raise ValueError("No Ballchasing player-game CSVs with the required columns were found")
        if not replays:
            raise ValueError("No eligible six-player replays; inspect your CSVs and alias/team labels")
        stats = analyze(replays)
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        events_path = output.with_name(output.stem + "_events.csv")
        games_path = output.with_name(output.stem + "_games.csv")
        text = report_text(root, db_path, replays, metadata, stats)
        output.write_text(text, encoding="utf-8")
        event_fields = [
            "replay_id", "date", "replay_title", "side", "player",
            *STATS, "category_count", "categories", "source_csv",
        ]
        game_fields = [
            "replay_id", "date", "replay_title", "shsu_standout_players",
            "opponent_standout_players", "total_standout_players",
            "category_awards", "source_csv",
        ]
        save_csv(events_path, stats["events"], event_fields)
        save_csv(games_path, stats["games"], game_fields)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    count = len(replays)
    total = stats["any_sides"]["SHSU"] + stats["any_sides"]["Opponent"]
    print(f"Analyzed {count:,} unique complete 3v3 games ({count*6:,} player-games).")
    print(f"Any standout: {total:,}/{count*6:,} ({ratio(total, count*6)}).")
    print(f"Duplicate replay copies collapsed: {metadata['duplicates_removed']:,}.")
    if metadata["problems"]:
        print(f"WARNING: {len(metadata['problems'])} data-quality exclusions; see report.")
    if not db_path:
        print("WARNING: SQLite alias DB not found; SHSU classification used CSV team labels only.")
    print(f"Report:       {output}")
    print(f"Event audit:  {events_path}")
    print(f"Game audit:   {games_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
