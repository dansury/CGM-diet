<?php
/**
 * POST /api/push_subscribe.php — store a browser PushSubscription.
 * spec: spec/web.md § Push-уведомления.
 * Request {clientId, tzOffset?, subscription: {endpoint, keys:{p256dh,auth}}}
 * tzOffset — minutes east of UTC; the dispatcher schedules in local time
 * (spec/notifications.md § Отправка).
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
$tzOffset = max(-840, min(840, (int) ($in['tzOffset'] ?? 0)));

if ($clientId === '' || $endpoint === '' || $p256dh === '' || $auth === '') {
    json_error('clientId and a full subscription (endpoint, keys.p256dh, keys.auth) are required');
}

$user = auth_user_by_token();
$stmt = web_db()->prepare(
    'INSERT INTO push_subscriptions(client_id, user_id, endpoint, p256dh, auth, reminders_enabled, tz_offset, created_at)
     VALUES (:c, :u, :e, :p, :a, 1, :z, :t)
     ON CONFLICT(endpoint) DO UPDATE SET client_id = excluded.client_id, user_id = excluded.user_id,
       p256dh = excluded.p256dh, auth = excluded.auth, reminders_enabled = 1,
       tz_offset = excluded.tz_offset'
);
$stmt->execute([
    ':c' => $clientId,
    ':u' => $user['id'] ?? null,
    ':e' => $endpoint,
    ':p' => $p256dh,
    ':a' => $auth,
    ':z' => $tzOffset,
    ':t' => gmdate('Y-m-d\TH:i:s\Z'),
]);

json_out(['ok' => true]);
