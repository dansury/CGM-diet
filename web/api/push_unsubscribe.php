<?php
/**
 * POST /api/push_unsubscribe.php — remove a browser PushSubscription.
 * spec: spec/web.md § Push-уведомления.
 * Request {endpoint}
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
$in = json_input();
$endpoint = is_string($in['endpoint'] ?? null) ? $in['endpoint'] : '';
if ($endpoint === '') json_error('endpoint is required');

$stmt = web_db()->prepare('DELETE FROM push_subscriptions WHERE endpoint = :e');
$stmt->execute([':e' => $endpoint]);

json_out(['ok' => true]);
