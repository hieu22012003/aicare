"""Patient-scoped music uploaded by family, persisted under the media directory."""
import hashlib
import io
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from shared.database.outbox_helper import insert_outbox_event

MAX_AUDIO_BYTES = 50 * 1024 * 1024
AUDIO_TYPES = {'.mp3': ('audio/mpeg', 'mp3'), '.wav': ('audio/wav', 'wav')}



_MP3_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)


def _duration_without_av(extension, data):
    """Seconds of audio, checked without PyAV: WAV through the wave module, MP3 from its first frame header."""
    if extension == '.wav':
        import wave
        with wave.open(io.BytesIO(data)) as w:
            if w.getnframes() <= 0 or w.getframerate() <= 0:
                raise ValueError('Không có dữ liệu âm thanh')
            return w.getnframes() / float(w.getframerate())
    start = 0
    if data[:3] == b'ID3' and len(data) > 10:
        start = 10 + ((data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F))
    for i in range(start, min(len(data) - 3, start + 65536)):
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0 and (data[i + 1] & 0x06) != 0:
            kbps = _MP3_BITRATES[data[i + 2] >> 4]
            if kbps:
                return max(1.0, (len(data) - i) * 8 / (kbps * 1000.0))
    raise ValueError('Không tìm thấy khung âm thanh MP3')

class FamilyMusicService:
    def __init__(self, db, media_dir=None):
        self.db = db
        if media_dir is None:
            from shared.config import MEDIA_DIR
            media_dir = MEDIA_DIR
        self.media_dir = Path(media_dir).resolve()

    def _path(self, stored_name):
        path = (self.media_dir / stored_name).resolve()
        if path.parent != self.media_dir:
            raise ValueError('Đường dẫn nhạc không hợp lệ')
        return path

    def list_tracks(self, patient_id):
        return self.db.fetchall('SELECT * FROM family_music WHERE patient_id = ? AND deleted_at IS NULL ORDER BY created_at DESC, track_id', (patient_id,))

    def get_track(self, patient_id, track_id):
        return self.db.fetchone('SELECT * FROM family_music WHERE patient_id = ? AND track_id = ? AND deleted_at IS NULL', (patient_id, track_id))

    def delete(self, patient_id, user_id, track_id):
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.fetchone('SELECT * FROM family_music WHERE patient_id = ? AND track_id = ?', (patient_id, track_id))
            if not row:
                return False
            if row['deleted_at'] is None:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute('UPDATE family_music SET deleted_at = ? WHERE track_id = ?', (now, track_id))
                insert_outbox_event(conn, patient_id, 'music.track_deleted', 'music_track', track_id,
                                    occurred_at=now, actor_id=user_id, source='caregiver_portal',
                                    payload_minimal={'track_id': track_id})
        return True

    @staticmethod
    def public_track(row):
        return {key: row[key] for key in ('track_id', 'title', 'original_filename', 'duration_seconds', 'size_bytes', 'created_at')}

    def audio_path(self, row):
        return self._path(row['stored_name'])

    def upload(self, patient_id, user_id, filename, title, data):
        if not isinstance(data, bytes) or not data:
            raise ValueError('Vui lòng chọn tệp nhạc MP3 hoặc WAV')
        if len(data) > MAX_AUDIO_BYTES:
            raise ValueError('Tệp nhạc vượt quá 50 MB')
        filename = str(filename).replace('\\', '/').split('/')[-1][:255]
        extension = Path(filename).suffix.lower()
        if extension not in AUDIO_TYPES:
            raise ValueError('Chỉ hỗ trợ tệp MP3 và WAV')
        title = (str(title or '').strip() or Path(filename).stem)[:128]
        if not title:
            raise ValueError('Vui lòng nhập tên bài nhạc')
        # Validate the container and decode an audio frame, not just its suffix.
        try:
            try:
                import av
            except ImportError:  # cloud backend without PyAV: check the file with the standard library
                av = None
            if av is None:
                duration = _duration_without_av(extension, data)
            else:
                with av.open(io.BytesIO(data)) as container:
                    if AUDIO_TYPES[extension][1] not in container.format.name.split(','):
                        raise ValueError('Sai định dạng âm thanh')
                    stream = next(s for s in container.streams if s.type == 'audio')
                    frame = next(container.decode(stream))
                    if frame.samples <= 0:
                        raise ValueError('Không có dữ liệu âm thanh')
                    duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / av.time_base
                    if not math.isfinite(duration) or duration <= 0:
                        raise ValueError('Không xác định được thời lượng âm thanh')
        except Exception as exc:
            raise ValueError('Tệp không phải âm thanh MP3/WAV hợp lệ hoặc đã bị hỏng') from exc
        digest = hashlib.sha256(data).hexdigest()
        track_id = 'family_' + uuid.uuid4().hex
        stored_name = track_id + extension
        path = self._path(stored_name)
        temp = path.with_suffix(path.suffix + '.upload')
        created_file = False
        try:
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                existing = conn.fetchone('SELECT * FROM family_music WHERE patient_id = ? AND sha256 = ?', (patient_id, digest))
                if existing:
                    if existing['deleted_at'] is None:
                        return existing, False
                    # Re-uploading a removed song restores it without duplicating history.
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute('UPDATE family_music SET deleted_at = NULL, title = ?, original_filename = ?, '
                                 'uploaded_by = ?, created_at = ? WHERE track_id = ?',
                                 (title, filename, user_id, now, existing['track_id']))
                    insert_outbox_event(conn, patient_id, 'music.track_uploaded', 'music_track', existing['track_id'],
                                        occurred_at=now, actor_id=user_id, source='caregiver_portal',
                                        payload_minimal={'track_id': existing['track_id']})
                    return conn.fetchone('SELECT * FROM family_music WHERE track_id = ?', (existing['track_id'],)), True
                self.media_dir.mkdir(parents=True, exist_ok=True)
                with temp.open('xb') as handle:
                    handle.write(data)
                temp.replace(path)
                created_file = True
                now = datetime.now(timezone.utc).isoformat()
                conn.execute('INSERT INTO family_music '
                             '(track_id, patient_id, uploaded_by, title, original_filename, stored_name, '
                             'content_type, size_bytes, sha256, duration_seconds, created_at) '
                             'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                             (track_id, patient_id, user_id, title, filename, stored_name,
                              AUDIO_TYPES[extension][0], len(data), digest, math.ceil(duration), now))
                insert_outbox_event(conn, patient_id, 'music.track_uploaded', 'music_track', track_id,
                                    occurred_at=now, actor_id=user_id, source='caregiver_portal',
                                    payload_minimal={'track_id': track_id})
                row = conn.fetchone('SELECT * FROM family_music WHERE track_id = ?', (track_id,))
            return row, True
        except Exception:
            if created_file:
                path.unlink(missing_ok=True)
            temp.unlink(missing_ok=True)
            raise
