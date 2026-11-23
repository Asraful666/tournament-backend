-- 0001_users_role_link_columns.sql
-- Additive (non-destructive) schema change applied 2026-11-09.
-- Needed for object-level RBAC: links a user account to its Team Manager
-- team, Player profile, or Referee identity. Models are unmanaged
-- (inspectdb), so Django migrations cannot produce this — apply manually.
-- Run with: myvenv\Scripts\python.exe -c ... (or any MySQL/MariaDB client
-- connected to the `tournament_management` database).

ALTER TABLE users
  ADD COLUMN team_id INT NULL,
  ADD COLUMN player_id INT NULL,
  ADD COLUMN referee_id INT NULL,
  ADD CONSTRAINT users_team_fk FOREIGN KEY (team_id) REFERENCES teams(team_id),
  ADD CONSTRAINT users_player_fk FOREIGN KEY (player_id) REFERENCES players(player_id),
  ADD CONSTRAINT users_referee_fk FOREIGN KEY (referee_id) REFERENCES referees(referee_id);
