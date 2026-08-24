package ru.cgmdiet.bridge

import android.content.Context
import android.net.Uri

/**
 * Три поля, которые пользователь копирует из бота, и отметка последней отправки.
 * Ничего больше приложение о человеке не знает.
 */
class Prefs(context: Context) {

    private val store = context.getSharedPreferences("bridge", Context.MODE_PRIVATE)

    var baseUrl: String
        get() = store.getString(KEY_BASE, "").orEmpty()
        set(value) = store.edit().putString(KEY_BASE, value.trim().trimEnd('/')).apply()

    var tgId: Long
        get() = store.getLong(KEY_TG, 0L)
        set(value) = store.edit().putLong(KEY_TG, value).apply()

    var token: String
        get() = store.getString(KEY_TOKEN, "").orEmpty()
        set(value) = store.edit().putString(KEY_TOKEN, value.trim()).apply()

    /** Начало следующего окна чтения: перекрытия не страшны — сервер их отсеет по external_id. */
    var syncedUntilMillis: Long
        get() = store.getLong(KEY_SYNCED, 0L)
        set(value) = store.edit().putLong(KEY_SYNCED, value).apply()

    val configured: Boolean
        get() = baseUrl.isNotEmpty() && tgId != 0L && token.isNotEmpty()

    /** `cgmdiet://setup?base=…&tg=…&token=…` — одна строка из бота вместо трёх полей. */
    fun applySetupLink(raw: String): Boolean {
        val text = raw.trim()
        if (!text.startsWith("cgmdiet://")) return false
        val uri = Uri.parse(text)
        val base = uri.getQueryParameter("base").orEmpty()
        val tg = uri.getQueryParameter("tg")?.toLongOrNull() ?: 0L
        val newToken = uri.getQueryParameter("token").orEmpty()
        if (base.isEmpty() || tg == 0L || newToken.isEmpty()) return false
        baseUrl = base
        tgId = tg
        token = newToken
        return true
    }

    private companion object {
        const val KEY_BASE = "base_url"
        const val KEY_TG = "tg_id"
        const val KEY_TOKEN = "token"
        const val KEY_SYNCED = "synced_until"
    }
}
