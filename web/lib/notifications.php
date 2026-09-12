<?php
/**
 * web/lib/notifications.php — notification templates (authored in the admin
 * panel), per-visitor overrides and the cron dispatch that turns them into web
 * push. The «smart» time itself is computed on the device, where the records
 * live (web/app/js/notify.js) — the server only ever sees a list of times.
 * spec: spec/notifications.md.
 */

declare(strict_types=1);

const NOTIFY_SEND_WINDOW_MIN = 30;   // how long a slot stays due for a slow cron
const NOTIFY_ACTIONS = ['camera', 'text', 'open'];
const NOTIFY_MODES = ['fixed', 'smart', 'off'];
const NOTIFY_USER_MODES = ['default', 'fixed', 'smart', 'off'];
const NOTIFY_SIGNALS = ['meal', 'glucose', 'activity', 'weight', 'wellbeing'];
const NOTIFY_LEAD_MIN = 15;

/** Tables live next to the rest of the web schema (lib/db.php calls this). */
function notify_migrate(PDO $pdo): void {
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS notification_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT "camera",
            mode TEXT NOT NULL DEFAULT "fixed",
            times TEXT NOT NULL DEFAULT "",
            signal TEXT NOT NULL DEFAULT "",
            lead_min INTEGER NOT NULL DEFAULT 15,
            enabled INTEGER NOT NULL DEFAULT 1,
            sort INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )'
    );
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS notification_prefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            code TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT "default",
            times TEXT NOT NULL DEFAULT "",
            tz_offset INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            UNIQUE(client_id, code)
        )'
    );
    $pdo->exec(
        'CREATE TABLE IF NOT EXISTS notification_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            code TEXT NOT NULL,
            slot TEXT NOT NULL,
            sent_on TEXT NOT NULL,
            status INTEGER NOT NULL DEFAULT 0,
            ts TEXT NOT NULL,
            UNIQUE(client_id, code, slot, sent_on)
        )'
    );
    notify_seed($pdo);
}

/**
 * First run only: two notifications so a fresh install is not silent. Both are
 * ordinary rows — the admin edits or deletes them like any other.
 */
function notify_seed(PDO $pdo): void {
    $count = (int) $pdo->query('SELECT COUNT(*) FROM notification_templates')->fetchColumn();
    if ($count > 0) return;
    $seed = [
        [
            'code' => 'meal', 'title' => 'Пора записать еду',
            'body' => 'Сфотографируйте тарелку или ответьте названием блюда — запись займёт секунду.',
            'action' => 'camera', 'mode' => 'fixed', 'times' => '08:30,13:30,19:00',
            'signal' => 'meal', 'sort' => 10,
        ],
        [
            'code' => 'cgm', 'title' => 'Скриншот датчика',
            'body' => 'Пришлите скриншот CGM — по нему статистика свяжет еду и сахар.',
            'action' => 'camera', 'mode' => 'fixed', 'times' => '10:00,22:00',
            'signal' => 'glucose', 'sort' => 20,
        ],
    ];
    foreach ($seed as $row) notify_save_template($pdo, $row);
}

function notify_templates(PDO $pdo, bool $onlyEnabled = false): array {
    $sql = 'SELECT * FROM notification_templates';
    if ($onlyEnabled) $sql .= ' WHERE enabled = 1';
    $sql .= ' ORDER BY sort, id';
    return $pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC) ?: [];
}

function notify_template(PDO $pdo, string $code): ?array {
    $stmt = $pdo->prepare('SELECT * FROM notification_templates WHERE code = :c');
    $stmt->execute([':c' => $code]);
    return $stmt->fetch(PDO::FETCH_ASSOC) ?: null;
}

/** Insert or update by `code`. Unknown enum values fall back to the default. */
function notify_save_template(PDO $pdo, array $row): void {
    $code = notify_slug((string) ($row['code'] ?? ''));
    if ($code === '') return;
    $action = in_array($row['action'] ?? '', NOTIFY_ACTIONS, true) ? $row['action'] : 'camera';
    $mode = in_array($row['mode'] ?? '', NOTIFY_MODES, true) ? $row['mode'] : 'fixed';
    $signal = implode(',', array_values(array_intersect(
        NOTIFY_SIGNALS,
        array_map('trim', explode(',', (string) ($row['signal'] ?? '')))
    )));
    $stmt = $pdo->prepare(
        'INSERT INTO notification_templates(code, title, body, action, mode, times, signal, lead_min, enabled, sort, updated_at)
         VALUES (:c, :t, :b, :a, :m, :ti, :s, :l, :e, :o, :u)
         ON CONFLICT(code) DO UPDATE SET title = excluded.title, body = excluded.body,
           action = excluded.action, mode = excluded.mode, times = excluded.times,
           signal = excluded.signal, lead_min = excluded.lead_min, enabled = excluded.enabled,
           sort = excluded.sort, updated_at = excluded.updated_at'
    );
    $stmt->execute([
        ':c' => $code,
        ':t' => trim((string) ($row['title'] ?? '')) ?: 'Напоминание',
        ':b' => trim((string) ($row['body'] ?? '')),
        ':a' => $action,
        ':m' => $mode,
        ':ti' => implode(',', notify_parse_times((string) ($row['times'] ?? ''))),
        ':s' => $signal,
        ':l' => max(0, min(180, (int) ($row['lead_min'] ?? NOTIFY_LEAD_MIN))),
        // A row that says nothing about `enabled` is on: the seeds and any
        // caller that only edits text must not silently switch it off.
        ':e' => array_key_exists('enabled', $row) ? (!empty($row['enabled']) ? 1 : 0) : 1,
        ':o' => (int) ($row['sort'] ?? 0),
        ':u' => gmdate('Y-m-d\TH:i:s\Z'),
    ]);
}

function notify_delete_template(PDO $pdo, string $code): void {
    $pdo->prepare('DELETE FROM notification_templates WHERE code = :c')->execute([':c' => $code]);
    $pdo->prepare('DELETE FROM notification_prefs WHERE code = :c')->execute([':c' => $code]);
}

/**
 * Lowercase latin slug — the code travels in push payloads and URLs, so a
 * Russian name typed in the admin panel is transliterated rather than dropped.
 */
function notify_slug(string $raw): string {
    static $translit = [
        'а' => 'a', 'б' => 'b', 'в' => 'v', 'г' => 'g', 'д' => 'd', 'е' => 'e', 'ё' => 'e',
        'ж' => 'zh', 'з' => 'z', 'и' => 'i', 'й' => 'i', 'к' => 'k', 'л' => 'l', 'м' => 'm',
        'н' => 'n', 'о' => 'o', 'п' => 'p', 'р' => 'r', 'с' => 's', 'т' => 't', 'у' => 'u',
        'ф' => 'f', 'х' => 'h', 'ц' => 'c', 'ч' => 'ch', 'ш' => 'sh', 'щ' => 'sch', 'ъ' => '',
        'ы' => 'y', 'ь' => '', 'э' => 'e', 'ю' => 'yu', 'я' => 'ya',
    ];
    $trimmed = trim($raw);
    $lower = function_exists('mb_strtolower') ? mb_strtolower($trimmed, 'UTF-8') : strtolower($trimmed);
    $slug = preg_replace('/[^a-z0-9_-]+/', '-', strtr($lower, $translit));
    return trim((string) $slug, '-');
}

/** «8:00, 13:30, чушь, 25:00» -> ['08:00','13:30']. */
function notify_parse_times(string $raw): array {
    $out = [];
    foreach (preg_split('/[,;]/', $raw) ?: [] as $chunk) {
        $minute = notify_parse_time($chunk);
        if ($minute === null) continue;
        $label = notify_format_minute($minute);
        if (!in_array($label, $out, true)) $out[] = $label;
    }
    return $out;
}

function notify_parse_time(string $raw): ?int {
    $text = str_replace(['.', '-'], ':', trim($raw));
    if ($text === '') return null;
    $parts = explode(':', $text);
    if (!ctype_digit($parts[0])) return null;
    $hour = (int) $parts[0];
    $minute = isset($parts[1]) && ctype_digit($parts[1]) ? (int) $parts[1] : 0;
    if ($hour < 0 || $hour > 23 || $minute < 0 || $minute > 59) return null;
    return $hour * 60 + $minute;
}

function notify_format_minute(int $minute): string {
    $minute = (($minute % 1440) + 1440) % 1440;
    return sprintf('%02d:%02d', intdiv($minute, 60), $minute % 60);
}

function notify_prefs(PDO $pdo, string $clientId): array {
    $stmt = $pdo->prepare('SELECT * FROM notification_prefs WHERE client_id = :c');
    $stmt->execute([':c' => $clientId]);
    $out = [];
    foreach ($stmt->fetchAll(PDO::FETCH_ASSOC) ?: [] as $row) $out[$row['code']] = $row;
    return $out;
}

function notify_save_pref(PDO $pdo, string $clientId, string $code, string $mode, array $times, int $tzOffset): void {
    if (!in_array($mode, NOTIFY_USER_MODES, true)) $mode = 'default';
    $stmt = $pdo->prepare(
        'INSERT INTO notification_prefs(client_id, code, mode, times, tz_offset, updated_at)
         VALUES (:c, :k, :m, :t, :z, :u)
         ON CONFLICT(client_id, code) DO UPDATE SET mode = excluded.mode, times = excluded.times,
           tz_offset = excluded.tz_offset, updated_at = excluded.updated_at'
    );
    $stmt->execute([
        ':c' => $clientId,
        ':k' => notify_slug($code),
        ':m' => $mode,
        ':t' => implode(',', notify_parse_times(implode(',', $times))),
        ':z' => max(-840, min(840, $tzOffset)),
        ':u' => gmdate('Y-m-d\TH:i:s\Z'),
    ]);
}

/**
 * Admin template first, the person's own choice on top of it.
 * Returns ['mode' => …, 'times' => [...]]. A template with enabled=0 is off for
 * everyone: that is the operator removing it, not a default to override.
 */
function notify_resolve(array $template, ?array $pref): array {
    if (empty($template['enabled'])) return ['mode' => 'off', 'times' => []];
    $templateTimes = notify_parse_times((string) ($template['times'] ?? ''));
    $mode = $pref['mode'] ?? 'default';
    if (!in_array($mode, NOTIFY_USER_MODES, true)) $mode = 'default';
    if ($mode === 'off') return ['mode' => 'off', 'times' => []];
    if ($mode === 'default') {
        $tmode = in_array($template['mode'] ?? '', NOTIFY_MODES, true) ? $template['mode'] : 'fixed';
        if ($tmode === 'off') return ['mode' => 'off', 'times' => []];
        return ['mode' => $tmode, 'times' => $templateTimes];
    }
    // fixed/smart: the device sends the times it wants (for «smart» — the ones
    // it computed from its own records); an empty list falls back to the admin's.
    $own = notify_parse_times((string) ($pref['times'] ?? ''));
    return ['mode' => $mode, 'times' => $own ?: $templateTimes];
}

/** Slots of this schedule the current tick should deliver. */
function notify_due_slots(array $schedule, int $localMinute, int $windowMin = NOTIFY_SEND_WINDOW_MIN): array {
    if (($schedule['mode'] ?? 'off') === 'off') return [];
    $due = [];
    foreach ($schedule['times'] as $label) {
        $slot = notify_parse_time($label);
        if ($slot === null) continue;
        $delta = (($localMinute - $slot) % 1440 + 1440) % 1440;
        if ($delta < $windowMin) $due[] = notify_format_minute($slot);
    }
    return $due;
}

/**
 * One cron tick: every subscription with reminders on, every enabled template.
 * The `notification_sends` row goes in **before** the push leaves — a dropped
 * connection must not turn into a second notification on the next tick.
 */
function notify_dispatch(PDO $pdo, ?int $nowTs = null): array {
    $nowTs = $nowTs ?? time();
    $templates = notify_templates($pdo, true);
    $subs = $pdo->query('SELECT * FROM push_subscriptions WHERE reminders_enabled = 1')->fetchAll(PDO::FETCH_ASSOC) ?: [];
    $result = ['sent' => 0, 'skipped' => 0, 'failed' => 0, 'gone' => 0, 'subscriptions' => count($subs)];
    if (!$templates || !$subs) return $result;

    $vapid = webpush_vapid($pdo);
    foreach ($subs as $sub) {
        $prefs = notify_prefs($pdo, (string) $sub['client_id']);
        $offset = (int) ($prefs ? (reset($prefs)['tz_offset'] ?? 0) : 0);
        if (isset($sub['tz_offset']) && $sub['tz_offset'] !== null && $sub['tz_offset'] !== '') {
            $offset = (int) $sub['tz_offset'];
        }
        $localTs = $nowTs + $offset * 60;
        $localMinute = (int) gmdate('G', $localTs) * 60 + (int) gmdate('i', $localTs);
        $localDate = gmdate('Y-m-d', $localTs);

        foreach ($templates as $template) {
            $schedule = notify_resolve($template, $prefs[$template['code']] ?? null);
            foreach (notify_due_slots($schedule, $localMinute) as $slot) {
                $sendId = notify_claim($pdo, (string) $sub['client_id'], (string) $template['code'], $slot, $localDate);
                if ($sendId === null) { $result['skipped']++; continue; }
                $outcome = notify_push($pdo, $sub, $template, $vapid, $sendId);
                $result[$outcome]++;
            }
        }
    }
    return $result;
}

/** Reserve (client, code, slot, local date). null = already delivered today. */
function notify_claim(PDO $pdo, string $clientId, string $code, string $slot, string $localDate): ?int {
    try {
        $stmt = $pdo->prepare(
            'INSERT INTO notification_sends(client_id, code, slot, sent_on, status, ts)
             VALUES (:c, :k, :s, :d, 0, :t)'
        );
        $stmt->execute([
            ':c' => $clientId, ':k' => $code, ':s' => $slot, ':d' => $localDate,
            ':t' => gmdate('Y-m-d\TH:i:s\Z'),
        ]);
        return (int) $pdo->lastInsertId();
    } catch (PDOException $e) {
        return null;   // UNIQUE violation — this slot already went out today
    }
}

/** Send one template to one subscription. Returns 'sent'|'gone'|'failed'. */
function notify_push(PDO $pdo, array $sub, array $template, array $vapid, ?int $sendId = null): string {
    $payload = json_encode([
        'title' => (string) $template['title'],
        'body' => (string) $template['body'],
        'code' => (string) $template['code'],
        'action' => (string) $template['action'],
    ], JSON_UNESCAPED_UNICODE);
    try {
        $res = webpush_send($sub, $payload, $vapid);
    } catch (Throwable $e) {
        return 'failed';
    }
    $status = (int) $res['status'];
    if ($sendId !== null) {
        $pdo->prepare('UPDATE notification_sends SET status = :s WHERE id = :id')
            ->execute([':s' => $status, ':id' => $sendId]);
    }
    $pdo->prepare('INSERT INTO telemetry_events(client_id, user_id, kind, payload, ts) VALUES (:c, :u, :k, :p, :t)')
        ->execute([
            ':c' => $sub['client_id'], ':u' => $sub['user_id'], ':k' => 'push_sent',
            ':p' => json_encode(['code' => $template['code'], 'status' => $status]),
            ':t' => gmdate('Y-m-d\TH:i:s\Z'),
        ]);
    if ($status >= 200 && $status < 300) return 'sent';
    if (in_array($status, [404, 410], true)) {
        $pdo->prepare('DELETE FROM push_subscriptions WHERE id = :id')->execute([':id' => $sub['id']]);
        return 'gone';
    }
    return 'failed';
}

/** «Send now» from the admin panel — one template to everyone, off-schedule. */
function notify_send_now(PDO $pdo, string $code): array {
    $template = notify_template($pdo, $code);
    if ($template === null) return ['error' => 'нет такого уведомления'];
    $vapid = webpush_vapid($pdo);
    $subs = $pdo->query('SELECT * FROM push_subscriptions WHERE reminders_enabled = 1')->fetchAll(PDO::FETCH_ASSOC) ?: [];
    $out = ['sent' => 0, 'gone' => 0, 'failed' => 0, 'total' => count($subs)];
    foreach ($subs as $sub) $out[notify_push($pdo, $sub, $template, $vapid)]++;
    return $out;
}
