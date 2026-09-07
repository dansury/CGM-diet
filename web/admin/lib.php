<?php
/**
 * web/admin/lib.php — session gate for /admin. Password lives in `web_config`
 * (CGM-diet-specific — not the vendored `settings` table, see
 * web/lib/vendor/VENDORED_FROM.md). spec: spec/web.md § Админка.
 */

declare(strict_types=1);
require_once __DIR__ . '/../api/_bootstrap.php';

if (session_status() !== PHP_SESSION_ACTIVE) {
    session_name('cgmdiet_admin');
    session_start();
}

function admin_password_hash(): ?string {
    return web_config_get(web_db(), 'ADMIN_PASSWORD_HASH');
}

function admin_is_authed(): bool {
    return !empty($_SESSION['admin_authed']);
}

function admin_require_login(): void {
    if (!admin_is_authed()) {
        header('Location: login.php');
        exit;
    }
}
