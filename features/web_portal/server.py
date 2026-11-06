"""
Standalone High-Performance HTTP Server for Healthcare Web Portals.
Powered by Python Standard Library ThreadingHTTPServer.
Includes Real-Time Server-Sent Events (SSE) Stream Gateway and Outbox Relay Worker.
Runs 100% offline with zero external package dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.database.connection import UnifiedDatabaseManager
from shared.services.outbox_relay import OutboxRelayService
from features.web_portal.api_router import ApiRouter
from features.web_portal.auth import AuthService
from features.web_portal.sse_hub import sse_hub

logger = logging.getLogger("WebServer")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class HealthcareHttpHandler(BaseHTTPRequestHandler):
    # Server references injected onto class
    router: Optional[ApiRouter] = None
    auth: Optional[AuthService] = None

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as exc:
            logger.debug("HTTP client disconnected: %s", exc)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/assets/portal-theme.css":
            self._serve_file(TEMPLATES_DIR.parent / "assets" / "portal-theme.css")
            return

        if path in ("/health", "/api/health"):
            try:
                db_ok = False
                if self.router and getattr(self.router, "db", None):
                    with self.router.db.connect() as conn:
                        row = conn.fetchone("SELECT 1 as ok")
                        if row and row.get("ok") == 1:
                            db_ok = True
                if db_ok:
                    self._send_json(200, {
                        "status": "ok",
                        "ready": True,
                        "database": "connected",
                        "service": "AI Care Healthcare Backend",
                        "portals": {
                            "login": "/login",
                            "caregiver": "/caregiver",
                            "clinician": "/clinician",
                            "admin": "/admin"
                        }
                    })
                    return
                else:
                    self._send_json(503, {"status": "error", "ready": False, "error": "Database not ready"})
                    return
            except Exception as e:
                self._send_json(503, {"status": "error", "ready": False, "error": str(e)})
                return

        if path in ("/", "/index.html"):
            self._serve_file(TEMPLATES_DIR / "login.html")
            return
        elif path == "/login":
            self._serve_file(TEMPLATES_DIR / "login.html")
            return
        elif path == "/register":
            # Caregiver registration is exclusively performed through the Elder Desktop Application
            self.send_response(302)
            self.send_header("Location", "/login?msg=register_via_app")
            self.end_headers()
            return
        elif path == "/caregiver":
            self._serve_file(TEMPLATES_DIR / "portal_caregiver.html")
            return
        elif path == "/clinician":
            self._serve_file(TEMPLATES_DIR / "portal_clinician.html")
            return
        elif path in ("/admin", "/portal/admin"):
            self._serve_file(TEMPLATES_DIR / "portal_admin.html")
            return

        # SSE Real-time Streaming Gateway: /api/stream or /v1/stream
        if path in ("/api/stream", "/v1/stream"):
            self._handle_sse_stream(query)
            return

        if path.startswith("/api/"):
            token = self._extract_token() or query.get("token")
            ip = self.client_address[0]
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, res = self.router.handle_request("GET", path, query, {}, token, ip, headers=headers)
            if status == 200 and '_audio_path' in res:
                self._serve_audio(Path(res['_audio_path']), res['content_type'])
                return
            self._send_json(status, res)
            return

        self.send_error(404, "Page not found")

    def do_POST(self) -> None:
        self._handle_body_request("POST")

    def do_PUT(self) -> None:
        self._handle_body_request("PUT")

    def do_DELETE(self) -> None:
        self._handle_body_request("DELETE")

    def do_PATCH(self) -> None:
        self._handle_body_request("PATCH")

    def _handle_body_request(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = dict(urllib.parse.parse_qsl(parsed.query))

        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send_json(400, {'error': 'invalid_content_length'})
            return
        if path == '/api/transcript-intent' and not 0 <= content_length <= 4096:
            self._send_json(413, {'error': 'intent_request_too_large'})
            return
        music_upload = method == 'POST' and re.fullmatch(r'/api/patients/[^/]+/music', path)
        if music_upload:
            from features.music_care.family_music_service import MAX_AUDIO_BYTES
            if not 0 < content_length <= MAX_AUDIO_BYTES:
                self.close_connection = True
                self._send_json(413, {'error': 'Vui lòng chọn tệp nhạc không quá 50 MB'})
                return
            # Authorize before reading or decoding a potentially large upload.
            token = self._extract_token()
            session = self.auth.get_session(token) if token else None
            if not session:
                self.close_connection = True
                self._send_json(401, {'error': 'Vui lòng đăng nhập trước khi tải nhạc'})
                return
            patient_id = path.split('/')[3]
            allowed, message = self.auth.check_patient_access(session['user_id'], patient_id)
            if not allowed or session['role'] not in ('caregiver', 'admin'):
                self.close_connection = True
                self._send_json(403, {'error': message if not allowed else 'Chỉ người nhà được tải nhạc lên'})
                return
            raw = self.rfile.read(content_length)
            if len(raw) != content_length:
                self._send_json(400, {'error': 'Tải tệp chưa hoàn tất, vui lòng thử lại'})
                return
            body = {'_audio_bytes': raw,
                    'filename': urllib.parse.unquote(self.headers.get('X-File-Name', '')),
                    'title': urllib.parse.unquote(self.headers.get('X-Track-Title', ''))}
            status, res = self.router.handle_request(method, path, query, body, token, self.client_address[0])
            self._send_json(status, res)
            return
        body = {}
        if content_length > 0:
            try:
                raw_body = self.rfile.read(content_length).decode("utf-8")
                body = json.loads(raw_body)
            except Exception:
                if path == '/api/transcript-intent':
                    self._send_json(400, {'error': 'invalid_intent_json'})
                    return
                body = {}

        if path.startswith("/api/"):
            token = self._extract_token() or query.get("token")
            ip = self.client_address[0]
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, res = self.router.handle_request(method, path, query, body, token, ip, headers=headers)
            self._send_json(status, res)
            return

        self.send_error(404, "Endpoint not found")

    def _handle_sse_stream(self, query: Dict[str, str]) -> None:
        """Establishes persistent Server-Sent Events (SSE) connection with authentication."""
        token = query.get("token") or self._extract_token()
        session = self.auth.get_session(token) if token and self.auth else None
        if not session:
            self._send_json(401, {"error": "Chưa đăng nhập để kết nối dòng sự kiện"})
            return

        user_id = session["user_id"]
        role = session["role"]

        # Send SSE HTTP Response Headers
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self._send_cors_headers()
        self.end_headers()

        client = sse_hub.register_client(user_id, role)

        # Initial handshake event
        init_payload = json.dumps({"status": "connected", "user_id": user_id, "role": role}, ensure_ascii=False)
        try:
            self.wfile.write(f"event: init\ndata: {init_payload}\n\n".encode("utf-8"))
            self.wfile.flush()
        except Exception as e:
            logger.debug("Failed sending initial SSE handshake: %s", e)
            sse_hub.unregister_client(client.client_id)
            return

        # Keep alive streaming loop
        try:
            while client.active:
                try:
                    event_data = client.queue.get(timeout=10.0)
                    if not event_data or event_data.get("type") == "close":
                        break
                    raw = json.dumps(event_data, ensure_ascii=False)
                    self.wfile.write(f"data: {raw}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Heartbeat comment to keep connection alive
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, Exception) as e:
            logger.debug("SSE client disconnected: %s (%s)", client.client_id, e)
        finally:
            sse_hub.unregister_client(client.client_id)

    def _extract_token(self) -> Optional[str]:
        auth_hdr = self.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            return auth_hdr[7:].strip()
        cookie_hdr = self.headers.get("Cookie", "")
        for item in cookie_hdr.split(";"):
            if "auth_token=" in item:
                return item.split("auth_token=")[1].strip()
        return None

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Idempotency-Key")

    def _send_json(self, status: int, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def _serve_file(self, file_path: Path) -> None:
        if not file_path.exists():
            self.send_error(404, "File not found")
            return
        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/css; charset=utf-8" if file_path.suffix == ".css" else "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_audio(self, path: Path, content_type: str) -> None:
        """Private authenticated audio, with byte ranges for browser seeking."""
        try:
            handle = path.open('rb')
        except OSError:
            self._send_json(404, {'error': 'Không tìm thấy tệp nhạc'})
            return
        with handle:
            size = os.fstat(handle.fileno()).st_size
            start, end, status = 0, size - 1, 200
            range_header = self.headers.get('Range')
            if range_header:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
                if not match or not any(match.groups()):
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.end_headers()
                    return
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.end_headers()
                    return
                status = 206
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            handle.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = handle.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


class HealthcareWebServer:
    def __init__(self, db: UnifiedDatabaseManager, host: str = "127.0.0.1", port: int = 8080) -> None:
        self.db = db
        try:
            from shared.services.patient_sync_service import sync_all_patient_profiles
            sync_all_patient_profiles(self.db)
        except Exception as e:
            logger.warning("Patient sync on web server init skipped: %s", e)

        self.auth = AuthService(db)
        self.outbox_relay = OutboxRelayService(db, self.auth, poll_interval=0.5)
        self.router = ApiRouter(db, self.auth, outbox_relay=self.outbox_relay)
        self.host = host
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self._thread = None

    def start(self, block: bool = False) -> None:
        HealthcareHttpHandler.router = self.router
        HealthcareHttpHandler.auth = self.auth
        ThreadingHTTPServer.allow_reuse_address = True

        # Try binding to requested port, auto-fallback to next ports if occupied
        max_attempts = 10
        current_port = self.port
        bound = False
        for offset in range(max_attempts):
            try:
                candidate_port = current_port + offset
                self.httpd = ThreadingHTTPServer((self.host, candidate_port), HealthcareHttpHandler)
                self.port = self.httpd.server_port
                bound = True
                break
            except OSError as e:
                if e.errno in (98, 10048) or getattr(e, "winerror", None) == 10048 or "Address already in use" in str(e):
                    if offset == 0:
                        print(f"⚠️ Cổng {current_port} đang bận (Address already in use). Đang thử cổng tiếp theo...")
                else:
                    raise

        if not bound or not self.httpd:
            raise OSError(f"Không thể mở cổng từ {current_port} đến {current_port + max_attempts - 1}. Vui lòng giải phóng cổng.")

        self.outbox_relay.start()
        print(f"\n=======================================================")
        print(f"🚀 HEALTHCARE WEB SERVER & SSE GATEWAY ĐANG CHẠY")
        print(f"🛡️ URL Cổng Quản trị viên: http://{self.host}:{self.port}/admin")
        print(f"👨‍👩‍👧 URL Cổng Người nhà:     http://{self.host}:{self.port}/caregiver")
        print(f"👨‍⚕️ URL Cổng Bác sĩ:        http://{self.host}:{self.port}/clinician")
        print(f"📡 SSE Stream Gateway:     http://{self.host}:{self.port}/api/stream")
        print(f"ℹ️ Nhấn Ctrl+C để dừng máy chủ.")
        print(f"=======================================================\n")
        logger.info("Healthcare Web Server started at http://%s:%d", self.host, self.port)

        if block:
            try:
                self.httpd.serve_forever()
            except KeyboardInterrupt:
                self.stop()
        else:
            import threading
            self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self.outbox_relay:
            self.outbox_relay.stop()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            logger.info("Healthcare Web Server stopped.")


if __name__ == "__main__":
    import argparse
    from shared.database.migrator import Migrator

    parser = argparse.ArgumentParser(description="Healthcare Web Server & SSE Gateway")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Port number (default: 8080)")
    args = parser.parse_args()

    db = UnifiedDatabaseManager()
    Migrator(db).apply_all()

    server = HealthcareWebServer(db, host=args.host, port=args.port)
    server.start(block=True)
