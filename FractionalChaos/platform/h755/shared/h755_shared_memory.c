#include "h755_shared_memory.h"

#include <stddef.h>

fc_shared_queue_t g_fc_h755_queue
    __attribute__((section(".shared.queue"), aligned(32), used));

volatile uint32_t g_fc_h755_cm4_ready
    __attribute__((section(".shared.flags"), aligned(4), used));

_Static_assert(
    (sizeof(g_fc_h755_queue) + sizeof(g_fc_h755_cm4_ready)) <=
        FC_H755_SHARED_BYTES,
    "La cola y sus banderas deben caber en SRAM4");

