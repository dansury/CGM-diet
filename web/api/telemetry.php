<?php
/**
 * POST /api/telemetry.php — one usage/notification event.
 * spec: spec/web.md § Телеметрия. Collected from every visitor, registered or not.
 *
 * Request {clientId, kind, payload?, utm?: {source,medium,campaign,term,content}}
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

require_method('POST');
$in = json_input();
$clientId = is_string($in['clientId'] ?? null) ? trim($in['clientId']) : '';
$kind = is_string($in['kind'] ?? null) ? trim($in['kind']) : '';
if ($clientId === '' || $kind === '') json_error('clientId and kind are required');
if (!preg_match('/^[a-z0-9_]{1,64}$/i', $kind)) json_error('invalid kind');

$payload = $in['payload'] ?? null;
$utm = is_array($in['utm'] ?? null) ? $in['utm'] : [];
$user = auth_user_by_token();

$stmt = web_db()->prepare(
    'INSERT INTO telemetry_events(client_id, user_id, kind, payload, utm_source, utm_medium, utm_campaign, utm_term, utm_content, ts)
     VALUES (:c, :u, :k, :p, :s, :m, :cp, :tm, :ct, :t)'
);
$stmt->execute([
    ':c' => substr($clientId, 0, 64),
    ':u' => $user['id'] ?? null,
    ':k' => $kind,
    ':p' => $payload !== null ? json_encode($payload, JSON_UNESCAPED_UNICODE) : null,
    ':s' => is_string($utm['source'] ?? null) ? substr($utm['source'], 0, 128) : null,
    ':m' => is_string($utm['medium'] ?? null) ? substr($utm['medium'], 0, 128) : null,
    ':cp' => is_string($utm['campaign'] ?? null) ? substr($utm['campaign'], 0, 128) : null,
    ':tm' => is_string($utm['term'] ?? null) ? substr($utm['term'], 0, 128) : null,
    ':ct' => is_string($utm['content'] ?? null) ? substr($utm['content'], 0, 128) : null,
    ':t' => gmdate('Y-m-d\TH:i:s\Z'),
]);

json_out(['ok' => true]);
