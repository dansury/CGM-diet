<?php
/**
 * php web/tests/notifications.php — schedule resolution and dispatch
 * bookkeeping on a throwaway SQLite file. No network: the push itself is the
 * one part that needs a real browser (web/lib/webpush.php header).
 * spec: spec/notifications.md.
 */

declare(strict_types=1);

require __DIR__ . '/../lib/db.php';

$failures = 0;
function check(string $what, $got, $want): void {
    global $failures;
    $ok = $got === $want;
    if (!$ok) $failures++;
    echo ($ok ? "ok   " : "FAIL ") . $what;
    if (!$ok) echo "\n       получено: " . json_encode($got, JSON_UNESCAPED_UNICODE)
        . "\n       ожидалось: " . json_encode($want, JSON_UNESCAPED_UNICODE);
    echo "\n";
}

$dbPath = sys_get_temp_dir() . '/cgm-notify-test-' . getmypid() . '.db';
@unlink($dbPath);
$pdo = web_db_connect($dbPath);

// ── parsing ──────────────────────────────────────────────────────────────
check('время читается из любой записи', notify_parse_times('8:00, 13.30; 19-05'), ['08:00', '13:30', '19:05']);
check('мусор и 25:00 отбрасываются', notify_parse_times('25:00, чушь, 07:60, 9'), ['09:00']);
check('дубли схлопываются', notify_parse_times('9:00, 09:00'), ['09:00']);

// ── seeds ────────────────────────────────────────────────────────────────
$templates = notify_templates($pdo);
check('на пустой базе заведены два уведомления', count($templates), 2);
check('первое — про еду', $templates[0]['code'], 'meal');

// ── resolution: admin default, then the person's own choice ──────────────
$meal = notify_template($pdo, 'meal');
check('без настроек человека действует шаблон',
    notify_resolve($meal, null), ['mode' => 'fixed', 'times' => ['08:30', '13:30', '19:00']]);
check('mode=default — тоже шаблон',
    notify_resolve($meal, ['mode' => 'default', 'times' => '07:00']), ['mode' => 'fixed', 'times' => ['08:30', '13:30', '19:00']]);
check('своё время перекрывает шаблон',
    notify_resolve($meal, ['mode' => 'fixed', 'times' => '07:00,12:00']), ['mode' => 'fixed', 'times' => ['07:00', '12:00']]);
check('пустое своё время откатывается к шаблону',
    notify_resolve($meal, ['mode' => 'fixed', 'times' => '']), ['mode' => 'fixed', 'times' => ['08:30', '13:30', '19:00']]);
check('умное время приходит с устройства',
    notify_resolve($meal, ['mode' => 'smart', 'times' => '08:15,12:45']), ['mode' => 'smart', 'times' => ['08:15', '12:45']]);
check('человек выключил — выключено',
    notify_resolve($meal, ['mode' => 'off', 'times' => '07:00']), ['mode' => 'off', 'times' => []]);

$off = $meal;
$off['enabled'] = 0;
check('выключенный админом шаблон не включается настройкой человека',
    notify_resolve($off, ['mode' => 'fixed', 'times' => '07:00']), ['mode' => 'off', 'times' => []]);

// ── due slots ────────────────────────────────────────────────────────────
$schedule = ['mode' => 'fixed', 'times' => ['08:30', '13:30']];
check('слот наступил ровно в своё время', notify_due_slots($schedule, 8 * 60 + 30), ['08:30']);
check('редкий cron через 20 минут ещё успевает', notify_due_slots($schedule, 8 * 60 + 50), ['08:30']);
check('через 40 минут слот уже упущен', notify_due_slots($schedule, 9 * 60 + 10), []);
check('выключенное не наступает никогда', notify_due_slots(['mode' => 'off', 'times' => ['08:30']], 8 * 60 + 30), []);
check('слот перед полуночью не залипает утром',
    notify_due_slots(['mode' => 'fixed', 'times' => ['23:50']], 10), ['23:50']);

// ── one notification per slot per local day ──────────────────────────────
$first = notify_claim($pdo, 'client-1', 'meal', '08:30', '2026-09-12');
$second = notify_claim($pdo, 'client-1', 'meal', '08:30', '2026-09-12');
check('первый тик занимает слот', is_int($first), true);
check('второй тик того же дня — нет', $second, null);
check('назавтра слот снова свободен', is_int(notify_claim($pdo, 'client-1', 'meal', '08:30', '2026-09-13')), true);
check('другому человеку слот не занят', is_int(notify_claim($pdo, 'client-2', 'meal', '08:30', '2026-09-12')), true);

// ── prefs round-trip ─────────────────────────────────────────────────────
notify_save_pref($pdo, 'client-1', 'meal', 'smart', ['07:45', '12:20'], 180);
$prefs = notify_prefs($pdo, 'client-1');
check('настройка человека сохранилась', $prefs['meal']['times'], '07:45,12:20');
check('часовой пояс сохранился', (int) $prefs['meal']['tz_offset'], 180);
notify_save_pref($pdo, 'client-1', 'meal', 'нет-такого-режима', [], 180);
check('неизвестный режим падает в default', notify_prefs($pdo, 'client-1')['meal']['mode'], 'default');

// ── admin editing ────────────────────────────────────────────────────────
notify_save_template($pdo, [
    'code' => 'Вода!', 'title' => 'Вода', 'body' => 'Запишите воду',
    'action' => 'бред', 'mode' => 'бред', 'times' => '11:00', 'signal' => 'meal,бред',
    'lead_min' => 999, 'enabled' => 1,
]);
$water = notify_template($pdo, notify_slug('Вода!'));
check('код приводится к слагу', $water !== null, true);
check('неизвестное действие — камера', $water['action'], 'camera');
check('неизвестный режим — по времени', $water['mode'], 'fixed');
check('несуществующий сигнал отброшен', $water['signal'], 'meal');
check('упреждение ограничено', (int) $water['lead_min'], 180);

notify_delete_template($pdo, 'meal');
check('удаление шаблона убирает и настройки людей', isset(notify_prefs($pdo, 'client-1')['meal']), false);

// ── dispatch with no subscriptions must not throw ────────────────────────
$result = notify_dispatch($pdo);
check('тик без подписчиков ничего не шлёт', $result['sent'], 0);

@unlink($dbPath);
@unlink($dbPath . '-wal');
@unlink($dbPath . '-shm');

echo $failures === 0 ? "\nвсе проверки прошли\n" : "\nпровалов: $failures\n";
exit($failures === 0 ? 0 : 1);
