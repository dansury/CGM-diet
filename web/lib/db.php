<?php
/**
 * web/lib/db.php — SQLite storage for the web app (telemetry, accounts,
 * backups, push subscriptions, web-only config). Separate tables from the
 * vendored `settings` table (web/lib/vendor/settings_store.php) — LLM/model
 * config stays in that shared shape; CGM-diet-specific config (admin
 * password, VAPID keys, cron secret) lives in `web_config` here so the
 * vendored files never need CGM-diet-specific keys.
 * spec: spec/web.md § Хранение. Notification tables live in
 * lib/notifications.php (spec/notifications.md) and are created from here.
 */

declare(strict_types=1);

require_once __DIR__ . '/notifications.php';

function web_db_connect(string $dbPath): PDO {
    $dir = dirname($dbPath);
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    $pdo = new PDO('sqlite:' . $dbPath);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec('PRAGMA journal_mode = WAL');
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS web_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )'
    );
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_hash TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            sync_token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )'
    );
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS backups (
            user_id INTEGER PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )'
    );
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            user_id INTEGER,
            kind TEXT NOT NULL,
            payload TEXT,
            utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, utm_term TEXT, utm_content TEXT,
            ts TEXT NOT NULL
        )'
    );
    $pdo->exec('CREATE INDEX IF NOT EXISTS ix_telemetry_kind_ts ON telemetry_events(kind, ts)');
    $pdo->exec('CREATE INDEX IF NOT EXISTS ix_telemetry_client ON telemetry_events(client_id)');
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            user_id INTEGER,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            reminders_enabled INTEGER NOT NULL DEFAULT 1,
            tz_offset INTEGER,
            created_at TEXT NOT NULL
        )'
    );
    // Installs created before notifications existed miss this column; the
    // schema above only covers a fresh database.
    web_add_column($pdo, 'push_subscriptions', 'tz_offset', 'INTEGER');
    notify_migrate($pdo);
    return $pdo;
}

/** ALTER TABLE ADD COLUMN, skipped when the column is already there. */
function web_add_column(PDO $pdo, string $table, string $column, string $type): void {
    $cols = $pdo->query('PRAGMA table_info(' . $table . ')')->fetchAll(PDO::FETCH_ASSOC) ?: [];
    foreach ($cols as $col) {
        if (($col['name'] ?? '') === $column) return;
    }
    $pdo->exec('ALTER TABLE ' . $table . ' ADD COLUMN ' . $column . ' ' . $type);
}

function web_config_get(PDO $pdo, string $key, ?string $default = null): ?string {
    $stmt = $pdo->prepare('SELECT value FROM web_config WHERE key = :k');
    $stmt->execute([':k' => $key]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    return $row ? (string) $row['value'] : $default;
}

function web_config_set(PDO $pdo, string $key, string $value): void {
    $stmt = $pdo->prepare(
        'INSERT INTO web_config(key, value, updated_at) VALUES (:k, :v, :t)
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at'
    );
    $stmt->execute([':k' => $key, ':v' => $value, ':t' => gmdate('Y-m-d\TH:i:s\Z')]);
}

function web_config_all(PDO $pdo): array {
    $out = [];
    foreach ($pdo->query('SELECT key, value FROM web_config')->fetchAll(PDO::FETCH_ASSOC) ?: [] as $row) {
        $out[$row['key']] = $row['value'];
    }
    return $out;
}

/** Random opaque token, e.g. sync tokens / cron secret / anonymous ids. */
function web_random_token(int $bytes = 32): string {
    return rtrim(strtr(base64_encode(random_bytes($bytes)), '+/', '-_'), '=');
}
