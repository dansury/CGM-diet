<?php
/**
 * GET/POST /api/push_send.php?key=<CRON_SECRET> — one dispatch tick: every
 * notification whose slot has just come round, for every subscriber, in that
 * subscriber's own local time. Meant to be called by the hosting's cron every
 * 5–15 minutes (shared PHP hosting has no persistent worker).
 *
 * `&code=<notification>` sends that one notification to everybody right now,
 * off-schedule — what the admin panel's «send now» button uses.
 * spec: spec/notifications.md § Отправка.
 *
 * [BLOCKED: delivery not verified against a live push service/browser — see
 * web/lib/webpush.php header.]
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

$pdo = web_db();
$cronSecret = web_config_get($pdo, 'CRON_SECRET');
$given = $_GET['key'] ?? ($_POST['key'] ?? '');
if (!$cronSecret || !hash_equals($cronSecret, (string) $given)) {
    json_error('invalid or missing cron key', 403);
}

$code = is_string($_GET['code'] ?? null) ? notify_slug($_GET['code']) : '';
if ($code !== '') {
    json_out(notify_send_now($pdo, $code));
}

json_out(notify_dispatch($pdo));
