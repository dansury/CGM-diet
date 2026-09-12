<?php
/**
 * web/lib/webpush.php — Web Push without Composer: VAPID (RFC 8292, ES256 JWT)
 * and payload encryption (RFC 8291, aes128gcm: ECDH P-256 + HKDF-SHA256 +
 * AES-128-GCM), built on PHP's own `openssl` extension (`openssl_pkey_derive`
 * requires PHP >= 8.1). spec: spec/web.md § Push-уведомления.
 *
 * [BLOCKED: not exercised against a real push service/browser in this
 * environment — implemented to spec, php -l clean, but end-to-end delivery
 * needs a live device test, same caveat as apps/health-bridge.]
 */

declare(strict_types=1);

function webpush_b64url(string $bin): string {
    return rtrim(strtr(base64_encode($bin), '+/', '-_'), '=');
}

function webpush_b64url_decode(string $s): string {
    $s = strtr($s, '-_', '+/');
    $pad = strlen($s) % 4;
    if ($pad) $s .= str_repeat('=', 4 - $pad);
    $out = base64_decode($s, true);
    if ($out === false) throw new RuntimeException('invalid base64url');
    return $out;
}

/** New VAPID keypair. Store 'public' (sent to the browser as applicationServerKey
 *  and in the Authorization header's k=) and 'private_pem' (server-side only). */
function webpush_generate_vapid_keypair(): array {
    $res = openssl_pkey_new(['curve_name' => 'prime256v1', 'private_key_type' => OPENSSL_KEYTYPE_EC]);
    if ($res === false) throw new RuntimeException('EC keygen failed: ' . openssl_error_string());
    if (!openssl_pkey_export($res, $pem)) throw new RuntimeException('EC key export failed: ' . openssl_error_string());
    $details = openssl_pkey_get_details($res);
    $publicRaw = "\x04" . $details['ec']['x'] . $details['ec']['y'];
    return ['public' => webpush_b64url($publicRaw), 'private_pem' => $pem];
}

/** Minimal SPKI DER wrapper for a raw uncompressed P-256 point (0x04||X||Y, 65
 *  bytes) — lets openssl_pkey_get_public()/openssl_pkey_derive() work on a
 *  subscription's `p256dh` key without any ASN.1 library. */
function webpush_ec_public_key_pem_from_raw(string $rawPoint): string {
    if (strlen($rawPoint) !== 65 || $rawPoint[0] !== "\x04") {
        throw new RuntimeException('expected an uncompressed P-256 point (65 bytes, 0x04 prefix)');
    }
    $prefix = hex2bin('3059301306072a8648ce3d020106082a8648ce3d030107034200');
    $der = $prefix . $rawPoint;
    return "-----BEGIN PUBLIC KEY-----\n" . chunk_split(base64_encode($der), 64, "\n") . "-----END PUBLIC KEY-----\n";
}

function webpush_der_skip_length(string $der, int $offset): int {
    $len = ord($der[$offset]);
    return ($len & 0x80) ? 1 + ($len & 0x7f) : 1;
}

function webpush_der_read_int(string $der, int $offset): array {
    if (ord($der[$offset]) !== 0x02) throw new RuntimeException('bad DER INTEGER tag');
    $offset++;
    $len = ord($der[$offset]);
    $offset++;
    $val = substr($der, $offset, $len);
    return [$val, $offset + $len];
}

function webpush_fixed32(string $bytes): string {
    if (strlen($bytes) > 32) $bytes = substr($bytes, strlen($bytes) - 32);
    return str_pad($bytes, 32, "\x00", STR_PAD_LEFT);
}

/** ECDSA DER signature (ASN.1 SEQUENCE{r,s}) -> JOSE fixed r||s (64 bytes), per RFC 7518 §3.4. */
function webpush_der_to_jose(string $der): string {
    $offset = 1; // skip outer SEQUENCE tag (0x30)
    $offset += webpush_der_skip_length($der, $offset);
    [$r, $offset] = webpush_der_read_int($der, $offset);
    [$s, $offset] = webpush_der_read_int($der, $offset);
    return webpush_fixed32($r) . webpush_fixed32($s);
}

/** VAPID Authorization header value for one push endpoint (RFC 8292). */
function webpush_vapid_header(string $endpoint, string $vapidPublicB64, string $vapidPrivatePem, string $subject): string {
    $parsed = parse_url($endpoint);
    if (!$parsed || empty($parsed['scheme']) || empty($parsed['host'])) {
        throw new RuntimeException('invalid push endpoint URL');
    }
    $aud = $parsed['scheme'] . '://' . $parsed['host'] . (isset($parsed['port']) ? ':' . $parsed['port'] : '');

    $header = webpush_b64url(json_encode(['typ' => 'JWT', 'alg' => 'ES256'], JSON_UNESCAPED_SLASHES));
    $claims = webpush_b64url(json_encode(['aud' => $aud, 'exp' => time() + 12 * 3600, 'sub' => $subject], JSON_UNESCAPED_SLASHES));
    $signingInput = $header . '.' . $claims;

    $pkey = openssl_pkey_get_private($vapidPrivatePem);
    if ($pkey === false) throw new RuntimeException('bad VAPID private key: ' . openssl_error_string());
    $derSig = '';
    if (!openssl_sign($signingInput, $derSig, $pkey, OPENSSL_ALGO_SHA256)) {
        throw new RuntimeException('VAPID signing failed: ' . openssl_error_string());
    }
    $jwt = $signingInput . '.' . webpush_b64url(webpush_der_to_jose($derSig));

    return 'vapid t=' . $jwt . ', k=' . $vapidPublicB64;
}

/** RFC 8291 aes128gcm encryption of one push payload for one subscription.
 *  Returns ['body' => binary aes128gcm record]. Single record only (payload
 *  capped well under the 4096-byte record size — push reminders are short). */
function webpush_encrypt(string $payload, string $p256dhB64, string $authB64): array {
    $uaPublic = webpush_b64url_decode($p256dhB64);
    $authSecret = webpush_b64url_decode($authB64);

    $eph = openssl_pkey_new(['curve_name' => 'prime256v1', 'private_key_type' => OPENSSL_KEYTYPE_EC]);
    if ($eph === false) throw new RuntimeException('ephemeral EC keygen failed: ' . openssl_error_string());
    $ephDetails = openssl_pkey_get_details($eph);
    $ephPublicRaw = "\x04" . $ephDetails['ec']['x'] . $ephDetails['ec']['y'];

    $uaPubKey = openssl_pkey_get_public(webpush_ec_public_key_pem_from_raw($uaPublic));
    if ($uaPubKey === false) throw new RuntimeException('bad subscription p256dh key: ' . openssl_error_string());
    $sharedSecret = openssl_pkey_derive($uaPubKey, $eph, 32);
    if ($sharedSecret === false) throw new RuntimeException('ECDH derive failed: ' . openssl_error_string());

    $salt = random_bytes(16);
    $ikm = hash_hkdf('sha256', $sharedSecret, 32, "WebPush: info\x00" . $uaPublic . $ephPublicRaw, $authSecret);
    $cek = hash_hkdf('sha256', $ikm, 16, "Content-Encoding: aes128gcm\x00", $salt);
    $nonce = hash_hkdf('sha256', $ikm, 12, "Content-Encoding: nonce\x00", $salt);

    $recordSize = 4096;
    $plaintext = $payload . "\x02"; // delimiter octet, no extra padding needed for short payloads
    if (strlen($plaintext) > $recordSize - 16) {
        throw new RuntimeException('push payload too large for a single aes128gcm record');
    }

    $tag = '';
    $ciphertext = openssl_encrypt($plaintext, 'aes-128-gcm', $cek, OPENSSL_RAW_DATA, $nonce, $tag);
    if ($ciphertext === false) throw new RuntimeException('AES-128-GCM encrypt failed: ' . openssl_error_string());

    $header = $salt . pack('N', $recordSize) . chr(strlen($ephPublicRaw)) . $ephPublicRaw;
    return ['body' => $header . $ciphertext . $tag];
}

/**
 * VAPID keypair for this install, generated on first use. One place so cron,
 * the admin panel and lib/notifications.php all sign with the same key.
 */
function webpush_vapid(PDO $pdo): array {
    $vapidPublic = web_config_get($pdo, 'VAPID_PUBLIC_KEY');
    $vapidPrivate = web_config_get($pdo, 'VAPID_PRIVATE_KEY_PEM');
    if (!$vapidPublic || !$vapidPrivate) {
        $keys = webpush_generate_vapid_keypair();
        web_config_set($pdo, 'VAPID_PUBLIC_KEY', $keys['public']);
        web_config_set($pdo, 'VAPID_PRIVATE_KEY_PEM', $keys['private_pem']);
        $vapidPublic = $keys['public'];
        $vapidPrivate = $keys['private_pem'];
    }
    return [
        'public' => $vapidPublic,
        'private_pem' => $vapidPrivate,
        'subject' => 'mailto:' . (web_config_get($pdo, 'ADMIN_EMAIL') ?: 'admin@example.com'),
    ];
}

/**
 * Send one reminder to every subscriber with reminders_enabled=1 — the legacy
 * scheduleless path, kept for the admin panel's «send now».
 * Returns ['sent'=>int,'gone'=>int,'failed'=>int,'total'=>int].
 */
function webpush_send_reminders(PDO $pdo, string $title, string $body): array {
    $vapid = webpush_vapid($pdo);
    $payload = json_encode(['title' => $title, 'body' => $body], JSON_UNESCAPED_UNICODE);

    $subs = $pdo->query('SELECT * FROM push_subscriptions WHERE reminders_enabled = 1')->fetchAll(PDO::FETCH_ASSOC);
    $sent = 0;
    $gone = 0;
    $failed = 0;

    foreach ($subs as $sub) {
        try {
            $result = webpush_send($sub, $payload, $vapid);
            if ($result['status'] >= 200 && $result['status'] < 300) {
                $sent++;
            } elseif (in_array($result['status'], [404, 410], true)) {
                $pdo->prepare('DELETE FROM push_subscriptions WHERE id = :id')->execute([':id' => $sub['id']]);
                $gone++;
            } else {
                $failed++;
            }
            $pdo->prepare(
                'INSERT INTO telemetry_events(client_id, user_id, kind, payload, ts) VALUES (:c, :u, :k, :p, :t)'
            )->execute([
                ':c' => $sub['client_id'],
                ':u' => $sub['user_id'],
                ':k' => 'push_sent',
                ':p' => json_encode(['status' => $result['status']]),
                ':t' => gmdate('Y-m-d\TH:i:s\Z'),
            ]);
        } catch (Throwable $e) {
            $failed++;
        }
    }

    return ['sent' => $sent, 'gone' => $gone, 'failed' => $failed, 'total' => count($subs)];
}

/**
 * Send one push message. $subscription = ['endpoint','p256dh','auth'].
 * $vapid = ['public' => b64url raw point, 'private_pem', 'subject' => 'mailto:...'].
 * Returns ['status' => int HTTP code (0 on transport failure), 'error' => ?string].
 */
function webpush_send(array $subscription, string $payloadJson, array $vapid, int $ttl = 2419200): array {
    $enc = webpush_encrypt($payloadJson, $subscription['p256dh'], $subscription['auth']);
    $auth = webpush_vapid_header($subscription['endpoint'], $vapid['public'], $vapid['private_pem'], $vapid['subject']);

    $ch = curl_init($subscription['endpoint']);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_HTTPHEADER => [
            'TTL: ' . $ttl,
            'Content-Type: application/octet-stream',
            'Content-Encoding: aes128gcm',
            'Authorization: ' . $auth,
        ],
        CURLOPT_POSTFIELDS => $enc['body'],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 15,
    ]);
    $resp = curl_exec($ch);
    $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $err = curl_error($ch) ?: null;
    curl_close($ch);
    return ['status' => $status, 'error' => $resp === false ? $err : null];
}
