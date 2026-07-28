#include "fc_shared_queue.h"

#include <string.h>

#if defined(__arm__) || defined(__thumb__)
#define FC_MEMORY_BARRIER() __asm volatile("dmb 0xF" ::: "memory")
#else
#include <stdatomic.h>
#define FC_MEMORY_BARRIER() atomic_thread_fence(memory_order_seq_cst)
#endif

void fc_shared_queue_initialize(fc_shared_queue_t *queue)
{
    if (queue == NULL) {
        return;
    }

    memset(queue, 0, sizeof(*queue));
    FC_MEMORY_BARRIER();
    queue->magic = FC_SHARED_QUEUE_MAGIC;
    FC_MEMORY_BARRIER();
}

bool fc_shared_queue_push(
    fc_shared_queue_t *queue,
    const fc_sample_t *sample)
{
    if ((queue == NULL) || (sample == NULL)) {
        return false;
    }

    const uint32_t write_sequence = queue->write_sequence;
    const uint32_t read_sequence = queue->read_sequence;

    if ((queue->magic != FC_SHARED_QUEUE_MAGIC) ||
        ((write_sequence - read_sequence) >= FC_SHARED_QUEUE_CAPACITY)) {
        queue->dropped++;
        return false;
    }

    queue->slots[write_sequence & (FC_SHARED_QUEUE_CAPACITY - UINT32_C(1))] =
        *sample;
    FC_MEMORY_BARRIER();
    queue->write_sequence = write_sequence + UINT32_C(1);
    return true;
}

bool fc_shared_queue_has_space(const fc_shared_queue_t *queue)
{
    uint32_t write_sequence;
    uint32_t read_sequence;

    if (queue == NULL) {
        return false;
    }

    FC_MEMORY_BARRIER();
    write_sequence = queue->write_sequence;
    read_sequence = queue->read_sequence;
    return (queue->magic == FC_SHARED_QUEUE_MAGIC) &&
           ((write_sequence - read_sequence) < FC_SHARED_QUEUE_CAPACITY);
}

bool fc_shared_queue_pop(
    fc_shared_queue_t *queue,
    fc_sample_t *sample)
{
    if ((queue == NULL) || (sample == NULL)) {
        return false;
    }

    const uint32_t read_sequence = queue->read_sequence;
    const uint32_t write_sequence = queue->write_sequence;

    if ((queue->magic != FC_SHARED_QUEUE_MAGIC) ||
        (read_sequence == write_sequence)) {
        return false;
    }

    *sample =
        queue->slots[read_sequence & (FC_SHARED_QUEUE_CAPACITY - UINT32_C(1))];
    FC_MEMORY_BARRIER();
    queue->read_sequence = read_sequence + UINT32_C(1);
    return true;
}

uint32_t fc_shared_queue_dropped(const fc_shared_queue_t *queue)
{
    if (queue == NULL) {
        return UINT32_C(0);
    }

    FC_MEMORY_BARRIER();
    return queue->dropped;
}
