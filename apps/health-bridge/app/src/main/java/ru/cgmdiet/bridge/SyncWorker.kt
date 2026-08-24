package ru.cgmdiet.bridge

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.time.Duration
import java.time.Instant

/**
 * Раз в час: прочитать окно, отправить, запомнить границу.
 * Батарея важнее свежести — при неудаче просто ждём следующего запуска.
 */
class SyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = try {
        syncOnce(applicationContext)
        Result.success()
    } catch (error: Exception) {
        // Сеть или сервер недоступны — повторим, данные никуда не денутся.
        Result.retry()
    }

    companion object {
        private const val WORK_NAME = "cgm-health-sync"
        private const val LOOKBACK_DAYS = 3L

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<SyncWorker>(Duration.ofHours(1))
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.UPDATE,
                request,
            )
        }

        /** Одна синхронизация. Returns accepted samples, -1 если настройки не заполнены. */
        suspend fun syncOnce(context: Context): Int {
            val prefs = Prefs(context)
            if (!prefs.configured) return -1
            val reader = HealthReader(context)
            if (!reader.available || reader.missingPermissions().isNotEmpty()) return -1
            val now = Instant.now()
            val from = Instant.ofEpochMilli(prefs.syncedUntilMillis)
                .takeIf { prefs.syncedUntilMillis > 0 }
                ?: now.minus(Duration.ofDays(LOOKBACK_DAYS))
            val accepted = Uploader.send(prefs, reader.read(from, now))
            prefs.syncedUntilMillis = now.toEpochMilli()
            return accepted
        }
    }
}
