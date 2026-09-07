<?php
/**
 * GET/POST /api/push_send.php?key=<CRON_SECRET> — send the "measure your sugar"
 * reminder to every subscriber with reminders_enabled=1. Meant to be called by
 * the hosting's cron (shared PHP hosting has no persistent worker); the admin
 * panel's "send now" button calls webpush_send_reminders() directly instead.
 * spec: spec/web.md § Push-уведомления.
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

$title = is_string($_GET['title'] ?? null) ? $_GET['title'] : 'Пора измерить сахар';
$body = is_string($_GET['body'] ?? null) ? $_GET['body'] : 'Загляните в приложение и отметьте показатель — так статистика точнее.';

json_out(webpush_send_reminders($pdo, $title, $body));
