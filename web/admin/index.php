<?php
/**
 * web/admin/index.php — config variables + usage statistics.
 * spec: spec/web.md § Админка.
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';
admin_require_login();

$pdo = web_db();
$store = new SettingsStore(WEB_DB_PATH);
$notice = null;

$LLM_FIELDS = [
    'LLM_PROVIDER' => 'Провайдер (openrouter | yandex)',
    'LLM_DEFAULT_MODEL' => 'Модель по умолчанию (short id)',
    'OPENROUTER_API_KEY' => 'OpenRouter API key',
    'LLM_VISION_MODEL' => 'Модель для фото (vision)',
    'YANDEX_API_KEY' => 'Yandex API key',
    'YANDEX_FOLDER_ID' => 'Yandex folder id',
    'ADMIN_EMAIL' => 'Email администратора (VAPID subject, уведомления)',
];

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    $action = $_POST['action'] ?? '';

    if ($action === 'save_llm') {
        foreach (array_keys($LLM_FIELDS) as $key) {
            if (isset($_POST[$key])) $store->setSetting($key, trim((string) $_POST[$key]));
        }
        $notice = 'Сохранено';
    } elseif ($action === 'set_cron_secret') {
        web_config_set($pdo, 'CRON_SECRET', web_random_token(24));
        $notice = 'Новый cron-ключ сгенерирован';
    } elseif ($action === 'regen_vapid') {
        $keys = webpush_generate_vapid_keypair();
        web_config_set($pdo, 'VAPID_PUBLIC_KEY', $keys['public']);
        web_config_set($pdo, 'VAPID_PRIVATE_KEY_PEM', $keys['private_pem']);
        $notice = 'VAPID-ключи перегенерированы — старые подписки на push перестанут работать';
    } elseif ($action === 'send_reminder_now') {
        try {
            $result = webpush_send_reminders($pdo, 'Пора измерить сахар', 'Загляните в приложение и отметьте показатель.');
            $notice = "Отправлено: {$result['sent']}, отписалось: {$result['gone']}, ошибок: {$result['failed']} (всего {$result['total']})";
        } catch (Throwable $e) {
            $notice = 'Ошибка отправки: ' . $e->getMessage();
        }
    } elseif ($action === 'change_password') {
        $p1 = (string) ($_POST['password'] ?? '');
        $p2 = (string) ($_POST['password_confirm'] ?? '');
        if (strlen($p1) < 8) {
            $notice = 'Пароль должен быть не короче 8 символов';
        } elseif ($p1 !== $p2) {
            $notice = 'Пароли не совпадают';
        } else {
            web_config_set($pdo, 'ADMIN_PASSWORD_HASH', password_hash($p1, PASSWORD_DEFAULT));
            $notice = 'Пароль изменён';
        }
    }
}

$llmValues = $store->allSettings();
$webConfig = web_config_all($pdo);

$sinceWeek = gmdate('Y-m-d\TH:i:s\Z', time() - 7 * 86400);

$totalVisitors = (int) $pdo->query("SELECT COUNT(DISTINCT client_id) FROM telemetry_events")->fetchColumn();
$stmt = $pdo->prepare("SELECT COUNT(DISTINCT client_id) FROM telemetry_events WHERE ts >= :t");
$stmt->execute([':t' => $sinceWeek]);
$weekVisitors = (int) $stmt->fetchColumn();

$utmRows = $pdo->query(
    "SELECT COALESCE(utm_source,'(без метки)') AS src, COUNT(DISTINCT client_id) AS n
     FROM telemetry_events WHERE kind = 'first_visit' GROUP BY src ORDER BY n DESC LIMIT 10"
)->fetchAll(PDO::FETCH_ASSOC);

$kindRows = $pdo->query(
    "SELECT kind, COUNT(*) AS n FROM telemetry_events GROUP BY kind ORDER BY n DESC"
)->fetchAll(PDO::FETCH_ASSOC);

$usersCount = (int) $pdo->query('SELECT COUNT(*) FROM users')->fetchColumn();
$pushSubsCount = (int) $pdo->query('SELECT COUNT(*) FROM push_subscriptions')->fetchColumn();
$pushEnabledCount = (int) $pdo->query('SELECT COUNT(*) FROM push_subscriptions WHERE reminders_enabled = 1')->fetchColumn();
?>
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CGM-diet — админка</title>
<link rel="stylesheet" href="../css/tokens.css">
<style>
    .wrap { max-width: 720px; margin: 0 auto; padding: 16px; }
    .row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
    .card { margin-bottom: 20px; }
    .field { margin-bottom: 10px; }
    .field label { display: block; font-size: 13px; color: var(--fg-muted); margin-bottom: 4px; }
    .field input { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--fg); font-size: 14px; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    td, th { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; }
    .notice { background: var(--accent); color: var(--accent-fg); padding: 10px 14px; border-radius: 10px; margin-bottom: 16px; }
    .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-bottom: 16px; }
    .stat { text-align: center; }
    .stat .n { font-size: 24px; font-weight: 700; }
    .stat .l { font-size: 12px; color: var(--fg-muted); }
    code { background: var(--bg); padding: 2px 6px; border-radius: 6px; word-break: break-all; }
</style>
</head>
<body>
<div class="wrap">
    <div class="row">
        <h1 style="font-size:20px;">CGM-diet — админка</h1>
        <a href="logout.php" class="btn btn-secondary">Выйти</a>
    </div>

    <?php if ($notice): ?><div class="notice"><?= htmlspecialchars($notice) ?></div><?php endif; ?>

    <div class="card">
        <h2 style="margin-top:0;">Статистика</h2>
        <div class="stat-grid">
            <div class="stat"><div class="n"><?= $totalVisitors ?></div><div class="l">посетителей всего</div></div>
            <div class="stat"><div class="n"><?= $weekVisitors ?></div><div class="l">за 7 дней</div></div>
            <div class="stat"><div class="n"><?= $usersCount ?></div><div class="l">зарегистрировано</div></div>
            <div class="stat"><div class="n"><?= $pushEnabledCount ?>/<?= $pushSubsCount ?></div><div class="l">подписок на push</div></div>
        </div>
        <table>
            <tr><th>UTM-источник</th><th>Посетителей</th></tr>
            <?php foreach ($utmRows as $r): ?>
            <tr><td><?= htmlspecialchars($r['src']) ?></td><td><?= (int) $r['n'] ?></td></tr>
            <?php endforeach; ?>
        </table>
        <table style="margin-top:12px;">
            <tr><th>Событие</th><th>Раз</th></tr>
            <?php foreach ($kindRows as $r): ?>
            <tr><td><?= htmlspecialchars($r['kind']) ?></td><td><?= (int) $r['n'] ?></td></tr>
            <?php endforeach; ?>
        </table>
    </div>

    <form method="post" class="card">
        <h2 style="margin-top:0;">Модель / LLM</h2>
        <input type="hidden" name="action" value="save_llm">
        <?php foreach ($LLM_FIELDS as $key => $label): ?>
        <div class="field">
            <label><?= htmlspecialchars($label) ?></label>
            <input type="text" name="<?= $key ?>" value="<?= htmlspecialchars($llmValues[$key] ?? '') ?>">
        </div>
        <?php endforeach; ?>
        <button class="btn btn-primary" type="submit">Сохранить</button>
    </form>

    <div class="card">
        <h2 style="margin-top:0;">Push-уведомления</h2>
        <p class="muted">VAPID-ключ: <code><?= htmlspecialchars($webConfig['VAPID_PUBLIC_KEY'] ?? '— ещё не сгенерирован —') ?></code></p>
        <p class="muted">Cron-ключ (для <code>/api/push_send.php?key=...</code>): <code><?= htmlspecialchars($webConfig['CRON_SECRET'] ?? '— не задан —') ?></code></p>
        <form method="post" style="display:inline;"><input type="hidden" name="action" value="set_cron_secret"><button class="btn btn-secondary" type="submit">Сгенерировать cron-ключ</button></form>
        <form method="post" style="display:inline;"><input type="hidden" name="action" value="regen_vapid"><button class="btn btn-secondary" type="submit">Перегенерировать VAPID</button></form>
        <form method="post" style="display:inline;"><input type="hidden" name="action" value="send_reminder_now"><button class="btn btn-primary" type="submit">Отправить напоминание сейчас</button></form>
    </div>

    <form method="post" class="card">
        <h2 style="margin-top:0;">Пароль администратора</h2>
        <input type="hidden" name="action" value="change_password">
        <div class="field"><label>Новый пароль</label><input type="password" name="password"></div>
        <div class="field"><label>Повторите</label><input type="password" name="password_confirm"></div>
        <button class="btn btn-secondary" type="submit">Изменить пароль</button>
    </form>
</div>
</body>
</html>
