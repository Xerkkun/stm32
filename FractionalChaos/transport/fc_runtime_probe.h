#ifndef FC_RUNTIME_PROBE_H
#define FC_RUNTIME_PROBE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FC_RUNTIME_PROBE_MAGIC UINT32_C(0x31505246) /* "FRP1" */
#define FC_RUNTIME_PROBE_VERSION UINT32_C(1)
#define FC_RUNTIME_PROBE_SENTINEL UINT32_C(0xA5A5A5A5)

#define FC_RUNTIME_PROBE_ARMED UINT32_C(0x01)
#define FC_RUNTIME_PROBE_COMPLETE UINT32_C(0x02)
#define FC_RUNTIME_PROBE_OVERFLOW UINT32_C(0x04)
#define FC_RUNTIME_PROBE_INVALID UINT32_C(0x08)

#define FC_RUNTIME_CORE_F746_M7 UINT32_C(1)
#define FC_RUNTIME_CORE_H755_M7 UINT32_C(2)
#define FC_RUNTIME_CORE_H755_M4 UINT32_C(3)

typedef struct {
    uint32_t core_id;
    uint32_t expected_core_clock_hz;
    uint32_t software_core_clock_hz;
    uint32_t solver_bytes;
    uint32_t capture_bytes;
    uint32_t dma_bytes;
    uint32_t shared_bytes;
    uint32_t heap_configured_bytes;
} fc_runtime_probe_config_t;

/*
 * All fields are fixed-width so a host can read this record directly from a
 * halted target. Addresses refer to the target address space.
 */
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t core_id;
    uint32_t status;
    uint32_t region_start;
    uint32_t region_end;
    uint32_t paint_end;
    uint32_t region_bytes;
    uint32_t painted_bytes;
    uint32_t reserved_top_bytes;
    uint32_t untouched_bytes;
    uint32_t stack_high_water_bytes;
    uint32_t expected_core_clock_hz;
    uint32_t software_core_clock_hz;
    uint32_t solver_bytes;
    uint32_t capture_bytes;
    uint32_t dma_bytes;
    uint32_t shared_bytes;
    uint32_t heap_configured_bytes;
    uint32_t sentinel_word;
    uint32_t checksum;
} fc_runtime_probe_record_t;

_Static_assert(
    sizeof(fc_runtime_probe_record_t) == (21u * sizeof(uint32_t)),
    "The runtime-probe record must keep its fixed 84-byte ABI");

bool fc_runtime_probe_begin(
    fc_runtime_probe_record_t *record,
    uint8_t *region_start,
    uint8_t *region_end,
    uintptr_t stack_pointer,
    size_t guard_bytes,
    const fc_runtime_probe_config_t *config);

bool fc_runtime_probe_finish(
    fc_runtime_probe_record_t *record,
    const uint8_t *region_start,
    const uint8_t *region_end);

uint32_t fc_runtime_probe_checksum(
    const fc_runtime_probe_record_t *record);

bool fc_runtime_probe_record_is_valid(
    const fc_runtime_probe_record_t *record);

#ifdef __cplusplus
}
#endif

#endif
