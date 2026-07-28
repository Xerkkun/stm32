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

extern fc_shared_queue_t g_fc_h755_queue;
extern volatile uint32_t g_fc_h755_cm4_ready;

#ifdef __cplusplus
}
#endif

#endif

