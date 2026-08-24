package ru.cgmdiet.bridge

import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Один экран: три поля, три кнопки, строка состояния.
 * Всё, что человек делает руками, — вставляет строку из бота и жмёт «Разрешить».
 */
class MainActivity : AppCompatActivity() {

    private lateinit var prefs: Prefs
    private lateinit var reader: HealthReader
    private lateinit var status: TextView
    private lateinit var base: EditText
    private lateinit var tgId: EditText
    private lateinit var token: EditText

    private val requestPermissions =
        registerForActivityResult(PermissionController.createRequestPermissionResultContract()) {
            refreshStatus()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        prefs = Prefs(this)
        reader = HealthReader(this)

        base = findViewById(R.id.base)
        tgId = findViewById(R.id.tgId)
        token = findViewById(R.id.token)
        status = findViewById(R.id.status)

        findViewById<Button>(R.id.paste).setOnClickListener { pasteSetupLink() }
        findViewById<Button>(R.id.save).setOnClickListener { saveFields() }
        findViewById<Button>(R.id.permissions).setOnClickListener {
            requestPermissions.launch(HealthReader.PERMISSIONS)
        }
        findViewById<Button>(R.id.syncNow).setOnClickListener { syncNow() }

        intent?.data?.toString()?.let { applyLink(it) }
        showFields()
        refreshStatus()
    }

    /** Ссылка из бота: сначала из открывшего нас интента, иначе из буфера обмена. */
    private fun pasteSetupLink() {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = clipboard.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
        if (!applyLink(text)) {
            toast("В буфере нет строки настройки. Скопируйте её в боте: /health → «Мои ключи».")
        }
    }

    private fun applyLink(text: String): Boolean {
        if (!prefs.applySetupLink(text)) return false
        showFields()
        SyncWorker.schedule(this)
        toast("Настройки приняты")
        refreshStatus()
        return true
    }

    private fun saveFields() {
        prefs.baseUrl = base.text.toString()
        prefs.tgId = tgId.text.toString().trim().toLongOrNull() ?: 0L
        prefs.token = token.text.toString()
        if (prefs.configured) SyncWorker.schedule(this)
        refreshStatus()
    }

    private fun showFields() {
        base.setText(prefs.baseUrl)
        tgId.setText(if (prefs.tgId == 0L) "" else prefs.tgId.toString())
        token.setText(prefs.token)
    }

    private fun syncNow() {
        status.text = "Отправляю…"
        lifecycleScope.launch {
            val message = try {
                when (val accepted = withContext(Dispatchers.IO) { SyncWorker.syncOnce(this@MainActivity) }) {
                    -1 -> "Сначала заполните настройки и разрешите доступ к Health Connect."
                    0 -> "Новых данных за это время не нашлось."
                    else -> "Отправлено записей: $accepted. Можно вернуться в бот и открыть /health."
                }
            } catch (error: Exception) {
                "Не получилось: ${error.message}"
            }
            status.text = message
        }
    }

    private fun refreshStatus() {
        lifecycleScope.launch {
            val lines = mutableListOf<String>()
            lines += if (reader.available) {
                "Health Connect: найден"
            } else {
                "Health Connect не установлен. Android 14+: Настройки → Безопасность и " +
                    "конфиденциальность → Ещё → Health Connect. Android 10–13: поставьте " +
                    "«Health Connect» из Galaxy Store или Google Play."
            }
            if (reader.available) {
                val missing = reader.missingPermissions()
                lines += if (missing.isEmpty()) {
                    "Доступ к данным: разрешён"
                } else {
                    "Доступ к данным: не хватает ${missing.size} — нажмите «Разрешить доступ»"
                }
            }
            lines += if (prefs.configured) "Настройки: заполнены" else "Настройки: пустые"
            status.text = lines.joinToString("\n\n")
        }
    }

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()
}
