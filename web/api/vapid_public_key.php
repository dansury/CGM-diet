<?php
/**
 * GET /api/vapid_public_key.php — the applicationServerKey the client needs
 * for pushManager.subscribe(). Generates and persists a VAPID keypair into
 * web_config on first use. spec: spec/web.md § Push-уведомления.
 */

declare(strict_types=1);
require __DIR__ . '/_bootstrap.php';

$pdo = web_db();
$public = web_config_get($pdo, 'VAPID_PUBLIC_KEY');
$privatePem = web_config_get($pdo, 'VAPID_PRIVATE_KEY_PEM');

if ($public === null || $privatePem === null) {
    $keys = webpush_generate_vapid_keypair();
    web_config_set($pdo, 'VAPID_PUBLIC_KEY', $keys['public']);
    web_config_set($pdo, 'VAPID_PRIVATE_KEY_PEM', $keys['private_pem']);
    $public = $keys['public'];
}

json_out(['publicKey' => $public]);
