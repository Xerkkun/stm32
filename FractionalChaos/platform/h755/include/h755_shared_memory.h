#ifndef H755_SHARED_MEMORY_H
#define H755_SHARED_MEMORY_H

#include "fc_shared_queue.h"

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FC_H755_SHARED_BASE       UINT32_C(0x38000000)
#define FC_H755_SHARED_BYTES      (64U * 1024U)
#define FC_H755_CM4_READY_MAGIC   UINT32_C(0x344D4346) /* "FCM4" */
#define FC_H755_CM4_DIAG_MAGIC    UINT32_C(0x47443443) /* "C4DG" */
#define FC_H755_START_MAGIC       UINT32_C(0x54534643) /* "CFST" */

typedef struct {
    uint32_t magic;
    uint8_t kind;
    uint8_t board_id;
    uint8_t system_id;
    uint8_t method_id;
} fc_h755_start_contract_t;

typedef struct {
    uint32_t magic;
    volatile uint32_t stage;
    volatile uint32_t loop_count;
    volatile uint32_t samples_popped;
    volatile uint32_t dma_started;
    volatile uint32_t tx_completed;
    volatile uint32_t uart_errors;
    volatile uint32_t last_hal_status;
    volatile uint32_t hardfault_count;
    volatile uint32_t cfsr;
    volatile uint32_t hfsr;
    volatile uint32_t mmfar;
    volatile uint32_t bfar;
} fc_h755_cm4_diagnostics_t;

extern fc_shared_queue_t g_fc_h755_queue;
extern volatile uint32_t g_fc_h755_cm4_ready;
extern fc_h755_start_contract_t g_fc_h755_start_contract;
extern fc_h755_cm4_diagnostics_t g_fc_h755_cm4_diagnostics;

#ifdef __cplusplus
}
#endif

#endif
