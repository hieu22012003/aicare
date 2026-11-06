-- Requests and delivery queue live in the unified store and publish existing SSE outbox events.
CREATE TABLE IF NOT EXISTS voice_requests (
 request_id TEXT PRIMARY KEY,
 patient_id TEXT NOT NULL REFERENCES patients(patient_id),
 utterance_id TEXT NOT NULL,
 action_identity TEXT NOT NULL,
 intent TEXT NOT NULL,
 original_text TEXT NOT NULL,
 priority TEXT NOT NULL,
 context_refs TEXT NOT NULL,
 rule_ids TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT 'voice',
 status TEXT NOT NULL CHECK(status IN ('open','acknowledged','resolved','cancelled')),
 created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 UNIQUE(utterance_id, action_identity)
);
CREATE TABLE IF NOT EXISTS voice_deliveries (
 request_id TEXT NOT NULL REFERENCES voice_requests(request_id),
 recipient_id TEXT NOT NULL REFERENCES users(user_id),
 recipient_email TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','failed','unknown','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0,
 transport_status TEXT,
 next_attempt_at TEXT,
 PRIMARY KEY(request_id, recipient_id)
);

CREATE INDEX IF NOT EXISTS idx_voice_requests_patient_created ON voice_requests(patient_id, created_at);
CREATE INDEX IF NOT EXISTS idx_voice_deliveries_status ON voice_deliveries(status, next_attempt_at);
