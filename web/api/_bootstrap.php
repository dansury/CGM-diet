<?php
/**
 * web/api/_bootstrap.php — shared setup for every endpoint in web/api/.
 * spec: spec/web.md § Хранение, § Распознавание.
 */

declare(strict_types=1);

error_reporting(E_ALL);
ini_set('display_errors', '0');

define('WEB_ROOT', dirname(__DIR__));
define('WEB_DB_PATH', getenv('WEB_DB_PATH') ?: WEB_ROOT . '/data/app.db');

// The vendored LLM layer reads DB_PATH itself (for the shared `settings`
// table) — point it at the same SQLite file so admin-configured API keys
// apply here too.
putenv('DB_PATH=' . WEB_DB_PATH);

require_once WEB_ROOT . '/lib/db.php';
require_once WEB_ROOT . '/lib/webpush.php';
require_once WEB_ROOT . '/lib/vendor/settings_store.php';
require_once WEB_ROOT . '/lib/vendor/model_catalog.php';
require_once WEB_ROOT . '/lib/vendor/llm.php';

$GLOBALS['web_pdo'] = web_db_connect(WEB_DB_PATH);
$GLOBALS['web_cfg'] = require WEB_ROOT . '/lib/vendor/config.php';
LLM::init($GLOBALS['web_cfg']);

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
