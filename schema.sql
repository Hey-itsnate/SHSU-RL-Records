PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS seasons (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS teams (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS team_seasons (
 id INTEGER PRIMARY KEY,
 season_id INTEGER NOT NULL REFERENCES seasons(id),
 team_id INTEGER NOT NULL REFERENCES teams(id),
 UNIQUE(season_id, team_id),
 UNIQUE(id, season_id)
);
CREATE TABLE IF NOT EXISTS players (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_aliases (
 id INTEGER PRIMARY KEY,
 player_id INTEGER NOT NULL REFERENCES players(id),
 alias TEXT NOT NULL COLLATE NOCASE UNIQUE
);
CREATE TABLE IF NOT EXISTS player_social_accounts (
 id INTEGER PRIMARY KEY,
 player_id INTEGER NOT NULL REFERENCES players(id),
 platform TEXT NOT NULL,
 username TEXT NOT NULL COLLATE NOCASE,
 UNIQUE(player_id, platform),
 UNIQUE(platform, username)
);
CREATE TABLE IF NOT EXISTS roster_memberships (
 id INTEGER PRIMARY KEY,
 team_season_id INTEGER NOT NULL REFERENCES team_seasons(id),
 player_id INTEGER NOT NULL REFERENCES players(id),
 role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','starter','reserve','substitute')),
 UNIQUE(team_season_id,player_id)
);
CREATE TABLE IF NOT EXISTS competitions (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_competition_divisions (
    team_season_id INTEGER NOT NULL REFERENCES team_seasons(id) ON DELETE CASCADE,
    competition_id INTEGER NOT NULL REFERENCES competitions(id),
    division TEXT NOT NULL CHECK (length(trim(division)) > 0),
    PRIMARY KEY (team_season_id, competition_id)
);
CREATE TABLE IF NOT EXISTS opponents (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS series (
 id INTEGER PRIMARY KEY,
 season_id INTEGER NOT NULL REFERENCES seasons(id),
 team_season_id INTEGER NOT NULL,
 competition_id INTEGER NOT NULL REFERENCES competitions(id),
 opponent_id INTEGER NOT NULL REFERENCES opponents(id),
 played_on TEXT, round_label TEXT,
 result TEXT NOT NULL CHECK(result IN ('W','L','T','pending')),
 official_games_won INTEGER NOT NULL DEFAULT 0 CHECK(official_games_won >= 0),
 official_games_lost INTEGER NOT NULL DEFAULT 0 CHECK(official_games_lost >= 0),
 ballchasing_group_url TEXT, notes TEXT,
 FOREIGN KEY(team_season_id,season_id) REFERENCES team_seasons(id,season_id)
);
CREATE TABLE IF NOT EXISTS games (
 id INTEGER PRIMARY KEY,
 series_id INTEGER NOT NULL REFERENCES series(id),
 game_number INTEGER NOT NULL CHECK(game_number >= 1),
 status TEXT NOT NULL DEFAULT 'played' CHECK(status IN ('played','forfeit_win','forfeit_loss')),
 team_goals INTEGER CHECK(team_goals >= 0),
 opponent_goals INTEGER CHECK(opponent_goals >= 0),
 opponent_shots INTEGER CHECK(opponent_shots >= 0),
 opponent_demos INTEGER CHECK(opponent_demos >= 0),
 ballchasing_replay_url TEXT,
 UNIQUE(series_id,game_number),
 CHECK (
  (status='played' AND team_goals IS NOT NULL AND opponent_goals IS NOT NULL)
  OR
  (status<>'played' AND team_goals IS NULL AND opponent_goals IS NULL)
 )
);
CREATE TABLE IF NOT EXISTS player_game_stats (
 id INTEGER PRIMARY KEY,
 game_id INTEGER NOT NULL REFERENCES games(id),
 player_id INTEGER NOT NULL REFERENCES players(id),
 score INTEGER NOT NULL CHECK(score >= 0),
 goals INTEGER NOT NULL CHECK(goals >= 0),
 assists INTEGER NOT NULL CHECK(assists >= 0),
 saves INTEGER NOT NULL CHECK(saves >= 0),
 shots INTEGER NOT NULL CHECK(shots >= 0),
 demos INTEGER NOT NULL CHECK(demos >= 0),
 UNIQUE(game_id,player_id)
);
CREATE INDEX IF NOT EXISTS idx_series_team ON series(team_season_id);
CREATE INDEX IF NOT EXISTS idx_games_series ON games(series_id);
CREATE INDEX IF NOT EXISTS idx_stats_player ON player_game_stats(player_id);
CREATE TRIGGER IF NOT EXISTS no_forfeit_stats_insert
BEFORE INSERT ON player_game_stats
WHEN (SELECT status FROM games WHERE id=NEW.game_id)<>'played'
BEGIN SELECT RAISE(ABORT,'Cannot add player stats to awarded forfeit'); END;
CREATE TRIGGER IF NOT EXISTS no_forfeit_stats_move
BEFORE UPDATE OF game_id ON player_game_stats
WHEN (SELECT status FROM games WHERE id=NEW.game_id)<>'played'
BEGIN SELECT RAISE(ABORT,'Cannot move stats onto awarded forfeit'); END;
CREATE TRIGGER IF NOT EXISTS no_forfeit_game_change
BEFORE UPDATE OF status ON games
WHEN NEW.status<>'played' AND EXISTS(SELECT 1 FROM player_game_stats WHERE game_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'Cannot change played game with stats to forfeit'); END;


-- The nine Fall 2026 categories can overlap, but a qualifying player-game counts once.
-- Version this logic if future seasons adopt different thresholds.
CREATE VIEW IF NOT EXISTS v_player_game_standouts AS
SELECT pg.id AS appearance_id, pg.player_id, g.id AS game_id,
 sr.season_id, sr.team_season_id,
 pg.score,pg.goals,pg.assists,pg.saves,pg.shots,pg.demos,
 pg.goals+pg.assists AS ga,
 pg.goals+pg.assists+pg.saves AS gas,
 (pg.score>=550) AS score_550,
 (pg.goals>=3) AS hat_trick,
 (pg.assists>=3) AS playmaker,
 (pg.goals>=2 AND pg.assists>=2) AS offensive_engine,
 (pg.saves>=5) AS anchor,
 (pg.saves>=4 AND (pg.goals+pg.assists>=1 OR pg.demos>=3)) AS defensive_problem,
 (pg.saves>=3 AND pg.goals+pg.assists>=2) AS two_way,
 (pg.demos>=4 AND pg.goals+pg.assists+pg.saves>=3) AS elite_disruptor,
 (pg.goals+pg.assists+pg.saves>=7) AS all_around,
 (pg.score>=550 OR pg.goals>=3 OR pg.assists>=3
  OR (pg.goals>=2 AND pg.assists>=2) OR pg.saves>=5
  OR (pg.saves>=4 AND (pg.goals+pg.assists>=1 OR pg.demos>=3))
  OR (pg.saves>=3 AND pg.goals+pg.assists>=2)
  OR (pg.demos>=4 AND pg.goals+pg.assists+pg.saves>=3)
  OR pg.goals+pg.assists+pg.saves>=7) AS is_standout
FROM player_game_stats pg
JOIN games g ON g.id=pg.game_id AND g.status='played'
JOIN series sr ON sr.id=g.series_id;

CREATE VIEW IF NOT EXISTS v_player_season_leaderboard AS
SELECT v.season_id,v.player_id,p.display_name,
 COUNT(*) AS games_played,
 SUM(v.is_standout) AS standout_games,
 ROUND(100.0*SUM(v.is_standout)/COUNT(*),2) AS standout_pct,
 SUM(v.score_550) AS score_550,
 SUM(v.hat_trick) AS hat_trick,
 SUM(v.playmaker) AS playmaker,
 SUM(v.offensive_engine) AS offensive_engine,
 SUM(v.anchor) AS anchor,
 SUM(v.defensive_problem) AS defensive_problem,
 SUM(v.two_way) AS two_way,
 SUM(v.elite_disruptor) AS elite_disruptor,
 SUM(v.all_around) AS all_around
FROM v_player_game_standouts v
JOIN players p ON p.id=v.player_id
GROUP BY v.season_id,v.player_id;
