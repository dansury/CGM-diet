<?php
/**
 * web/admin/login.php — password gate. First visit with no password set yet
 * lets the operator choose one (bootstrap), same idea as site_yacloud's
 * setup.php ADMIN_PASSWORD but stored separately (web_config).
 */
declare(strict_types=1);
require __DIR__ . '/lib.php';

$error = null;
$bootstrap = admin_password_hash() === null;

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'POST') {
    $password = (string) ($_POST['password'] ?? '');
    if ($bootstrap) {
        $confirm = (string) ($_POST['password_confirm'] ?? '');
        if (strlen($password) < 8) {
            $error = 'Пароль должен быть не короче 8 символов';
        } elseif ($password !== $confirm) {
            $error = 'Пароли не совпадают';
        } else {
            web_config_set(web_db(), 'ADMIN_PASSWORD_HASH', password_hash($password, PASSWORD_DEFAULT));
            $_SESSION['admin_authed'] = true;
            header('Location: index.php');
            exit;
        }
    } else {
        if (password_verify($password, (string) admin_password_hash())) {
            $_SESSION['admin_authed'] = true;
            header('Location: index.php');
            exit;
        }
        $error = 'Неверный пароль';
    }
}
?>
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CGM-diet — админка</title>
<link rel="stylesheet" href="../css/tokens.css">
<style>
    body { display: flex; align-items: center; justify-content: center; min-height: 100vh; }
    .box { width: 100%; max-width: 340px; padding: 24px; }
    .box input { width: 100%; padding: 12px; margin-bottom: 10px; border-radius: 10px; border: 1px solid var(--border); background: var(--bg-elevated); color: var(--fg); font-size: 16px; }
    .error { color: var(--danger); font-size: 14px; margin-bottom: 10px; }
</style>
</head>
<body>
<form class="card box" method="post">
    <h2><?= $bootstrap ? 'Задайте пароль администратора' : 'Вход в админку' ?></h2>
    <?php if ($error): ?><div class="error"><?= htmlspecialchars($error) ?></div><?php endif; ?>
    <input type="password" name="password" placeholder="Пароль" required autofocus>
    <?php if ($bootstrap): ?>
        <input type="password" name="password_confirm" placeholder="Повторите пароль" required>
    <?php endif; ?>
    <button class="btn btn-primary" type="submit" style="width:100%;"><?= $bootstrap ? 'Сохранить' : 'Войти' ?></button>
</form>
</body>
</html>
