CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'web',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  tool_calls TEXT NOT NULL DEFAULT '[]',
  tool_call_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'running',
  error TEXT NOT NULL DEFAULT '',
  usage TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS goals (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  evidence TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  met_at TEXT
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL,
  parent_id TEXT,
  instruction TEXT NOT NULL,
  acceptance TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT 'builtin',
  cwd TEXT NOT NULL DEFAULT '',
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  lease_owner TEXT,
  lease_until TEXT,
  result TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_deps (
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  depends_on_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  PRIMARY KEY (item_id, depends_on_id),
  CHECK (item_id != depends_on_id)
);

CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  worker_id TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  output TEXT NOT NULL DEFAULT '',
  error_kind TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  exit_code INTEGER
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_frontier ON items(run_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_items_lease ON items(lease_until, status);
CREATE INDEX IF NOT EXISTS idx_item_deps_parent ON item_deps(depends_on_id);
CREATE INDEX IF NOT EXISTS idx_attempts_item ON attempts(item_id, id);

CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  result TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL DEFAULT 1,
  ms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
