<?php
/**
 * POST /api/push_subscribe.php — store a browser PushSubscription.
 * spec: spec/web.md § Push-уведомления.
 * Request {clientId, subscription: {endpoint, keys:{p256dh,auth}}}
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
$in = json_input();
$clientId = is_string($in['clientId'] ?? null) ? substr(trim($in['clientId']), 0, 64) : '';
$sub = $in['subscription'] ?? null;
$endpoint = is_array($sub) && is_string($sub['endpoint'] ?? null) ? $sub['endpoint'] : '';
$p256dh = is_array($sub['keys'] ?? null) && is_string($sub['keys']['p256dh'] ?? null) ? $sub['keys']['p256dh'] : '';
$auth = is_array($sub['keys'] ?? null) && is_string($sub['keys']['auth'] ?? null) ? $sub['keys']['auth'] : '';

if ($clientId === '' || $endpoint === '' || $p256dh === '' || $auth === '') {
    json_error('clientId and a full subscription (endpoint, keys.p256dh, keys.auth) are required');
}

$user = auth_user_by_token();
$stmt = web_db()->prepare(
    'INSERT INTO push_subscriptions(client_id, user_id, endpoint, p256dh, auth, reminders_enabled, created_at)
     VALUES (:c, :u, :e, :p, :a, 1, :t)
     ON CONFLICT(endpoint) DO UPDATE SET client_id = excluded.client_id, user_id = excluded.user_id,
       p256dh = excluded.p256dh, auth = excluded.auth, reminders_enabled = 1'
);
$stmt->execute([
    ':c' => $clientId,
    ':u' => $user['id'] ?? null,
    ':e' => $endpoint,
    ':p' => $p256dh,
    ':a' => $auth,
    ':t' => gmdate('Y-m-d\TH:i:s\Z'),
]);

json_out(['ok' => true]);
