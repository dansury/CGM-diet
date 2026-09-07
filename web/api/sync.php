<?php
/**
 * GET/PUT /api/sync.php — full IndexedDB snapshot backup/restore.
 * spec: spec/web.md § Регистрация и синхронизация.
 * Auth: Authorization: Bearer <syncToken>.
 * GET  -> {payload: <last PUT'ed JSON>, updatedAt} | 404 if never backed up
 * PUT  {payload: <any JSON>} -> {ok:true, updatedAt}
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

$user = auth_user_by_token();
if ($user === null) json_error('invalid or missing sync token', 401);

$method = $_SERVER['REQUEST_METHOD'] ?? '';
$pdo = web_db();

if ($method === 'GET') {
    $stmt = $pdo->prepare('SELECT payload, updated_at FROM backups WHERE user_id = :u');
    $stmt->execute([':u' => $user['id']]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    if (!$row) json_error('no backup yet', 404);
    json_out(['payload' => json_decode($row['payload'], true), 'updatedAt' => $row['updated_at']]);
}

if ($method === 'PUT') {
    $in = json_input();
    if (!array_key_exists('payload', $in)) json_error('payload is required');
    $now = gmdate('Y-m-d\TH:i:s\Z');
    $stmt = $pdo->prepare(
        'INSERT INTO backups(user_id, payload, updated_at) VALUES (:u, :p, :t)
         ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at'
    );
    $stmt->execute([':u' => $user['id'], ':p' => json_encode($in['payload'], JSON_UNESCAPED_UNICODE), ':t' => $now]);
    json_out(['ok' => true, 'updatedAt' => $now]);
}

json_error('method not allowed', 405);
