<?php
/**
 * GET  /api/notify_config.php?clientId=…  — notifications the admin authored
 *      plus this device's own overrides, so the app can show one merged list.
 * POST /api/notify_config.php             — save the overrides of one device.
 *      {clientId, tzOffset, prefs: [{code, mode, times:[...]}, …]}
 * The «smart» times are computed on the device (app/js/notify.js) and arrive
 * here already resolved: records never leave the phone.
 * spec: spec/notifications.md.
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

$pdo = web_db();

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'GET') {
    $clientId = is_string($_GET['clientId'] ?? null) ? substr(trim($_GET['clientId']), 0, 64) : '';
    $prefs = $clientId !== '' ? notify_prefs($pdo, $clientId) : [];
    $out = [];
    foreach (notify_templates($pdo, true) as $template) {
        $pref = $prefs[$template['code']] ?? null;
        $resolved = notify_resolve($template, $pref);
        $out[] = [
            'code' => $template['code'],
            'title' => $template['title'],
            'body' => $template['body'],
            'action' => $template['action'],
            'signal' => array_values(array_filter(explode(',', (string) $template['signal']))),
            'leadMin' => (int) $template['lead_min'],
            'adminMode' => $template['mode'],
            'adminTimes' => notify_parse_times((string) $template['times']),
            'mode' => $pref['mode'] ?? 'default',
            'times' => notify_parse_times((string) ($pref['times'] ?? '')),
            'effectiveMode' => $resolved['mode'],
            'effectiveTimes' => $resolved['times'],
        ];
    }
    json_out(['notifications' => $out]);
}

require_method('POST');
$in = json_input();
$clientId = is_string($in['clientId'] ?? null) ? substr(trim($in['clientId']), 0, 64) : '';
if ($clientId === '') json_error('clientId is required');
$tzOffset = (int) ($in['tzOffset'] ?? 0);

foreach ((array) ($in['prefs'] ?? []) as $pref) {
    if (!is_array($pref) || !is_string($pref['code'] ?? null)) continue;
    $times = array_values(array_filter(array_map(
        static fn ($t): string => is_string($t) ? $t : '',
        (array) ($pref['times'] ?? [])
    )));
    notify_save_pref($pdo, $clientId, $pref['code'], (string) ($pref['mode'] ?? 'default'), $times, $tzOffset);
}

// The dispatcher reads the offset from the subscription itself: prefs may be
// absent entirely (everything left «как у всех») and the clock still has to
// be right.
$pdo->prepare('UPDATE push_subscriptions SET tz_offset = :z WHERE client_id = :c')
    ->execute([':z' => max(-840, min(840, $tzOffset)), ':c' => $clientId]);

json_out(['ok' => true]);
