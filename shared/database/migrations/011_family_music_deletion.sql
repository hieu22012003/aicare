-- Retain media and listening history while removing a song from all playlists.
ALTER TABLE family_music ADD COLUMN deleted_at TEXT;
