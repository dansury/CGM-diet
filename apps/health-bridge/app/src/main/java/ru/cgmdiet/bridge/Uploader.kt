package ru.cgmdiet.bridge

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONArray
import org.json.JSONObject

/**
 * POST /health/samsung — ровно один эндпоинт, ровно один заголовок.
 * Токен уходит только на адрес, который ввёл пользователь.
 */
object Uploader {

    private const val TIMEOUT_MS = 20_000

    class UploadError(message: String) : IOException(message)

    /** Returns how many samples the server accepted. */
    fun send(prefs: Prefs, samples: List<Sample>): Int {
        if (samples.isEmpty()) return 0
        val body = JSONObject().apply {
            put("tg_id", prefs.tgId)
            put("source", "health_connect")
            put("samples", JSONArray().also { array -> samples.forEach { array.put(it.toJson()) } })
        }.toString()

        val connection = (URL("${prefs.baseUrl}/health/samsung").openConnection()
            as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = TIMEOUT_MS
            readTimeout = TIMEOUT_MS
            doOutput = true
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            setRequestProperty("X-Health-Token", prefs.token)
        }
        try {
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            val text = (if (code in 200..299) connection.inputStream else connection.errorStream)
                ?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code == 403) throw UploadError("сервер не принял токен — проверьте строку настройки")
            if (code !in 200..299) throw UploadError("сервер ответил $code: ${text.take(200)}")
            return JSONObject(text.ifEmpty { "{}" }).optInt("accepted", samples.size)
        } finally {
            connection.disconnect()
        }
    }

    private fun Sample.toJson(): JSONObject = JSONObject().apply {
        put("kind", kind)
        put("start", start.toString())
        put("end", end.toString())
        put("external_id", externalId)
        steps?.let { put("steps", it) }
        avgHr?.let { put("avg_hr", it) }
        title?.let { put("title", it) }
    }
}
