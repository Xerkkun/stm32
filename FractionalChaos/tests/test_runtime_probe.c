#include "fc_runtime_probe.h"

#include <stdint.h>
#include <stdio.h>

#define CHECK(condition)                                                   \
    do {                                                                   \
        if (!(condition)) {                                                \
            fprintf(stderr, "CHECK failed at %s:%d\n", __FILE__, __LINE__); \
            return 1;                                                      \
        }                                                                  \
    } while (0)

static int test_clean_probe(void)
{
    _Alignas(4) uint8_t region[256];
    fc_runtime_probe_record_t record;
    const fc_runtime_probe_config_t config = {
        FC_RUNTIME_CORE_F746_M7,
        UINT32_C(216000000),
        UINT32_C(216000000),
        UINT32_C(48192),
        UINT32_C(40000),
        UINT32_C(64),
        0U,
        0U,
    };
    uintptr_t stack_pointer =
        (uintptr_t)(void *)(region + sizeof(region));

    CHECK(fc_runtime_probe_begin(
        &record,
        region,
        region + sizeof(region),
        stack_pointer,
        32U,
        &config));
    CHECK(record.painted_bytes == 224U);
    CHECK(record.reserved_top_bytes == 32U);
    CHECK(fc_runtime_probe_record_is_valid(&record));
    CHECK(fc_runtime_probe_finish(
        &record,
        region,
        region + sizeof(region)));
    CHECK(record.status == FC_RUNTIME_PROBE_COMPLETE);
    CHECK(record.untouched_bytes == 224U);
    CHECK(record.stack_high_water_bytes == 32U);
    CHECK(fc_runtime_probe_record_is_valid(&record));
    return 0;
}

static int test_detects_high_water(void)
{
    _Alignas(4) uint8_t region[256];
    fc_runtime_probe_record_t record;
    const fc_runtime_probe_config_t config = {
        FC_RUNTIME_CORE_H755_M7,
        UINT32_C(400000000),
        UINT32_C(400000000),
        UINT32_C(48192),
        UINT32_C(40000),
        0U,
        UINT32_C(1632),
        0U,
    };

    CHECK(fc_runtime_probe_begin(
        &record,
        region,
        region + sizeof(region),
        (uintptr_t)(void *)(region + sizeof(region)),
        32U,
        &config));
    region[record.painted_bytes - 68U] = 0U;
    CHECK(fc_runtime_probe_finish(
        &record,
        region,
        region + sizeof(region)));
    CHECK(record.untouched_bytes == record.painted_bytes - 68U);
    CHECK(record.stack_high_water_bytes == 100U);
    CHECK((record.status & FC_RUNTIME_PROBE_OVERFLOW) == 0U);
    CHECK(fc_runtime_probe_record_is_valid(&record));
    return 0;
}

static int test_detects_probe_exhaustion(void)
{
    _Alignas(4) uint8_t region[128];
    fc_runtime_probe_record_t record;
    const fc_runtime_probe_config_t config = {
        FC_RUNTIME_CORE_H755_M4,
        UINT32_C(200000000),
        UINT32_C(200000000),
        0U,
        0U,
        UINT32_C(64),
        UINT32_C(1632),
        0U,
    };

    CHECK(fc_runtime_probe_begin(
        &record,
        region,
        region + sizeof(region),
        (uintptr_t)(void *)(region + sizeof(region)),
        16U,
        &config));
    region[0] = 0U;
    CHECK(fc_runtime_probe_finish(
        &record,
        region,
        region + sizeof(region)));
    CHECK((record.status & FC_RUNTIME_PROBE_OVERFLOW) != 0U);
    CHECK(record.stack_high_water_bytes == sizeof(region));
    CHECK(fc_runtime_probe_record_is_valid(&record));
    return 0;
}

static int test_rejects_invalid_layout(void)
{
    _Alignas(4) uint8_t region[64];
    fc_runtime_probe_record_t record;
    const fc_runtime_probe_config_t config = {0};

    CHECK(!fc_runtime_probe_begin(
        &record,
        region,
        region + sizeof(region),
        (uintptr_t)(void *)(region + 8U),
        8U,
        &config));
    CHECK((record.status & FC_RUNTIME_PROBE_INVALID) != 0U);
    CHECK(!fc_runtime_probe_record_is_valid(&record));
    return 0;
}

int main(void)
{
    CHECK(test_clean_probe() == 0);
    CHECK(test_detects_high_water() == 0);
    CHECK(test_detects_probe_exhaustion() == 0);
    CHECK(test_rejects_invalid_layout() == 0);
    return 0;
}
