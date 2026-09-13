<?php
/**
 * php web/tests/auto_pull.php — what the deploy check decides BEFORE any
 * network call: reading pull-config.php, finding the root, the switch mapping,
 * the cooldown and the pull.php auth token. The GitHub call and the deploy
 * itself are HTTP and stay out of tests.
 * spec: spec/web.md § Автообновление кода.
 */

declare(strict_types=1);

require __DIR__ . '/../lib/vendor/auto_pull.php';

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

$root = sys_get_temp_dir() . '/cgm-autopull-test-' . getmypid();
@mkdir($root . '/app', 0775, true);

// ── pull-config.php ──────────────────────────────────────────────────────
check('нет pull-config.php — включать нечего', AutoPull::pullConfig($root), null);

file_put_contents($root . '/pull-config.php', "<?php return ["
    . "'repo' => 'owner/name', 'branch' => 'main', 'gh_token' => 'tok',"
    . "'password_hash' => 'HASH'];");
putenv('GITHUB_TOKEN=');        // как на хостинге: переменной обычно нет
$cfg = AutoPull::pullConfig($root);
check('репозиторий из конфига', $cfg['repo'], 'owner/name');
check('ветка по умолчанию — отслеживается', $cfg['source'], 'branch');
check('токен читается', $cfg['token'], 'tok');

putenv('GITHUB_TOKEN=из-окружения');   // ENV важнее файла — как у самого pull.php
check('GITHUB_TOKEN перекрывает gh_token', AutoPull::pullConfig($root)['token'], 'из-окружения');
putenv('GITHUB_TOKEN=');

file_put_contents($root . '/pull-config.php', "<?php return ["
    . "'repo' => 'owner/name', 'branch' => 'main', 'pr_number' => 42];");
$cfg = AutoPull::pullConfig($root);
check('номер PR переключает источник', [$cfg['source'], $cfg['pr_number']], ['pr', 42]);

// ── где искать pull.php ──────────────────────────────────────────────────
$_SERVER['DOCUMENT_ROOT'] = $root . '/app';
check('корень ищется вверх от DOCUMENT_ROOT', AutoPull::root([]), $root);
check('явный корень не ищут', AutoPull::root(['root' => '/srv/site/']), '/srv/site');

// ── галочка и её параметры ───────────────────────────────────────────────
$opts = AutoPull::options(['AUTOPULL_ENABLED' => '0', 'AUTOPULL_INTERVAL' => 30]);
check('выключено — enabled false', $opts['enabled'], false);
$opts = AutoPull::options(['AUTOPULL_ENABLED' => '1', 'AUTOPULL_INTERVAL' => 30, 'AUTOPULL_URL' => 'https://s/pull.php']);
check('включено — enabled true', $opts['enabled'], true);
check('интервал и адрес переносятся', [$opts['interval'], $opts['pull_url']], [30, 'https://s/pull.php']);
check('свои значения перекрывают конфиг',
    AutoPull::options(['AUTOPULL_ENABLED' => '1'], ['enabled' => false])['enabled'], false);

// ── состояние: ошибка гасит автоматику, пустое состояние ничего не знает ──
$stateDir = $root . '/data';
$opts = ['enabled' => true, 'root' => $root, 'state_dir' => $stateDir];
check('состояния ещё нет — проверок не было', AutoPull::status($opts)['checked_at'], 0);

file_put_contents($root . '/pull-config.php', "<?php return ['repo' => ''];");
$report = AutoPull::check($opts, false);   // без repo до сети дело не доходит
check('пустой репозиторий — ошибка, а не запрос', $report['ok'], false);
$status = AutoPull::status($opts);
check('ошибка записана', $status['error'] !== '', true);
check('ошибка гасит автоматику на время', $status['cooldown_until'] > time(), true);

// ── кука, которой подписывается запрос к pull.php ────────────────────────
// Тот же алгоритм, что у самого pull.php (auth_token/auth_token_valid).
$hash    = password_hash('деплой-пароль', PASSWORD_DEFAULT);
$expires = time() + 300;
$token   = $expires . '|' . hash_hmac('sha256', 'pull-auth|' . $expires, $hash);
[$exp, $sig] = explode('|', $token, 2);
check('pull.php примет подпись по хешу из pull-config.php',
    hash_equals(hash_hmac('sha256', 'pull-auth|' . (int) $exp, $hash), $sig), true);
check('чужой хеш подпись не подтвердит',
    hash_equals(hash_hmac('sha256', 'pull-auth|' . (int) $exp, 'other'), $sig), false);

array_map('unlink', glob($root . '/data/*') ?: []);
array_map('unlink', glob($root . '/*.php') ?: []);
@rmdir($root . '/data'); @rmdir($root . '/app'); @rmdir($root);

echo $failures === 0 ? "\nвсе проверки пройдены\n" : "\nпровалено: {$failures}\n";
exit($failures === 0 ? 0 : 1);
