#!/usr/bin/env python3
"""Preview and (optionally) import one Ballchasing players-games CSV as one SHSU series.
Stdlib only. Run --help.
"""
import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_DEFAULT = ROOT / "shsu_records.sqlite3"
FIELDS = ("score", "goals", "assists", "saves", "shots", "demos")
REQUIRED = {"replay id","date","team name","player name","score","goals","assists","saves","shots","demos inflicted","demos taken"}

def clean(s):
    return " ".join(str(s or "").split()).casefold()

def positive_int(value, field):
    try:
        n = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {field}: {value!r}")
    if n < 0: raise ValueError(f"Negative {field}: {n}")
    return n

def migrate(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(games)")}
    if "replay_id" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN replay_id TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_games_replay_id ON games(replay_id) WHERE replay_id IS NOT NULL")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(series)")}
    if "title" not in cols: conn.execute("ALTER TABLE series ADD COLUMN title TEXT")
    stats_cols = {r["name"] for r in conn.execute("PRAGMA table_info(player_game_stats)")}
    if "demos_taken" not in stats_cols:
        conn.execute("ALTER TABLE player_game_stats ADD COLUMN demos_taken INTEGER CHECK(demos_taken >= 0)")

def load_csv(path, aliases):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader=csv.DictReader(f)
        missing=REQUIRED-set(reader.fieldnames or ())
        if missing: raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
        grouped=defaultdict(list)
        for row in reader:
            rid=str(row["replay id"] or "").strip()
            if not rid: raise ValueError("CSV has a row with no replay id")
            grouped[rid].append(row)
    if not grouped: raise ValueError("CSV contains no game data")
    games=[]
    for rid, rows in grouped.items():
        known=[r for r in rows if clean(r["player name"]) in aliases]
        unknown=[r for r in rows if clean(r["player name"]) not in aliases]
        shsu_team_labels={clean(r["team name"]) for r in known}
        if len(shsu_team_labels)!=1:
            raise ValueError(f"Replay {rid}: known SHSU names span {len(shsu_team_labels)} CSV team labels: {shsu_team_labels}. Check aliases/team assignments.")
        label=next(iter(shsu_team_labels))
        own=[r for r in rows if clean(r["team name"])==label]
        other=[r for r in rows if clean(r["team name"])!=label]
        if len(own)!=3 or len(other)!=3:
            raise ValueError(f"Replay {rid}: expected 3 SHSU + 3 opponents, found {len(own)} + {len(other)}. Check aliases and CSV.")
        if any(clean(r["player name"]) not in aliases for r in own):
            raise ValueError(f"Replay {rid}: unrecognized SHSU player name(s): {[r['player name'] for r in own if clean(r['player name']) not in aliases]}")
        if any(clean(r["player name"]) in aliases for r in other):
            raise ValueError(f"Replay {rid}: recognized SHSU player appears on opposite team")
        ids=[aliases[clean(r["player name"])] for r in own]
        if len(set(ids))!=3:
            raise ValueError(f"Replay {rid}: two SHSU aliases resolve to same player")
        def data(rs):
            return [{ "player_id": aliases[clean(r["player name"])] if clean(r["player name"]) in aliases else None,
                    "name":r["player name"].strip(),
                    **{k:positive_int(r["demos inflicted"] if k=="demos" else r[k],k) for k in FIELDS},
                    "demos_taken":positive_int(r["demos taken"],"demos taken")}
                     for r in rs]
        own_data,op_data=data(own),data(other)
        if any(clean(r["date"])!=clean(rows[0]["date"]) for r in rows):
            raise ValueError(f"Replay {rid}: inconsistent timestamps")
        try: date=datetime.strptime(rows[0]["date"].strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError as e: raise ValueError(f"Replay {rid}: invalid date: {e}")
        ours=sum(r["goals"] for r in own_data)
        theirs=sum(r["goals"] for r in op_data)
        # ODU Game 2: six goals conceded, five credited to ODU players.
        if rid == "d39f1bf7-1f1f-4f5e-9fd5-4d581189efca":
            if ours != 4 or theirs != 5:
                raise ValueError("Unexpected ODU Game 2 scoring data")
            theirs = 6
        if ours==theirs: raise ValueError(f"Replay {rid}: tied goals; overtime/final score needs review")
        if "goals conceded" in rows[0]:
            for r in own:
                if r["goals conceded"].strip() and positive_int(r["goals conceded"],"goals conceded")!=theirs:
                    raise ValueError(f"Replay {rid}: goals-conceded disagreement")
        games.append({"id":rid,"date":date,"title":(rows[0].get("replay title") or "").strip(),
                      "goals":ours,"opponent_goals":theirs,"opponent_shots":sum(x["shots"] for x in op_data),
                      "opponent_demos":sum(x["demos"] for x in op_data),
                      "team_stats":own_data,"opponent_stats":op_data,
                      "team_label":rows[0]["team name"],"result":"W" if ours>theirs else "L"})
    return games

def sort_games(games, manual_order, forfeit_before=0):
    if manual_order:
        ids=[i.strip() for i in manual_order.split(",") if i.strip()]
        actual={g["id"]:g for g in games}
        if len(ids)!=len(games) or len(set(ids))!=len(ids) or set(ids)!=set(actual):
            raise ValueError("--order must contain each replay ID exactly once")
        return [actual[i] for i in ids], "manual replay-ID order"
    parsed=[]
    for game in games:
        title=game["title"].strip().lower()
        m=re.search(r"\b(?:game|g)\s*#?\s*(\d+)\b",title)
        if not m:
            m=re.search(r"(\d+)\s*$",title)
        parsed.append(int(m.group(1)) if m else None)
    if all(v is not None for v in parsed):
        actual_numbers = sorted(parsed)

        sequential = list(range(1, len(games) + 1))
        official = list(range(
            forfeit_before + 1,
            forfeit_before + len(games) + 1
        ))

        if actual_numbers not in (sequential, official):
            raise ValueError(
                f"Unexpected replay title numbers {parsed}; supply --order"
            )
        return [g for _,g in sorted(zip(parsed,games),key=lambda x:x[0])], "replay title game numbers"
    if parsed.count(None)==1 and sorted(v for v in parsed if v is not None)==list(range(2,len(games)+1)):
        parsed=[1 if v is None else v for v in parsed]
        return [g for _,g in sorted(zip(parsed,games),key=lambda x:x[0])], "replay title numbers (missing #1 inferred)"
    if all(v is None for v in parsed):
        # Explicit confirmation is safer than assuming timestamps correspond to numbered games.
        raise ValueError("No replay titles contain game numbers. Supply --order with replay IDs in actual game order.")
    raise ValueError(f"Ambiguous replay title game numbers {parsed}; supply --order with replay IDs")

def get_aliases(conn):
    pairs=conn.execute("SELECT alias,player_id FROM player_aliases").fetchall()
    out={}
    for r in pairs:
        key=clean(r["alias"])
        if key in out and out[key]!=r["player_id"]: raise ValueError(f"Conflicting normalized alias {key}")
        out[key]=r["player_id"]
    return out

def arg_parser():
    p=argparse.ArgumentParser(description="Preview/import one series of Ballchasing 3v3 players-games CSV")
    p.add_argument("csv",type=Path)
    p.add_argument("--db",type=Path,default=DB_DEFAULT)
    p.add_argument("--season",default="fall-2026")
    p.add_argument("--team",required=True,choices=("orange","white","black"))
    p.add_argument("--competition",required=True,choices=("necc","ccs","crl","pcl"))
    p.add_argument("--opponent",required=True,help="Full opponent team name")
    p.add_argument("--title",required=True,help="Human-readable series title")
    p.add_argument("--url",required=True,help="Ballchasing group URL")
    p.add_argument("--played-on",help="Optional YYYY-MM-DD; defaults to first replay date")
    p.add_argument("--round",default="",help="NECC W5 / CCS Week 6 / etc.")
    p.add_argument("--notes",default="")
    p.add_argument("--forfeit-before",type=int,default=0,help="Number of awarded games preceding the played replay games (e.g. TAMU = 1)")
    p.add_argument("--forfeit-after",type=int,default=0)
    p.add_argument("--forfeit-result",choices=("win","loss"),default="win",help="Applies to --forfeit-before/after")
    p.add_argument("--official-wins",type=int,help="Override computed official game wins; must match played+forfeits")
    p.add_argument("--official-losses",type=int,help="Override computed official game losses; must match played+forfeits")
    p.add_argument("--order",default="",help="Optional comma-separated replay IDs in official Game 1..N order; overrides replay-title ordering")
    p.add_argument("--commit",action="store_true",help="Only this option writes records; default is preview")
    return p

def main():
    args=arg_parser().parse_args()
    if not args.csv.is_file(): raise SystemExit(f"CSV not found: {args.csv}")
    if not args.db.is_file(): raise SystemExit(f"DB not found: {args.db}. Copy this file beside your existing .sqlite3.")
    if args.forfeit_before<0 or args.forfeit_after<0: raise SystemExit("Forfeit count cannot be negative")
    if not args.url.startswith("https://ballchasing.com/"):
        raise SystemExit("--url must start with https://ballchasing.com/")
    if not args.opponent.strip() or not args.title.strip(): raise SystemExit("Title/opponent cannot be blank")
    conn=sqlite3.connect(args.db)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        season=conn.execute("SELECT id FROM seasons WHERE slug=?",(args.season,)).fetchone()
        team=conn.execute("""SELECT ts.id FROM team_seasons ts
              JOIN teams t ON t.id=ts.team_id
              WHERE ts.season_id=? AND t.slug=?""",(season["id"] if season else -1,args.team)).fetchone()
        competition=conn.execute("SELECT id FROM competitions WHERE slug=?",(args.competition,)).fetchone()
        if not season or not team or not competition:
            raise ValueError("Season, roster for season, or competition not present. Seed database first.")
        games,ordering=sort_games(
            load_csv(args.csv,get_aliases(conn)),
            args.order,
            args.forfeit_before
        )
        actual_dates={g["date"].date().isoformat() for g in games}
        if len(actual_dates)>2:
            raise ValueError(f"Series replays span unusual date range: {sorted(actual_dates)}")
        played_on=args.played_on or games[0]["date"].date().isoformat()
        datetime.strptime(played_on,"%Y-%m-%d")
        if args.played_on and any(abs((g["date"].date()-datetime.strptime(played_on,"%Y-%m-%d").date()).days)>2 for g in games):
            raise ValueError("Selected series date is too far from replay dates")
        shsu_ids={r["player_id"] for g in games for r in g["team_stats"]}
        memberships={r["player_id"] for r in conn.execute("SELECT player_id FROM roster_memberships WHERE team_season_id=?",(team["id"],))}
        absent=shsu_ids-memberships
        if absent:
            names=conn.execute("SELECT display_name FROM players WHERE id IN (" + ",".join("?"*len(absent))+")",tuple(absent)).fetchall()
            raise ValueError(f"Players not on this team_season roster/sub list: {[r['display_name'] for r in names]}. Add a substitute membership first.")
        ids=[g["id"] for g in games]
        existing=[dict(r) for r in conn.execute("SELECT replay_id,series_id,game_number FROM games WHERE replay_id IN ("+",".join("?"*len(ids))+")",ids)] if "replay_id" in {r["name"] for r in conn.execute("PRAGMA table_info(games)")} else []
        if existing:
            raise ValueError(f"Duplicate replay(s) already in DB: {existing}. Import aborted entirely.")
        fcount=args.forfeit_before+args.forfeit_after
        wins=sum(g["result"]=="W" for g in games)+(fcount if args.forfeit_result=="win" else 0)
        losses=sum(g["result"]=="L" for g in games)+(fcount if args.forfeit_result=="loss" else 0)
        if args.official_wins is not None and args.official_wins!=wins: raise ValueError(f"Official wins entered {args.official_wins} != implied {wins}")
        if args.official_losses is not None and args.official_losses!=losses: raise ValueError(f"Official losses entered {args.official_losses} != implied {losses}")
        print(f"\nGame ordering: {ordering}")
        print(f"\nSERIES: {args.title}\nTeam: {args.team.upper()} | {args.competition.upper()} | {args.season}")
        print(f"Opponent: {args.opponent} | Date: {played_on} | URL: {args.url}")
        print(f"Official games: {wins}–{losses} | Played replays: {len(games)} | Awarded forfeits: {fcount}")
        print(f"{'Game':<6} {'W/L':<5} {'Score':<8} {'Date/time':<20} Replay ID")
        for i,g in enumerate(games,1):
            num=i+args.forfeit_before
            print(f"{num:<6} {g['result']:<5} {g['goals']}-{g['opponent_goals']:<6} {g['date'].strftime('%m-%d %H:%M'):<20} {g['id']}")
            print("       " + " | ".join(f"{r['name']} [{r['score']}p {r['goals']}G {r['assists']}A {r['saves']}S {r['shots']}Sh {r['demos']}D+ {r['demos_taken']}D-]" for r in g["team_stats"]))
        if fcount:print(f"Awarded games: before={args.forfeit_before}, after={args.forfeit_after} ({args.forfeit_result})")
        if not args.commit:
            print("\nPREVIEW ONLY. Nothing written. Review game numbering/results and rerun with --commit.")
            return
        # Recheck and execute all writes as one atomic transaction.
        with conn:
            migrate(conn)
            conn.execute("INSERT OR IGNORE INTO opponents(name) VALUES (?)",(args.opponent.strip(),))
            opponent_id=conn.execute("SELECT id FROM opponents WHERE name=?",(args.opponent.strip(),)).fetchone()["id"]
            match_result="W" if wins>losses else "L" if wins<losses else "T"
            cur=conn.execute("""INSERT INTO series
            (season_id,team_season_id,competition_id,opponent_id,played_on,round_label,result,official_games_won,official_games_lost,ballchasing_group_url,notes,title)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (season["id"],team["id"],competition["id"],opponent_id,played_on,args.round,match_result,wins,losses,args.url,args.notes,args.title))
            series_id=cur.lastrowid
            def forfeit(num):
                conn.execute("INSERT INTO games(series_id,game_number,status) VALUES (?,?,?)",
                             (series_id,num,"forfeit_win" if args.forfeit_result=="win" else "forfeit_loss"))
            for n in range(1,args.forfeit_before+1):forfeit(n)
            for i,g in enumerate(games,1):
                num=i+args.forfeit_before
                cur=conn.execute("""INSERT INTO games
                (series_id,game_number,status,team_goals,opponent_goals,opponent_shots,opponent_demos,ballchasing_replay_url,replay_id)
                VALUES (?,?,'played',?,?,?,?,?,?)""",
                (series_id,num,g["goals"],g["opponent_goals"],g["opponent_shots"],g["opponent_demos"],
                 f"https://ballchasing.com/replay/{g['id']}",g["id"]))
                game_id=cur.lastrowid
                for r in g["team_stats"]:
                    conn.execute("""INSERT INTO player_game_stats
                    (game_id,player_id,score,goals,assists,saves,shots,demos,demos_taken)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (game_id,r["player_id"],*(r[k] for k in FIELDS),r["demos_taken"]))
            for n in range(len(games)+args.forfeit_before+1,len(games)+fcount+1):forfeit(n)
        print(f"\nCOMMITTED series_id={series_id}: {len(games)} played games, {len(games)*3} SHSU appearances.")
        print("Team-game stats are derived by summing the 3 SHSU appearances per game.")
    finally:
        conn.close()

if __name__=="__main__":
    try: main()
    except (ValueError,sqlite3.Error) as exc: raise SystemExit(f"ERROR: {exc}")
