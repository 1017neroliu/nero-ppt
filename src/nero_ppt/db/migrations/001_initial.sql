-- 001：初始表结构。ID 均为随机字符串；时间戳 ISO 文本。
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_sessions_user ON sessions(user_id);

CREATE TABLE jobs (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id),
  title TEXT NOT NULL,
  scene_id TEXT NOT NULL,
  scene_version INTEGER NOT NULL,
  template_profile_id TEXT,
  brief_json TEXT,
  phase TEXT NOT NULL DEFAULT 'intake',
  status TEXT NOT NULL DEFAULT 'ready',
  job_revision INTEGER NOT NULL DEFAULT 1,
  batch_index INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_jobs_owner ON jobs(owner_id);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  display_name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('material', 'template')),
  file_ext TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  parse_status TEXT NOT NULL DEFAULT 'pending',
  stored_path TEXT NOT NULL,
  uploaded_at TEXT NOT NULL
);
CREATE INDEX idx_documents_job ON documents(job_id);

CREATE TABLE template_profiles (
  id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES jobs(id),
  template_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  file_hash TEXT NOT NULL,
  profile_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE revisions (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  revision INTEGER NOT NULL,
  kind TEXT NOT NULL,
  data_json TEXT NOT NULL,
  input_revision INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(job_id, revision)
);

CREATE TABLE steps (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  kind TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','running','done','failed','interrupted','cancelled')),
  input_revision INTEGER NOT NULL,
  scope_key TEXT NOT NULL DEFAULT '',
  payload_json TEXT,
  result_json TEXT,
  error TEXT,
  checkpoint_json TEXT,
  lease_owner TEXT,
  lease_expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(job_id, input_revision, kind, scope_key)
);
CREATE INDEX idx_steps_status ON steps(status);

CREATE TABLE artifacts (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  kind TEXT NOT NULL CHECK (kind IN ('pptx','pdf','notes','preview','thumbnail','generic')),
  revision INTEGER NOT NULL,
  slide_id TEXT,
  relative_path TEXT NOT NULL,
  file_hash TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(job_id, revision, kind, slide_id, relative_path)
);
CREATE INDEX idx_artifacts_job ON artifacts(job_id, revision);

CREATE TABLE reviews (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  target_kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  target_revision INTEGER NOT NULL,
  reviewer_id TEXT NOT NULL REFERENCES users(id),
  decision TEXT NOT NULL CHECK (decision IN ('approve','reject')),
  comment TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(job_id, target_kind, target_id, target_revision, reviewer_id, decision)
);

CREATE TABLE change_requests (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  plan_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed','approved','rejected','applied')),
  created_at TEXT NOT NULL
);

CREATE TABLE releases (
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id),
  job_revision INTEGER NOT NULL,
  manifest_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
