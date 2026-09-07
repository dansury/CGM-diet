<?php
/**
 * POST /api/register.php — optional account creation for reliable cloud backup.
 * spec: spec/web.md § Регистрация и синхронизация.
 * Request {email, password} -> {syncToken}
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
$in = json_input();
$email = is_string($in['email'] ?? null) ? strtolower(trim($in['email'])) : '';
$password = is_string($in['password'] ?? null) ? $in['password'] : '';

if (!filter_var($email, FILTER_VALIDATE_EMAIL)) json_error('invalid email');
if (strlen($password) < 8) json_error('password must be at least 8 characters');

$emailHash = hash('sha256', $email);
$pdo = web_db();

$stmt = $pdo->prepare('SELECT id FROM users WHERE email_hash = :e');
$stmt->execute([':e' => $emailHash]);
if ($stmt->fetch()) json_error('an account for this email already exists — use /api/login.php', 409);

$syncToken = web_random_token();
$stmt = $pdo->prepare(
    'INSERT INTO users(email_hash, password_hash, sync_token, created_at) VALUES (:e, :p, :t, :c)'
);
$stmt->execute([
    ':e' => $emailHash,
    ':p' => password_hash($password, PASSWORD_DEFAULT),
    ':t' => $syncToken,
    ':c' => gmdate('Y-m-d\TH:i:s\Z'),
]);

json_out(['syncToken' => $syncToken]);
