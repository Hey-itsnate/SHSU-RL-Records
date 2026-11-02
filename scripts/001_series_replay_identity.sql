-- Apply only once to an existing starter DB (import_series.py does this automatically).
ALTER TABLE games ADD COLUMN replay_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_games_replay_id ON games(replay_id) WHERE replay_id IS NOT NULL;
ALTER TABLE series ADD COLUMN title TEXT;
