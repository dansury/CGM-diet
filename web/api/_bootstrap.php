<?php
/**
 * web/api/_bootstrap.php — shared setup for every endpoint in web/api/.
 * spec: spec/web.md § Хранение, § Распознавание.
 */

declare(strict_types=1);

error_reporting(E_ALL);
ini_set('display_errors', '0');

define('WEB_ROOT', dirname(__DIR__));

/**
 * Where app.db lives. A deploy replaces the contents of web/, so a database
 * inside it takes the API keys and the model order with it — that is exactly
 * what «при редеплое затираются настройки» was. Order:
 *   1. WEB_DB_PATH   — explicit full path (kept for existing installs)
 *   2. WEB_DATA_DIR  — explicit directory
 *   3. <parent of web root>/cgm-diet-data — outside the deploy, created on demand
 *   4. web/data      — last resort; the admin page then says settings are at risk
 */
function web_resolve_data_dir(): string {
    $explicitDb = getenv('WEB_DB_PATH');
    if (is_string($explicitDb) && $explicitDb !== '') return dirname($explicitDb);
    $explicitDir = getenv('WEB_DATA_DIR');
    if (is_string($explicitDir) && $explicitDir !== '') return rtrim($explicitDir, '/');
    $outside = dirname(WEB_ROOT) . '/cgm-diet-data';
    if (is_dir($outside)) {
        if (is_writable($outside)) return $outside;
    } elseif (@mkdir($outside, 0775, true) || is_dir($outside)) {
        return $outside;
    }
    return WEB_ROOT . '/data';
}

define('WEB_DATA_DIR', web_resolve_data_dir());
$_webDbPath = getenv('WEB_DB_PATH');
define('WEB_DB_PATH', is_string($_webDbPath) && $_webDbPath !== '' ? $_webDbPath : WEB_DATA_DIR . '/app.db');
/** Is the database outside the directory a deploy overwrites? */
define('WEB_DATA_PERSISTENT', strpos(realpath(WEB_DATA_DIR) ?: WEB_DATA_DIR, realpath(WEB_ROOT) ?: WEB_ROOT) !== 0);

// One-time move of a pre-existing in-tree database to the persistent location,
// so an install that already has keys saved does not start from scratch.
if (!file_exists(WEB_DB_PATH) && WEB_DB_PATH !== WEB_ROOT . '/data/app.db' && file_exists(WEB_ROOT . '/data/app.db')) {
    if (!is_dir(dirname(WEB_DB_PATH))) @mkdir(dirname(WEB_DB_PATH), 0775, true);
    @copy(WEB_ROOT . '/data/app.db', WEB_DB_PATH);
}

// The vendored LLM layer reads DB_PATH itself (for the shared `settings`
// table) — point it at the same SQLite file so admin-configured API keys
// apply here too.
putenv('DB_PATH=' . WEB_DB_PATH);

require_once WEB_ROOT . '/lib/db.php';
require_once WEB_ROOT . '/lib/webpush.php';
require_once WEB_ROOT . '/lib/model_hints.php';
require_once WEB_ROOT . '/lib/vendor/settings_store.php';
require_once WEB_ROOT . '/lib/vendor/model_catalog.php';
require_once WEB_ROOT . '/lib/vendor/diag_log.php';
require_once WEB_ROOT . '/lib/vendor/llm.php';
require_once WEB_ROOT . '/lib/vendor/auto_pull.php';

$GLOBALS['web_pdo'] = web_db_connect(WEB_DB_PATH);
$GLOBALS['web_cfg'] = require WEB_ROOT . '/lib/vendor/config.php';

// Diagnostic log — same database, emptied whenever the deployed code changes
// (spec/web.md § Лог). Secrets are registered so a copied log carries none.
DiagLog::init(WEB_DB_PATH, DiagLog::codeStamp([
    WEB_ROOT . '/lib/vendor/llm.php', WEB_ROOT . '/lib/vendor/config.php',
    WEB_ROOT . '/lib/vendor/model_catalog.php', WEB_ROOT . '/lib/db.php',
    WEB_ROOT . '/lib/notifications.php', WEB_ROOT . '/lib/model_hints.php',
    WEB_ROOT . '/api/recognize.php', WEB_ROOT . '/admin/index.php',
    WEB_ROOT . '/app/js/app.js',
]));
foreach (['OPENROUTER_API_KEY', 'YANDEX_API_KEY'] as $secretKey) {
    DiagLog::addSecret((string) ($GLOBALS['web_cfg'][$secretKey] ?? ''));
}
LLM::init($GLOBALS['web_cfg'], DiagLog::store());

// Автообновление кода на время активной разработки (галочка в /admin). Пока она
// стоит, каждый запрос тихо спрашивает у GitHub head отслеживаемой pull.php
// ссылки: тот же коммит — ничего не происходит и ничего не печатается, новый —
// pull.php выкладывает его, и страница открывается заново уже на новом коде.
// Креды — из pull-config.php в корне сайта. Состояние лежит в каталоге данных,
// который деплой не перезаписывает (WEB_DATA_DIR).
AutoPull::run(AutoPull::options($GLOBALS['web_cfg'], ['state_dir' => WEB_DATA_DIR]));

// PHP notices/warnings and uncaught throwables are invisible on shared hosting
// otherwise: display_errors is off and the host's error_log is unreachable.
set_error_handler(static function (int $no, string $msg, string $file = '', int $line = 0): bool {
    DiagLog::warn('php', $msg, ['file' => basename($file), 'line' => $line, 'errno' => $no]);
    return false;   // let PHP's own handling continue
});
set_exception_handler(static function (Throwable $e): void {
    DiagLog::error('php', get_class($e) . ': ' . $e->getMessage(), [
        'file' => basename($e->getFile()) . ':' . $e->getLine(),
        'trace' => mb_substr($e->getTraceAsString(), 0, 2000),
    ]);
});

function web_db(): PDO {
    return $GLOBALS['web_pdo'];
}

function web_cfg(): array {
    return $GLOBALS['web_cfg'];
}

function json_input(): array {
    $raw = file_get_contents('php://input');
    if ($raw === false || $raw === '') return [];
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function json_out($data, int $status = 200): void {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function json_error(string $message, int $status = 400): void {
    json_out(['error' => $message], $status);
}

function require_method(string $method): void {
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== $method) {
        json_error('method not allowed', 405);
    }
}

/** Bearer <sync_token> -> users row, or null. */
function auth_user_by_token(): ?array {
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    if (!preg_match('/^Bearer\s+(.+)$/i', $header, $m)) return null;
    $stmt = web_db()->prepare('SELECT * FROM users WHERE sync_token = :t');
    $stmt->execute([':t' => trim($m[1])]);
    $row = $stmt->fetch(PDO::FETCH_ASSOC);
    return $row ?: null;
}
