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

// Model / provider settings. Selects are filled from the live catalogue
// (ModelCatalog), so the operator picks a model instead of typing a slug —
// same approach as setup.php in site_yacloud_openrouter.
$LLM_SELECT_KEYS = [
    'LLM_PROVIDER', 'LLM_PROVIDER_PRIORITY', 'LLM_DEFAULT_MODEL',
    'LLM_FALLBACK_MODE', 'LLM_VISION_MODEL',
];
$LLM_TEXT_FIELDS = [
    'LLM_FALLBACK_MODELS' => 'Запасные модели (короткие id через запятую, пробуются после выбранной)',
    'MODEL_CATALOG_TTL_MIN' => 'Срок годности кэша каталога моделей, мин',
    'YANDEX_FOLDER_ID' => 'Yandex folder id',
    'ADMIN_EMAIL' => 'Email администратора (VAPID subject, уведомления)',
];
// Keys: an empty field keeps the stored value instead of wiping it.
$LLM_SECRET_FIELDS = [
    'OPENROUTER_API_KEY' => 'OpenRouter API key',
    'YANDEX_API_KEY' => 'Yandex API key',
];

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    $action = $_POST['action'] ?? '';

    if (isset($_POST['model_catalog'])) {
        // Keys just typed into the form are saved first — otherwise the
        // catalogue would be pulled with the previous credentials.
        foreach (array_keys($LLM_SECRET_FIELDS) as $key) {
            $value = trim((string) ($_POST[$key] ?? ''));
            if ($value !== '') $store->setSetting($key, $value);
        }
        if ($_POST['model_catalog'] === 'forget') {
            ModelCatalog::forget($store);
            $notice = 'Живой каталог забыт — в списках остались вшитые модели';
        } else {
            try {
                $report = ModelCatalog::refresh(require WEB_ROOT . '/lib/vendor/config.php', $store);
                $notice = 'Каталог обновлён: ' . (int) $report['rows'] . ' моделей'
                    . ' (OpenRouter: ' . (is_int($report['openrouter']) ? $report['openrouter'] : '⛔ ' . $report['openrouter'])
                    . ', Yandex: ' . (is_int($report['yandex']) ? $report['yandex'] : '⛔ ' . $report['yandex']) . ')';
            } catch (Throwable $e) {
                $notice = 'Каталог моделей не получен: ' . $e->getMessage();
            }
        }
    } elseif ($action === 'save_llm') {
        foreach (array_merge($LLM_SELECT_KEYS, array_keys($LLM_TEXT_FIELDS)) as $key) {
            if (isset($_POST[$key])) $store->setSetting($key, trim((string) $_POST[$key]));
        }
        foreach (array_keys($LLM_SECRET_FIELDS) as $key) {
            $value = trim((string) ($_POST[$key] ?? ''));
            if ($value !== '') $store->setSetting($key, $value);
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

// The catalogue refreshes itself when this page is opened and the cache is
// older than MODEL_CATALOG_TTL_MIN; a network failure only leaves a reason
// behind, the previous list keeps working.
ModelCatalog::maybeRefresh(require WEB_ROOT . '/lib/vendor/config.php', $store);
$cfg = require WEB_ROOT . '/lib/vendor/config.php';

$llmValues = $store->allSettings();
$webConfig = web_config_all($pdo);

/** Setting → config → '' : what the LLM layer actually uses right now. */
$eff = static function (string $key) use ($llmValues, $cfg): string {
    $value = $llmValues[$key] ?? ($cfg[$key] ?? '');
    return is_scalar($value) ? (string) $value : '';
};

/** Catalogue rows grouped for <optgroup>, OCR-only models left out. */
$modelGroups = [];
foreach ((array) ($cfg['AVAILABLE_MODELS'] ?? []) as $model) {
    if (empty($model['ocr_only'])) $modelGroups[(string) ($model['group'] ?? 'Модели')][] = $model;
}
/** Price hint: RUB per 1k for hardcoded rows, USD per 1M for live ones. */
$modelPrice = static function (array $m): string {
    $num = static fn (float $v): string => rtrim(rtrim(number_format($v, 2, '.', ''), '0'), '.');
    $in = (float) ($m['price_in'] ?? 0);
    $out = (float) ($m['price_out'] ?? 0);
    if ($in > 0 || $out > 0) return sprintf(' · ~%s/%s ₽ за 1k', $num($in), $num($out));
    $usdIn = (float) ($m['price_usd_in'] ?? 0);
    $usdOut = (float) ($m['price_usd_out'] ?? 0);
    if ($usdIn > 0 || $usdOut > 0) return sprintf(' · $%s/$%s за 1M', $num($usdIn), $num($usdOut));
    return !empty($m['free']) ? ' · бесплатно' : '';
};
// A value saved earlier can be missing from the catalogue (forgotten cache,
// model withdrawn). It stays in the list as its own option — otherwise the
// browser would silently pick the first one and saving would swap the model.
$modelIds = [];
$visionIds = [];
foreach ($modelGroups as $groupRows) {
    foreach ($groupRows as $m) {
        $modelIds[] = (string) ($m['id'] ?? '');
        if (($m['provider'] ?? '') === 'openrouter') $visionIds[] = (string) ($m['full_id'] ?? '');
    }
}

$liveRows = ModelCatalog::decode((string) ($cfg['MODEL_CATALOG_MODELS'] ?? ''));
$liveSynced = (string) ($cfg['MODEL_CATALOG_SYNCED_AT'] ?? '');
$liveError = (string) ($cfg['MODEL_CATALOG_ERROR'] ?? '');
$liveTtl = (int) ($cfg['MODEL_CATALOG_TTL_MIN'] ?? ModelCatalog::TTL_MIN);

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
    .field input, .field select { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--fg); font-size: 14px; }
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

        <div class="field">
            <label>Провайдер по умолчанию</label>
            <select name="LLM_PROVIDER">
                <option value="openrouter" <?= $eff('LLM_PROVIDER') === 'openrouter' ? 'selected' : '' ?>>openrouter</option>
                <option value="yandex" <?= $eff('LLM_PROVIDER') === 'yandex' ? 'selected' : '' ?>>yandex</option>
            </select>
        </div>
        <div class="field">
            <label>Приоритет провайдеров (второй пробуется, когда первый не ответил)</label>
            <select name="LLM_PROVIDER_PRIORITY">
                <option value="openrouter,yandex" <?= $eff('LLM_PROVIDER_PRIORITY') !== 'yandex,openrouter' ? 'selected' : '' ?>>openrouter → yandex</option>
                <option value="yandex,openrouter" <?= $eff('LLM_PROVIDER_PRIORITY') === 'yandex,openrouter' ? 'selected' : '' ?>>yandex → openrouter</option>
            </select>
        </div>
        <div class="field">
            <label>Модель по умолчанию — ею же распознаётся фото еды (каталог: вшитый список + то, что отдал провайдер)</label>
            <select name="LLM_DEFAULT_MODEL">
                <?php $modelCurrent = $eff('LLM_DEFAULT_MODEL'); ?>
                <?php if ($modelCurrent !== '' && !in_array($modelCurrent, $modelIds, true)): ?>
                <option value="<?= htmlspecialchars($modelCurrent) ?>" selected><?= htmlspecialchars($modelCurrent) ?> — нет в каталоге</option>
                <?php endif; ?>
                <?php foreach ($modelGroups as $groupName => $groupRows): ?>
                <optgroup label="<?= htmlspecialchars((string) $groupName) ?>">
                    <?php foreach ($groupRows as $m): ?>
                    <option value="<?= htmlspecialchars((string) $m['id']) ?>" <?= $modelCurrent === (string) $m['id'] ? 'selected' : '' ?>>
                        <?= htmlspecialchars((string) $m['label']) ?> — <?= htmlspecialchars((string) $m['provider']) ?>/<?= htmlspecialchars((string) $m['full_id']) ?><?= htmlspecialchars($modelPrice($m)) ?>
                    </option>
                    <?php endforeach; ?>
                </optgroup>
                <?php endforeach; ?>
            </select>
        </div>
        <div class="field">
            <label>Vision-модель для PDF/OCR (OpenRouter full_id)</label>
            <select name="LLM_VISION_MODEL">
                <?php $visionCurrent = $eff('LLM_VISION_MODEL'); ?>
                <?php if ($visionCurrent !== '' && !in_array($visionCurrent, $visionIds, true)): ?>
                <option value="<?= htmlspecialchars($visionCurrent) ?>" selected><?= htmlspecialchars($visionCurrent) ?> — нет в каталоге</option>
                <?php endif; ?>
                <?php foreach ($modelGroups as $groupName => $groupRows): ?>
                    <?php $orRows = array_filter($groupRows, static fn ($m) => ($m['provider'] ?? '') === 'openrouter'); ?>
                    <?php if (!$orRows) continue; ?>
                <optgroup label="<?= htmlspecialchars((string) $groupName) ?>">
                    <?php foreach ($orRows as $m): ?>
                    <option value="<?= htmlspecialchars((string) $m['full_id']) ?>" <?= $visionCurrent === (string) $m['full_id'] ? 'selected' : '' ?>>
                        <?= htmlspecialchars((string) $m['label']) ?> — <?= htmlspecialchars((string) $m['full_id']) ?><?= htmlspecialchars($modelPrice($m)) ?>
                    </option>
                    <?php endforeach; ?>
                </optgroup>
                <?php endforeach; ?>
            </select>
        </div>
        <div class="field">
            <label>Запасная модель: что пробовать после выбранной</label>
            <select name="LLM_FALLBACK_MODE">
                <option value="auto" <?= $eff('LLM_FALLBACK_MODE') !== 'manual' ? 'selected' : '' ?>>авто — более новая версия той же модели, затем список</option>
                <option value="manual" <?= $eff('LLM_FALLBACK_MODE') === 'manual' ? 'selected' : '' ?>>только список ниже</option>
            </select>
        </div>

        <p class="muted" style="font-size:13px;">
            Список тянется прямо у провайдеров (OpenRouter <code>GET /models</code>, Yandex <code>GET /v1/models</code>)
            и кэшируется в настройках. Обновляется сам при заходе на эту страницу, если кэш старше <?= $liveTtl ?> мин.
            <?php if ($liveRows): ?>
            Сейчас живых моделей: <b><?= count($liveRows) ?></b><?= $liveSynced !== '' ? ', обновлено ' . htmlspecialchars($liveSynced) : '' ?>.
            <?php else: ?>
            Живого каталога пока нет — в списках только вшитые модели.
            <?php endif; ?>
            <?php if ($liveError !== ''): ?>
            <br>Последняя попытка: <?= htmlspecialchars(mb_substr($liveError, 0, 200)) ?>
            <?php endif; ?>
        </p>

        <?php foreach ($LLM_TEXT_FIELDS as $key => $label): ?>
        <div class="field">
            <label><?= htmlspecialchars($label) ?></label>
            <input type="text" name="<?= $key ?>" value="<?= htmlspecialchars($eff($key)) ?>">
        </div>
        <?php endforeach; ?>
        <?php foreach ($LLM_SECRET_FIELDS as $key => $label): ?>
        <div class="field">
            <label><?= htmlspecialchars($label) ?><?= ($llmValues[$key] ?? '') !== '' ? ' — задан, пустое поле не сотрёт' : '' ?></label>
            <input type="password" name="<?= $key ?>" autocomplete="new-password" value="">
        </div>
        <?php endforeach; ?>

        <button class="btn btn-primary" type="submit">Сохранить</button>
        <button class="btn btn-secondary" type="submit" name="model_catalog" value="refresh" formnovalidate>Обновить каталог моделей</button>
        <?php if ($liveRows): ?>
        <button class="btn btn-secondary" type="submit" name="model_catalog" value="forget" formnovalidate>Забыть живой каталог</button>
        <?php endif; ?>
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
