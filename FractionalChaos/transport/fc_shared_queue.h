#ifndef FC_SHARED_QUEUE_H
#define FC_SHARED_QUEUE_H

#include "fc_protocol.h"

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FC_SHARED_QUEUE_MAGIC    UINT32_C(0x31514643) /* "CFQ1" */
#define FC_SHARED_QUEUE_CAPACITY UINT32_C(64)

typedef struct {
    uint32_t magic;
    volatile uint32_t write_sequence;
    volatile uint32_t read_sequence;
    volatile uint32_t dropped;
    fc_sample_t slots[FC_SHARED_QUEUE_CAPACITY];
} fc_shared_queue_t;

_Static_assert((FC_SHARED_QUEUE_CAPACITY &
                (FC_SHARED_QUEUE_CAPACITY - UINT32_C(1))) == UINT32_C(0),
               "La capacidad de la cola debe ser potencia de dos");

void fc_shared_queue_initialize(fc_shared_queue_t *queue);

bool fc_shared_queue_push(
    fc_shared_queue_t *queue,
    const fc_sample_t *sample);

/*
 * Consulta no destructiva para el productor único. Permite esperar espacio
 * antes de publicar bloques de benchmark, sin incrementar dropped.
 */
bool fc_shared_queue_has_space(const fc_shared_queue_t *queue);

bool fc_shared_queue_pop(
    fc_shared_queue_t *queue,
    fc_sample_t *sample);

uint32_t fc_shared_queue_dropped(const fc_shared_queue_t *queue);

#ifdef __cplusplus
}
#endif

#endif
