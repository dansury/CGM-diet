<?php
/**
 * POST /api/login.php — get a sync token back on a new device.
 * spec: spec/web.md § Регистрация и синхронизация.
 * Request {email, password} -> {syncToken}
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
$in = json_input();
$email = is_string($in['email'] ?? null) ? strtolower(trim($in['email'])) : '';
$password = is_string($in['password'] ?? null) ? $in['password'] : '';

$emailHash = hash('sha256', $email);
$stmt = web_db()->prepare('SELECT * FROM users WHERE email_hash = :e');
$stmt->execute([':e' => $emailHash]);
$user = $stmt->fetch(PDO::FETCH_ASSOC);

if (!$user || !password_verify($password, $user['password_hash'])) {
    json_error('invalid email or password', 401);
}

json_out(['syncToken' => $user['sync_token']]);
