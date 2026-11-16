-- 002：幂等键表（owner/job/action/request_id 去重）
CREATE TABLE idempotency (
  key TEXT PRIMARY KEY,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
