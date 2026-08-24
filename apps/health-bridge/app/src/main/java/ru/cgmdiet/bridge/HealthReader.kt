package ru.cgmdiet.bridge

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import java.time.Instant

/**
 * Reads Health Connect and turns it into the bot's platform-neutral samples
 * (see `spec/health_sync.md` § Payload). Nothing is stored on the phone.
 */
class HealthReader(context: Context) {

    private val client: HealthConnectClient? =
        if (HealthConnectClient.getSdkStatus(context) == HealthConnectClient.SDK_AVAILABLE) {
            HealthConnectClient.getOrCreate(context)
        } else {
            null
        }

    val available: Boolean get() = client != null

    suspend fun missingPermissions(): Set<String> {
        val granted = client?.permissionController?.getGrantedPermissions() ?: emptySet()
        return PERMISSIONS - granted
    }

    /** Всё, что попало в окно `[from, to)`, одним списком. */
    suspend fun read(from: Instant, to: Instant): List<Sample> {
        val connect = client ?: return emptyList()
        val range = TimeRangeFilter.between(from, to)
        val samples = mutableListOf<Sample>()

        connect.readRecords(ReadRecordsRequest(StepsRecord::class, range)).records.forEach {
            samples += Sample(
                kind = "steps",
                start = it.startTime,
                end = it.endTime,
                externalId = "steps-${it.metadata.id}",
                steps = it.count,
            )
        }
        connect.readRecords(ReadRecordsRequest(ExerciseSessionRecord::class, range)).records
            .forEach {
                samples += Sample(
                    kind = "workout",
                    start = it.startTime,
                    end = it.endTime,
                    externalId = "workout-${it.metadata.id}",
                    title = it.title ?: it.exerciseType.toString(),
                )
            }
        connect.readRecords(ReadRecordsRequest(SleepSessionRecord::class, range)).records.forEach {
            samples += Sample(
                kind = "sleep",
                start = it.startTime,
                end = it.endTime,
                externalId = "sleep-${it.metadata.id}",
            )
        }
        connect.readRecords(ReadRecordsRequest(HeartRateRecord::class, range)).records.forEach {
            record ->
            val beats = record.samples.map { it.beatsPerMinute }
            if (beats.isNotEmpty()) {
                samples += Sample(
                    kind = "heart_rate",
                    start = record.startTime,
                    end = record.endTime,
                    externalId = "hr-${record.metadata.id}",
                    avgHr = beats.average(),
                )
            }
        }
        return samples
    }

    companion object {
        val PERMISSIONS = setOf(
            HealthPermission.getReadPermission(StepsRecord::class),
            HealthPermission.getReadPermission(ExerciseSessionRecord::class),
            HealthPermission.getReadPermission(SleepSessionRecord::class),
            HealthPermission.getReadPermission(HeartRateRecord::class),
        )
    }
}

/** One reading on its way to the bot. `externalId` makes a re-send harmless. */
data class Sample(
    val kind: String,
    val start: Instant,
    val end: Instant,
    val externalId: String,
    val steps: Long? = null,
    val avgHr: Double? = null,
    val title: String? = null,
)
