#include "fc_runtime_probe.h"

static void clear_record(fc_runtime_probe_record_t *record)
{
    uint32_t *words = (uint32_t *)(void *)record;
    size_t index;

    for (index = 0U;
         index < sizeof(*record) / sizeof(*words);
         ++index) {
        words[index] = 0U;
    }
}

uint32_t fc_runtime_probe_checksum(
    const fc_runtime_probe_record_t *record)
{
    const uint8_t *bytes = (const uint8_t *)(const void *)record;
    const size_t count =
        offsetof(fc_runtime_probe_record_t, checksum);
    uint32_t hash = UINT32_C(2166136261);
    size_t index;

    if (record == NULL) {
        return 0U;
    }
    for (index = 0U; index < count; ++index) {
        hash ^= bytes[index];
        hash *= UINT32_C(16777619);
    }
    return hash;
}

bool fc_runtime_probe_begin(
    fc_runtime_probe_record_t *record,
    uint8_t *region_start,
    uint8_t *region_end,
    uintptr_t stack_pointer,
    size_t guard_bytes,
    const fc_runtime_probe_config_t *config)
{
    uintptr_t start;
    uintptr_t end;
    uintptr_t paint_end;
    uint32_t *word;

    if (record == NULL) {
        return false;
    }
    clear_record(record);
    record->magic = FC_RUNTIME_PROBE_MAGIC;
    record->version = FC_RUNTIME_PROBE_VERSION;
    record->status = FC_RUNTIME_PROBE_INVALID;
    record->sentinel_word = FC_RUNTIME_PROBE_SENTINEL;

    if ((region_start == NULL) ||
        (region_end == NULL) ||
        (config == NULL)) {
        record->checksum = fc_runtime_probe_checksum(record);
        return false;
    }
    start = (uintptr_t)(void *)region_start;
    end = (uintptr_t)(void *)region_end;
    if ((start >= end) ||
        ((start & (sizeof(uint32_t) - 1U)) != 0U) ||
        ((end & (sizeof(uint32_t) - 1U)) != 0U) ||
        (stack_pointer <= start) ||
        (stack_pointer > end) ||
        (guard_bytes >= (size_t)(stack_pointer - start)) ||
        ((end - start) > UINT32_MAX)) {
        record->checksum = fc_runtime_probe_checksum(record);
        return false;
    }

    paint_end =
        (stack_pointer - (uintptr_t)guard_bytes) &
        ~(uintptr_t)(sizeof(uint32_t) - 1U);
    if ((paint_end <= start) ||
        ((paint_end - start) > UINT32_MAX) ||
        ((end - paint_end) > UINT32_MAX)) {
        record->checksum = fc_runtime_probe_checksum(record);
        return false;
    }

    for (word = (uint32_t *)(void *)region_start;
         (uintptr_t)(void *)word < paint_end;
         ++word) {
        *word = FC_RUNTIME_PROBE_SENTINEL;
    }

    record->core_id = config->core_id;
    record->status = FC_RUNTIME_PROBE_ARMED;
    record->region_start = (uint32_t)start;
    record->region_end = (uint32_t)end;
    record->paint_end = (uint32_t)paint_end;
    record->region_bytes = (uint32_t)(end - start);
    record->painted_bytes = (uint32_t)(paint_end - start);
    record->reserved_top_bytes = (uint32_t)(end - paint_end);
    record->untouched_bytes = record->painted_bytes;
    record->stack_high_water_bytes = record->reserved_top_bytes;
    record->expected_core_clock_hz = config->expected_core_clock_hz;
    record->software_core_clock_hz = config->software_core_clock_hz;
    record->solver_bytes = config->solver_bytes;
    record->capture_bytes = config->capture_bytes;
    record->dma_bytes = config->dma_bytes;
    record->shared_bytes = config->shared_bytes;
    record->heap_configured_bytes = config->heap_configured_bytes;
    record->checksum = fc_runtime_probe_checksum(record);
    return true;
}

bool fc_runtime_probe_finish(
    fc_runtime_probe_record_t *record,
    const uint8_t *region_start,
    const uint8_t *region_end)
{
    const uint32_t *word;
    const uint32_t *last;
    uint32_t untouched = 0U;

    if ((record == NULL) ||
        (region_start == NULL) ||
        (region_end == NULL) ||
        (record->magic != FC_RUNTIME_PROBE_MAGIC) ||
        (record->version != FC_RUNTIME_PROBE_VERSION) ||
        (record->status != FC_RUNTIME_PROBE_ARMED) ||
        ((uint32_t)(uintptr_t)(const void *)region_start !=
         record->region_start) ||
        ((uint32_t)(uintptr_t)(const void *)region_end !=
         record->region_end)) {
        if (record != NULL) {
            record->status |= FC_RUNTIME_PROBE_INVALID;
            record->checksum = fc_runtime_probe_checksum(record);
        }
        return false;
    }

    word = (const uint32_t *)(const void *)region_start;
    last = (const uint32_t *)(const void *)(
        region_start + record->painted_bytes);
    while ((word < last) && (*word == FC_RUNTIME_PROBE_SENTINEL)) {
        untouched += (uint32_t)sizeof(*word);
        ++word;
    }

    record->untouched_bytes = untouched;
    record->stack_high_water_bytes =
        record->reserved_top_bytes +
        (record->painted_bytes - untouched);
    record->status = FC_RUNTIME_PROBE_COMPLETE;
    if ((untouched == 0U) && (record->painted_bytes != 0U)) {
        record->status |= FC_RUNTIME_PROBE_OVERFLOW;
    }
    record->checksum = fc_runtime_probe_checksum(record);
    return true;
}

bool fc_runtime_probe_record_is_valid(
    const fc_runtime_probe_record_t *record)
{
    if (record == NULL) {
        return false;
    }
    return
        (record->magic == FC_RUNTIME_PROBE_MAGIC) &&
        (record->version == FC_RUNTIME_PROBE_VERSION) &&
        ((record->status & FC_RUNTIME_PROBE_INVALID) == 0U) &&
        (record->region_start < record->region_end) &&
        (record->paint_end > record->region_start) &&
        (record->paint_end <= record->region_end) &&
        (record->painted_bytes + record->reserved_top_bytes ==
         record->region_bytes) &&
        (record->untouched_bytes <= record->painted_bytes) &&
        (record->stack_high_water_bytes <= record->region_bytes) &&
        (record->sentinel_word == FC_RUNTIME_PROBE_SENTINEL) &&
        (record->checksum == fc_runtime_probe_checksum(record));
}
