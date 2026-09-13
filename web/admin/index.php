<?php
/**
 * web/admin/index.php — config variables, notifications, usage statistics.
 * spec: spec/web.md § Админка, spec/notifications.md § Админка.
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
    'LLM_FALLBACK_MODE',
];
// Written even when empty: «— не задана —» has to be able to clear the value.
$LLM_CLEARABLE_KEYS = ['LLM_VISION_MODEL', 'LLM_FALLBACK_MODEL', 'YANDEX_FALLBACK_MODEL'];
$LLM_TEXT_FIELDS = [
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
        foreach (array_merge($LLM_SELECT_KEYS, $LLM_CLEARABLE_KEYS, array_keys($LLM_TEXT_FIELDS)) as $key) {
            if (isset($_POST[$key])) $store->setSetting($key, trim((string) $_POST[$key]));
        }
        // Backup models: three ordered dropdowns → one comma-separated setting.
        if (isset($_POST['LLM_FALLBACK_MODEL_1'])) {
            $picked = [];
            for ($i = 1; $i <= 3; $i++) {
                $v = trim((string) ($_POST['LLM_FALLBACK_MODEL_' . $i] ?? ''));
                if ($v !== '' && !in_array($v, $picked, true)) $picked[] = $v;
            }
            $store->setSetting('LLM_FALLBACK_MODELS', implode(',', $picked));
        }
        foreach (array_keys($LLM_SECRET_FIELDS) as $key) {
            $value = trim((string) ($_POST[$key] ?? ''));
            if ($value !== '') $store->setSetting($key, $value);
        }
        DiagLog::info('admin', 'Настройки модели сохранены');
        $notice = 'Сохранено';
    } elseif ($action === 'save_autopull') {
        // Галочка и оба её параметра пишутся всегда — иначе «выключить» и
        // «очистить адрес» не сработали бы.
        $store->setSetting('AUTOPULL_ENABLED', isset($_POST['AUTOPULL_ENABLED']) ? '1' : '0');
        foreach (['AUTOPULL_INTERVAL', 'AUTOPULL_URL'] as $key) {
            if (isset($_POST[$key])) $store->setSetting($key, trim((string) $_POST[$key]));
        }
        DiagLog::info('admin', 'Автообновление кода: ' . (isset($_POST['AUTOPULL_ENABLED']) ? 'включено' : 'выключено'));
        $notice = 'Сохранено';
    } elseif ($action === 'autopull_check') {
        $report = AutoPull::check(AutoPull::options(require WEB_ROOT . '/lib/vendor/config.php',
            ['state_dir' => WEB_DATA_DIR]), true);
        $notice = $report['ok']
            ? 'Автообновление: ' . $report['note'] . ' (head ' . substr($report['head'], 0, 7) . ')'
            : 'Автообновление: ' . $report['error'];
        DiagLog::info('admin', $notice);
    } elseif ($action === 'llm_probe') {
        // One real completion per configured leg — a wrong key or a model the
        // cloud folder does not serve is named here instead of surfacing hours
        // later as «не получилось распознать».
        $probeCfg = require WEB_ROOT . '/lib/vendor/config.php';
        LLM::init($probeCfg, DiagLog::store());
        $probeModels = (array) ($probeCfg['AVAILABLE_MODELS'] ?? []);
        $probeLive = model_live_providers($probeModels);
        $lines = [];
        foreach (LLM::probe() as $leg) {
            // «Failed to get model» reads as a provider outage; say when it is
            // just a model the folder does not serve.
            $hint = empty($leg['ok']) ? model_probe_hint((string) $leg['model'], $probeModels, $probeLive) : '';
            $lines[] = ($leg['ok'] ? '✅ ' : '⛔ ') . $leg['leg'] . ' — ' . $leg['model'] . ': ' . $leg['text'] . $hint;
        }
        $notice = $lines ? implode("\n", $lines) : 'Проверять нечего: ни один провайдер не настроен';
    } elseif ($action === 'diag_clear') {
        DiagLog::clear();
        DiagLog::info('admin', 'Лог очищен вручную из админки');
        $notice = 'Лог очищен';
    } elseif ($action === 'set_cron_secret') {
        web_config_set($pdo, 'CRON_SECRET', web_random_token(24));
        $notice = 'Новый cron-ключ сгенерирован';
    } elseif ($action === 'regen_vapid') {
        $keys = webpush_generate_vapid_keypair();
        web_config_set($pdo, 'VAPID_PUBLIC_KEY', $keys['public']);
        web_config_set($pdo, 'VAPID_PRIVATE_KEY_PEM', $keys['private_pem']);
        $notice = 'VAPID-ключи перегенерированы — старые подписки на push перестанут работать';
    } elseif ($action === 'save_notification') {
        notify_save_template($pdo, [
            'code' => (string) ($_POST['code'] ?? ''),
            'title' => (string) ($_POST['title'] ?? ''),
            'body' => (string) ($_POST['body'] ?? ''),
            'action' => (string) ($_POST['notify_action'] ?? 'camera'),
            'mode' => (string) ($_POST['mode'] ?? 'fixed'),
            'times' => (string) ($_POST['times'] ?? ''),
            'signal' => implode(',', array_map('strval', (array) ($_POST['signal'] ?? []))),
            'lead_min' => (int) ($_POST['lead_min'] ?? 15),
            'enabled' => isset($_POST['enabled']) ? 1 : 0,
            'sort' => (int) ($_POST['sort'] ?? 0),
        ]);
        $notice = notify_slug((string) ($_POST['code'] ?? '')) === ''
            ? 'Код уведомления пустой или из одних небуквенных знаков — не сохранено'
            : 'Уведомление сохранено';
    } elseif ($action === 'delete_notification') {
        notify_delete_template($pdo, notify_slug((string) ($_POST['code'] ?? '')));
        $notice = 'Уведомление удалено вместе с настройками пользователей';
    } elseif ($action === 'send_notification_now') {
        $result = notify_send_now($pdo, notify_slug((string) ($_POST['code'] ?? '')));
        $notice = isset($result['error'])
            ? 'Ошибка: ' . $result['error']
            : "Отправлено: {$result['sent']}, отписалось: {$result['gone']}, ошибок: {$result['failed']} (всего {$result['total']})";
    } elseif ($action === 'dispatch_now') {
        $result = notify_dispatch($pdo);
        $notice = "Тик расписания: отправлено {$result['sent']}, пропущено (уже было сегодня) {$result['skipped']},"
            . " ошибок {$result['failed']}, подписок {$result['subscriptions']}";
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
/** Providers that answered GET /models — only their rows can be disproved. */
$liveProviders = model_live_providers((array) ($cfg['AVAILABLE_MODELS'] ?? []));
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
/**
 * One model <select> over the catalogue. $value says what a row is worth as a
 * stored setting — a short id for LLM_DEFAULT_MODEL, "provider:slug" wherever
 * the provider must travel with the slug.
 *
 * A value saved earlier can be missing from the catalogue (forgotten cache,
 * model withdrawn). It stays in the list as its own option — otherwise the
 * browser would silently pick the first one and saving would swap the model.
 */
$modelSelect = static function (string $name, string $current, callable $value, ?callable $filter = null, string $empty = '') use ($modelGroups, $modelPrice, $liveProviders): string {
    $esc = static fn (string $v): string => htmlspecialchars($v, ENT_QUOTES, 'UTF-8');
    $html = '<select name="' . $esc($name) . '">';
    if ($empty !== '') {
        $html .= '<option value=""' . ($current === '' ? ' selected' : '') . '>' . $esc($empty) . '</option>';
    }
    $known = false;
    $body = '';
    foreach ($modelGroups as $groupName => $groupRows) {
        $rows = $filter === null ? $groupRows : array_values(array_filter($groupRows, $filter));
        if (!$rows) continue;
        $body .= '<optgroup label="' . $esc((string) $groupName) . '">';
        foreach ($rows as $m) {
            $val = (string) $value($m);
            if ($val === $current) $known = true;
            $body .= '<option value="' . $esc($val) . '"' . ($val === $current ? ' selected' : '') . '>'
                . $esc(model_mark($m, $liveProviders))
                . $esc((string) $m['label']) . ' — ' . $esc((string) $m['provider']) . '/' . $esc((string) $m['full_id'])
                . $esc($modelPrice($m)) . '</option>';
        }
        $body .= '</optgroup>';
    }
    if ($current !== '' && !$known) {
        $html .= '<option value="' . $esc($current) . '" selected>' . $esc($current) . ' — нет в каталоге</option>';
    }
    return $html . $body . '</select>';
};
$byShortId = static fn (array $m): string => (string) $m['id'];
$byProviderSlug = static fn (array $m): string => (string) $m['provider'] . ':' . (string) $m['full_id'];
$bySlug = static fn (array $m): string => (string) $m['full_id'];
$onlyVision = static fn (array $m): bool => !empty($m['vision']);
$fallbackPicked = array_values(array_filter(array_map('trim', explode(',', $eff('LLM_FALLBACK_MODELS')))));
// A vision model stored as a bare slug (the config default) is displayed as the
// provider-qualified option it resolves to, not as «нет в каталоге».
$visionCurrent = $eff('LLM_VISION_MODEL');
if ($visionCurrent !== '' && strpos($visionCurrent, ':') === false) {
    $visionRow = LLM::resolveModelSpec($visionCurrent);
    if ($visionRow !== null) $visionCurrent = $visionRow['provider'] . ':' . $visionRow['full_id'];
}

// Picked models the provider answered for but did not list: every request to
// them comes back «Failed to get model», so the page says it before the probe.
$effPicks = [];
foreach (array_merge(array_keys(MODEL_PICK_FIELDS), ['LLM_FALLBACK_MODELS']) as $pickKey) {
    $effPicks[$pickKey] = $eff($pickKey);
}
$deadPicks = model_dead_picks($effPicks, (array) ($cfg['AVAILABLE_MODELS'] ?? []), $liveProviders);
// Что взять взамен — один раз на провайдера, а не построчно: для vision-поля
// список другой (только зрячие), поэтому ключ составной.
$deadSuggest = [];
foreach ($deadPicks as $pick) {
    $key = $pick['provider'] . ($pick['vision'] ? '|vision' : '|text');
    if (isset($deadSuggest[$key])) continue;
    $deadSuggest[$key] = [
        'provider' => $pick['provider'],
        'vision'   => $pick['vision'],
        'models'   => model_live_suggestions((array) ($cfg['AVAILABLE_MODELS'] ?? []), $pick['provider'], $pick['vision']),
    ];
}

$liveRows = ModelCatalog::decode((string) ($cfg['MODEL_CATALOG_MODELS'] ?? ''));
$liveSynced = (string) ($cfg['MODEL_CATALOG_SYNCED_AT'] ?? '');
$liveError = (string) ($cfg['MODEL_CATALOG_ERROR'] ?? '');
$liveTtl = (int) ($cfg['MODEL_CATALOG_TTL_MIN'] ?? ModelCatalog::TTL_MIN);

// ── Diagnostics + log ────────────────────────────────────────────────────
// The header makes a copied log self-contained: whoever reads it needs no
// access to this page to know which code, provider, models and keys were live.
$diagCounts = DiagLog::counts();
$diagHeader = [
    'приложение' => 'CGM-diet web (' . ($_SERVER['HTTP_HOST'] ?? 'хост неизвестен') . ')',
    'php'        => PHP_VERSION . ' на ' . PHP_OS,
    'база'       => WEB_DB_PATH . (WEB_DATA_PERSISTENT
        ? ' (вне каталога деплоя — настройки переживают обновление)'
        : ' (ВНУТРИ каталога деплоя — настройки сотрутся при следующей заливке)'),
    'код'        => (string) (DiagLog::state('code_stamp') ?? '—')
        . ', задеплоен ' . (string) (DiagLog::state('deployed_at') ?? '—'),
    'ключи'      => 'OpenRouter: ' . ($eff('OPENROUTER_API_KEY') !== '' ? DiagLog::maskKey($eff('OPENROUTER_API_KEY')) : 'НЕ ЗАДАН')
        . '; Yandex: ' . ($eff('YANDEX_API_KEY') !== '' ? DiagLog::maskKey($eff('YANDEX_API_KEY')) : 'НЕ ЗАДАН')
        . '; folder: ' . ($eff('YANDEX_FOLDER_ID') !== '' ? $eff('YANDEX_FOLDER_ID') : 'НЕ ЗАДАН'),
];
foreach (LLM::configSummary() as $k => $v) {
    $diagHeader['llm.' . $k] = is_scalar($v) ? (string) $v : (string) json_encode($v, JSON_UNESCAPED_UNICODE);
}
$diagFull = DiagLog::asText(DiagLog::tail(400, false), $diagHeader, 'CGM-diet — полный лог');
$diagErrors = DiagLog::asText(DiagLog::tail(400, true), $diagHeader, 'CGM-diet — только ошибки');

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

$notifyTemplates = notify_templates($pdo);
$notifySent = $pdo->query(
    "SELECT code, COUNT(*) AS n FROM notification_sends GROUP BY code ORDER BY n DESC"
)->fetchAll(PDO::FETCH_ASSOC);
$notifySentByCode = [];
foreach ($notifySent as $row) $notifySentByCode[$row['code']] = (int) $row['n'];

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
    .field input, .field select, .field textarea { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--fg); font-size: 14px; }
    .field select + select { margin-top: 6px; }
    .field textarea { font: 12px/1.45 ui-monospace, Menlo, Consolas, monospace; white-space: pre; overflow-wrap: normal; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    td, th { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; }
    .notice { background: var(--accent); color: var(--accent-fg); padding: 10px 14px; border-radius: 10px; margin-bottom: 16px; }
    .dead-picks { border: 1px solid var(--danger); color: var(--danger); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; font-size: 13px; }
    .dead-picks ul { margin: 6px 0; padding-left: 20px; }
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

    <?php if ($notice): ?><div class="notice"><?= nl2br(htmlspecialchars($notice)) ?></div><?php endif; ?>

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

        <?php if ($deadPicks): ?>
        <div class="dead-picks">
            <b>Этих моделей нет в живом каталоге провайдера</b> — на запрос к ним приходит
            <code>Failed to get model</code>:
            <ul>
            <?php foreach ($deadPicks as $pick): ?>
                <li><?= htmlspecialchars($pick['field']) ?> — <code><?= htmlspecialchars($pick['tag']) ?></code></li>
            <?php endforeach; ?>
            </ul>
            <?php foreach ($deadSuggest as $suggest): ?>
            <?php if ($suggest['models']): ?>
            В каталоге <?= htmlspecialchars($suggest['provider']) ?><?= $suggest['vision'] ? ' со зрением' : '' ?>:
            <?= htmlspecialchars(implode(', ', $suggest['models'])) ?>.<br>
            <?php else: ?>
            В каталоге <?= htmlspecialchars($suggest['provider']) ?> нет ни одной модели<?= $suggest['vision'] ? ' со зрением' : '' ?>.<br>
            <?php endif; ?>
            <?php endforeach; ?>
            Выберите модель из списков ниже или включите нужную в каталоге облака.
        </div>
        <?php endif; ?>

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
            <label>Модель по умолчанию — текст и всё, для чего не выбрана vision-модель</label>
            <?= $modelSelect('LLM_DEFAULT_MODEL', $eff('LLM_DEFAULT_MODEL'), $byShortId) ?>
        </div>
        <div class="field">
            <label>Vision-модель — фото еды, этикетки, страницы PDF (модели со зрением у обоих провайдеров)</label>
            <?= $modelSelect('LLM_VISION_MODEL', $visionCurrent, $byProviderSlug, $onlyVision, '— не задана (фото пойдёт в модель по умолчанию) —') ?>
        </div>
        <div class="field">
            <label>Запасная модель: что пробовать после выбранной</label>
            <select name="LLM_FALLBACK_MODE">
                <option value="auto" <?= $eff('LLM_FALLBACK_MODE') !== 'manual' ? 'selected' : '' ?>>авто — более новая версия той же модели, затем список</option>
                <option value="manual" <?= $eff('LLM_FALLBACK_MODE') === 'manual' ? 'selected' : '' ?>>только список ниже</option>
            </select>
        </div>
        <div class="field">
            <label>Запасные модели — из каталога, пробуются в этом порядке после выбранной</label>
            <?php for ($i = 1; $i <= 3; $i++): ?>
            <?= $modelSelect('LLM_FALLBACK_MODEL_' . $i, (string) ($fallbackPicked[$i - 1] ?? ''), $byShortId, null, '№' . $i . ' — не задана') ?>
            <?php endfor; ?>
        </div>
        <div class="field">
            <label>Последняя попытка на OpenRouter</label>
            <?= $modelSelect('LLM_FALLBACK_MODEL', $eff('LLM_FALLBACK_MODEL'), $bySlug, static fn (array $m): bool => ($m['provider'] ?? '') === 'openrouter', '— не задана —') ?>
        </div>
        <div class="field">
            <label>Последняя попытка на Yandex (full_id без gpt://)</label>
            <?= $modelSelect('YANDEX_FALLBACK_MODEL', $eff('YANDEX_FALLBACK_MODEL'), $bySlug, static fn (array $m): bool => ($m['provider'] ?? '') === 'yandex', '— не задана —') ?>
            <p class="muted" style="font-size:13px;"><b>⛔</b> в списках — провайдер ответил на запрос каталога, но этой модели в нём не назвал: слепой запрос к ней отвечает <code>Failed to get model</code> и прячет настоящую причину сбоя. В запасные такая модель не подставляется.</p>
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
        <?php $savedKey = (string) ($llmValues[$key] ?? ($cfg[$key] ?? '')); ?>
        <div class="field">
            <!-- First and last 4 characters only: enough to tell WHICH key is
                 saved without exposing it. Empty field keeps the stored one. -->
            <label><?= htmlspecialchars($label) ?> — <?= $savedKey !== '' ? 'сейчас ' . htmlspecialchars(DiagLog::maskKey($savedKey)) . ', пустое поле не сотрёт' : 'не задан' ?></label>
            <input type="password" name="<?= $key ?>" autocomplete="new-password" value="" placeholder="<?= $savedKey !== '' ? htmlspecialchars(DiagLog::maskKey($savedKey)) : 'вставьте ключ' ?>">
        </div>
        <?php endforeach; ?>

        <button class="btn btn-primary" type="submit">Сохранить</button>
        <button class="btn btn-secondary" type="submit" name="model_catalog" value="refresh" formnovalidate>Обновить каталог моделей</button>
        <?php if ($liveRows): ?>
        <button class="btn btn-secondary" type="submit" name="model_catalog" value="forget" formnovalidate>Забыть живой каталог</button>
        <?php endif; ?>
    </form>

    <form method="post" class="card">
        <h2 style="margin-top:0;">Проверка провайдеров</h2>
        <input type="hidden" name="action" value="llm_probe">
        <p class="muted">Один короткий запрос к каждой настроенной модели: сразу видно, что отвечает провайдер —
            неверный ключ, чужая папка, модель не включена в каталоге облака. Результат уходит и в лог.</p>
        <button class="btn btn-secondary" type="submit">Проверить модели и ключи</button>
    </form>

    <div class="card">
        <h2 style="margin-top:0;">Уведомления</h2>
        <p class="muted">Текст, время и действие задаются здесь — это <b>исходная</b> настройка для всех.
            Каждый человек может в приложении поставить своё время, включить «умное»
            (оно считается по его собственным записям на устройстве) или выключить уведомление совсем;
            его выбор перекрывает эту страницу. Снятая галочка «Включено» выключает уведомление у всех.</p>
        <?php foreach ($notifyTemplates as $t): ?>
        <form method="post" style="border-top:1px solid var(--border); padding-top:12px; margin-top:12px;">
            <input type="hidden" name="action" value="save_notification">
            <input type="hidden" name="code" value="<?= htmlspecialchars($t['code']) ?>">
            <div class="field">
                <label>Код <code><?= htmlspecialchars($t['code']) ?></code> · отправлено по расписанию: <?= (int) ($notifySentByCode[$t['code']] ?? 0) ?></label>
                <input type="text" name="title" value="<?= htmlspecialchars($t['title']) ?>" placeholder="Заголовок">
            </div>
            <div class="field">
                <label>Текст уведомления</label>
                <textarea name="body" rows="2" style="white-space:pre-wrap; font:inherit;"><?= htmlspecialchars($t['body']) ?></textarea>
            </div>
            <div class="field">
                <label>Время (через запятую, местное время человека)</label>
                <input type="text" name="times" value="<?= htmlspecialchars($t['times']) ?>" placeholder="08:30, 13:30, 19:00">
            </div>
            <div class="field">
                <label>Режим по умолчанию</label>
                <select name="mode">
                    <?php foreach (['fixed' => 'по времени выше', 'smart' => 'умное — по записям человека', 'off' => 'не слать по умолчанию'] as $m => $mLabel): ?>
                    <option value="<?= $m ?>" <?= ($t['mode'] === $m) ? 'selected' : '' ?>><?= $mLabel ?></option>
                    <?php endforeach; ?>
                </select>
            </div>
            <div class="field">
                <label>Что открывается по нажатию</label>
                <select name="notify_action">
                    <?php foreach (['camera' => 'камера', 'text' => 'ответ текстом', 'open' => 'просто приложение'] as $a => $aLabel): ?>
                    <option value="<?= $a ?>" <?= ($t['action'] === $a) ? 'selected' : '' ?>><?= $aLabel ?></option>
                    <?php endforeach; ?>
                </select>
            </div>
            <div class="field">
                <label>Записи, по которым считается «умное» время</label>
                <?php $signals = explode(',', (string) $t['signal']); ?>
                <?php foreach (NOTIFY_SIGNALS as $sig): ?>
                <label style="display:inline-block; margin-right:12px; font-size:14px;">
                    <input type="checkbox" name="signal[]" value="<?= $sig ?>" style="width:auto;" <?= in_array($sig, $signals, true) ? 'checked' : '' ?>>
                    <?= htmlspecialchars(['meal' => 'еда', 'glucose' => 'сахар/CGM', 'activity' => 'нагрузка', 'weight' => 'вес', 'wellbeing' => 'самочувствие'][$sig]) ?>
                </label>
                <?php endforeach; ?>
            </div>
            <div class="field">
                <label>За сколько минут до привычного момента писать (для «умного»)</label>
                <input type="number" name="lead_min" min="0" max="180" value="<?= (int) $t['lead_min'] ?>">
            </div>
            <div class="field">
                <label>Порядок в списке</label>
                <input type="number" name="sort" value="<?= (int) $t['sort'] ?>">
            </div>
            <label style="font-size:14px;"><input type="checkbox" name="enabled" style="width:auto;" <?= !empty($t['enabled']) ? 'checked' : '' ?>> Включено</label>
            <p style="margin-top:10px;">
                <button class="btn btn-primary" type="submit">Сохранить</button>
                <button class="btn btn-secondary" type="submit" name="action" value="send_notification_now" formnovalidate>Отправить сейчас</button>
                <button class="btn btn-secondary" type="submit" name="action" value="delete_notification" formnovalidate
                        onclick="return confirm('Удалить уведомление и настройки людей по нему?')">Удалить</button>
            </p>
        </form>
        <?php endforeach; ?>

        <form method="post" style="border-top:1px solid var(--border); padding-top:12px; margin-top:12px;">
            <h3 style="font-size:15px;">Новое уведомление</h3>
            <input type="hidden" name="action" value="save_notification">
            <div class="field"><label>Код (латиницей, попадает в ссылку)</label><input type="text" name="code" placeholder="water"></div>
            <div class="field"><label>Заголовок</label><input type="text" name="title" placeholder="Пора записать воду"></div>
            <div class="field"><label>Текст</label><textarea name="body" rows="2" style="white-space:pre-wrap; font:inherit;"></textarea></div>
            <div class="field"><label>Время</label><input type="text" name="times" placeholder="11:00, 16:00"></div>
            <div class="field">
                <label>Режим</label>
                <select name="mode"><option value="fixed">по времени выше</option><option value="smart">умное</option><option value="off">не слать по умолчанию</option></select>
            </div>
            <div class="field">
                <label>По нажатию</label>
                <select name="notify_action"><option value="camera">камера</option><option value="text">ответ текстом</option><option value="open">просто приложение</option></select>
            </div>
            <div class="field">
                <label>Записи для «умного» времени</label>
                <?php foreach (NOTIFY_SIGNALS as $sig): ?>
                <label style="display:inline-block; margin-right:12px; font-size:14px;">
                    <input type="checkbox" name="signal[]" value="<?= $sig ?>" style="width:auto;">
                    <?= htmlspecialchars(['meal' => 'еда', 'glucose' => 'сахар/CGM', 'activity' => 'нагрузка', 'weight' => 'вес', 'wellbeing' => 'самочувствие'][$sig]) ?>
                </label>
                <?php endforeach; ?>
            </div>
            <div class="field"><label>Упреждение, мин</label><input type="number" name="lead_min" min="0" max="180" value="15"></div>
            <label style="font-size:14px;"><input type="checkbox" name="enabled" style="width:auto;" checked> Включено</label>
            <p style="margin-top:10px;"><button class="btn btn-primary" type="submit">Добавить</button></p>
        </form>
    </div>

    <div class="card">
        <h2 style="margin-top:0;">Push-уведомления</h2>
        <p class="muted">VAPID-ключ: <code><?= htmlspecialchars($webConfig['VAPID_PUBLIC_KEY'] ?? '— ещё не сгенерирован —') ?></code></p>
        <p class="muted">Cron-ключ (для <code>/api/push_send.php?key=...</code>): <code><?= htmlspecialchars($webConfig['CRON_SECRET'] ?? '— не задан —') ?></code></p>
        <form method="post" style="display:inline;"><input type="hidden" name="action" value="set_cron_secret"><button class="btn btn-secondary" type="submit">Сгенерировать cron-ключ</button></form>
        <form method="post" style="display:inline;"><input type="hidden" name="action" value="regen_vapid"><button class="btn btn-secondary" type="submit">Перегенерировать VAPID</button></form>
        <form method="post" style="display:inline;"><input type="hidden" name="action" value="dispatch_now"><button class="btn btn-primary" type="submit">Прогнать расписание сейчас</button></form>
        <form method="post" style="display:inline;"><input type="hidden" name="action" value="send_reminder_now"><button class="btn btn-secondary" type="submit">Тестовое напоминание всем</button></form>
        <p class="muted">Cron хостинга должен дёргать <code>/api/push_send.php?key=…</code> каждые 5–15 минут:
            расписание считается в местном времени каждого человека, повтор в тот же слот отсекается.</p>
    </div>

    <div class="card">
        <h2 style="margin-top:0;">Лог</h2>
        <p class="muted">
            Записей: <b><?= (int) $diagCounts['total'] ?></b>, из них ошибок и предупреждений:
            <b><?= (int) $diagCounts['errors'] ?></b>. Лог лежит в той же базе и
            <b>очищается сам при обновлении кода</b> — здесь всегда только текущий деплой.
            Ключи и пароли в тексте замаскированы, лог можно пересылать как есть.
        </p>
        <p class="muted">База: <code><?= htmlspecialchars(WEB_DB_PATH) ?></code> —
            <?= WEB_DATA_PERSISTENT
                ? 'вне каталога деплоя, настройки переживут обновление.'
                : '<b>внутри каталога деплоя: настройки сотрутся при следующей заливке.</b> Создайте каталог <code>cgm-diet-data</code> рядом с корнем сайта (или задайте <code>WEB_DATA_DIR</code>) и повторите — база переедет сама.' ?>
        </p>
        <p>
            <button class="btn btn-secondary" type="button" onclick="diagCopy('diag-errors', this)">Скопировать только ошибки</button>
            <button class="btn btn-secondary" type="button" onclick="diagCopy('diag-full', this)">Скопировать полный лог</button>
        </p>
        <div class="field">
            <label>Только ошибки</label>
            <textarea id="diag-errors" readonly rows="10"><?= htmlspecialchars($diagErrors) ?></textarea>
        </div>
        <div class="field">
            <label>Полный лог</label>
            <textarea id="diag-full" readonly rows="16"><?= htmlspecialchars($diagFull) ?></textarea>
        </div>
        <form method="post"><input type="hidden" name="action" value="diag_clear"><button class="btn btn-secondary" type="submit">Очистить лог</button></form>
    </div>
    <script>
    function diagCopy(id, btn) {
        var el = document.getElementById(id);
        var done = function () { var t = btn.textContent; btn.textContent = 'Скопировано'; setTimeout(function () { btn.textContent = t; }, 1500); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(el.value).then(done, function () { el.select(); document.execCommand('copy'); done(); });
        } else { el.select(); document.execCommand('copy'); done(); }
    }
    </script>

    <form method="post" class="card">
        <h2 style="margin-top:0;">Автообновление кода с GitHub</h2>
        <input type="hidden" name="action" value="save_autopull">
        <?php
        $apOpts   = AutoPull::options($cfg, ['state_dir' => WEB_DATA_DIR]);
        $apStatus = AutoPull::status($apOpts);
        $apRoot   = AutoPull::root($apOpts);
        $apCfg    = AutoPull::pullConfig($apRoot);
        ?>
        <p class="muted">На время активной разработки: каждое обращение к сервису тихо спрашивает у GitHub
            head отслеживаемой ссылки. Тот же коммит — не происходит ничего; новый — <code>pull.php</code>
            выкладывает его, и страница открывается заново уже на новом коде. Репозиторий, токен и пароль
            <code>pull.php</code> берутся из <code>pull-config.php</code> в корне сайта — здесь их дублировать не нужно.
            <?php if ($apCfg === null): ?>
                <br><b>pull-config.php не найден (искали в <?= htmlspecialchars($apRoot) ?>) — включать нечего.</b>
            <?php else: ?>
                <br>Отслеживается: <b><?= htmlspecialchars($apCfg['repo']) ?></b> ·
                <?= $apCfg['source'] === 'pr' ? 'PR #' . (int) $apCfg['pr_number'] : 'ветка ' . htmlspecialchars($apCfg['branch']) ?>.
            <?php endif; ?>
            <?php if ($apStatus['checked_at'] > 0): ?>
                <br>Последняя проверка: <?= htmlspecialchars(date('Y-m-d H:i:s', $apStatus['checked_at'])) ?><?= $apStatus['note'] !== '' ? ' — ' . htmlspecialchars($apStatus['note']) : '' ?>.
            <?php endif; ?>
            <?php if ($apStatus['error'] !== ''): ?>
                <br><b>Ошибка: <?= htmlspecialchars(mb_substr($apStatus['error'], 0, 200)) ?></b>
            <?php endif; ?>
        </p>
        <div class="field">
            <label><input type="checkbox" name="AUTOPULL_ENABLED" value="1" <?= $eff('AUTOPULL_ENABLED') === '1' ? 'checked' : '' ?>>
                Проверять обновления при каждом запуске сервиса</label>
        </div>
        <div class="field"><label>Не чаще, сек (0 — при каждом обращении)</label>
            <input type="number" min="0" step="1" name="AUTOPULL_INTERVAL" value="<?= htmlspecialchars($eff('AUTOPULL_INTERVAL')) ?>"></div>
        <div class="field"><label>Адрес pull.php (пусто — вычисляется сам)</label>
            <input type="text" name="AUTOPULL_URL" value="<?= htmlspecialchars($eff('AUTOPULL_URL')) ?>" placeholder="https://сайт/pull.php"></div>
        <button class="btn btn-primary" type="submit">Сохранить</button>
    </form>

    <form method="post" class="card">
        <h2 style="margin-top:0;">Проверить обновление сейчас</h2>
        <input type="hidden" name="action" value="autopull_check">
        <p class="muted">Спрашивает head у GitHub и, если коммит новее выложенного, запускает
            <code>pull.php</code> — независимо от галочки выше.</p>
        <button class="btn btn-secondary" type="submit">Проверить и обновить</button>
    </form>

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
