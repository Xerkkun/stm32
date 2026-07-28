#include "fc_protocol.h"
#include "fc_shared_queue.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int failures;

#define CHECK(condition)                                                    \
    do {                                                                    \
        if (!(condition)) {                                                 \
            fprintf(stderr, "FAIL %s:%d: %s\n",                           \
                    __FILE__, __LINE__, #condition);                        \
            ++failures;                                                     \
        }                                                                   \
    } while (0)

static uint32_t float_word(float value)
{
    uint32_t word;
    memcpy(&word, &value, sizeof(word));
    return word;
}

static void test_protocol_golden_frame(void)
{
    const fc_sample_t sample = {
        .sequence = UINT32_C(0x01020304),
        .cycles = UINT32_C(0x11223344),
        .state = {1.0f, -2.0f, 0.5f},
        .system_id = UINT8_C(2),
        .method_id = UINT8_C(1),
        .status = FC_SAMPLE_STATUS_OK,
        .representation = FC_REPRESENTATION_FLOAT32
    };
    fc_wire_frame_t frame;
    fc_wire_frame_t corrupted;

    fc_make_state_frame(
        &frame,
        &sample,
        FC_BOARD_H755,
        UINT32_C(0xAABBCCDD));

    CHECK(frame.sync == FC_PROTOCOL_SYNC);
    CHECK(frame.version == FC_PROTOCOL_VERSION);
    CHECK(frame.kind == FC_FRAME_STATE);
    CHECK(frame.board_id == FC_BOARD_H755);
    CHECK(frame.payload_bytes == UINT16_C(24));
    CHECK(frame.sequence == sample.sequence);
    CHECK(frame.cycles == sample.cycles);
    CHECK(frame.dropped == UINT32_C(0xAABBCCDD));
    CHECK(frame.x_bits == float_word(1.0f));
    CHECK(frame.y_bits == float_word(-2.0f));
    CHECK(frame.z_bits == float_word(0.5f));
    CHECK(frame.crc32 == UINT32_C(0xEDA10E9E));
    CHECK(fc_wire_frame_is_valid(&frame));

    corrupted = frame;
    corrupted.y_bits ^= UINT32_C(1);
    CHECK(!fc_wire_frame_is_valid(&corrupted));
    CHECK(!fc_wire_frame_is_valid(NULL));
    fc_make_state_frame(NULL, &sample, FC_BOARD_H755, 0u);
    fc_make_state_frame(&frame, NULL, FC_BOARD_H755, 0u);
}

static void test_protocol_fixed_frame(void)
{
    fc_sample_t sample = {0};
    fc_wire_frame_t frame;

    sample.sequence = 7u;
    sample.fixed_state[0] = INT32_C(16384);
    sample.fixed_state[1] = -INT32_C(32768);
    sample.fixed_state[2] = INT32_C(8192);
    sample.system_id = 0u;
    sample.method_id = 2u;
    sample.status =
        FC_SAMPLE_STATUS_FIXED_STATE_SATURATION |
        FC_SAMPLE_STATUS_FIXED_COEFFICIENT_SATURATION |
        FC_SAMPLE_STATUS_FIXED_COEFFICIENT_ZEROED;
    sample.representation = FC_REPRESENTATION_FIXED_Q14;

    fc_make_state_frame(&frame, &sample, FC_BOARD_F746, 0u);
    CHECK(frame.kind == FC_FRAME_STATE_FIXED);
    CHECK(frame.x_bits == UINT32_C(0x00004000));
    CHECK(frame.y_bits == UINT32_C(0xFFFF8000));
    CHECK(frame.z_bits == UINT32_C(0x00002000));
    CHECK(frame.status == UINT8_C(0x1C));
    CHECK(fc_wire_frame_is_valid(&frame));

    frame.status = UINT8_C(0x80);
    frame.crc32 = fc_crc32(
        &frame, offsetof(fc_wire_frame_t, crc32));
    CHECK(!fc_wire_frame_is_valid(&frame));
}

static void test_protocol_timing_block(void)
{
    fc_sample_t sample = {0};
    fc_wire_frame_t frame;

    sample.sequence = 40u;
    sample.cycles = 101u;
    sample.state_words[0] = 102u;
    sample.state_words[1] = 103u;
    sample.state_words[2] = 104u;
    sample.system_id = 1u;
    sample.method_id = 0u;
    sample.status = FC_SAMPLE_STATUS_OK;
    sample.representation = FC_REPRESENTATION_TIMING_BLOCK;

    fc_make_state_frame(&frame, &sample, FC_BOARD_H755, 0u);
    CHECK(frame.kind == FC_FRAME_TIMING_BLOCK);
    CHECK(frame.sequence == 40u);
    CHECK(frame.cycles == 101u);
    CHECK(frame.x_bits == 102u);
    CHECK(frame.y_bits == 103u);
    CHECK(frame.z_bits == 104u);
    CHECK(frame.dropped == 0u);
    CHECK(fc_wire_frame_is_valid(&frame));
}

static void test_shared_queue_wrap_and_full(void)
{
    fc_shared_queue_t queue;
    fc_sample_t source = {0};
    fc_sample_t result;
    uint32_t index;

    fc_shared_queue_initialize(&queue);
    CHECK(queue.magic == FC_SHARED_QUEUE_MAGIC);
    CHECK(fc_shared_queue_dropped(&queue) == 0u);
    CHECK(fc_shared_queue_has_space(&queue));
    CHECK(!fc_shared_queue_has_space(NULL));

    for (index = 0u; index < FC_SHARED_QUEUE_CAPACITY; ++index) {
        source.sequence = index;
        source.cycles = UINT32_C(1000) + index;
        source.state[0] = (float)index;
        CHECK(fc_shared_queue_push(&queue, &source));
    }
    CHECK(!fc_shared_queue_has_space(&queue));
    CHECK(!fc_shared_queue_push(&queue, &source));
    CHECK(fc_shared_queue_dropped(&queue) == 1u);

    for (index = 0u; index < FC_SHARED_QUEUE_CAPACITY; ++index) {
        CHECK(fc_shared_queue_pop(&queue, &result));
        CHECK(fc_shared_queue_has_space(&queue));
        CHECK(result.sequence == index);
        CHECK(result.cycles == UINT32_C(1000) + index);
        CHECK(result.state[0] == (float)index);
    }
    CHECK(!fc_shared_queue_pop(&queue, &result));

    for (index = 0u; index < (FC_SHARED_QUEUE_CAPACITY * 3u); ++index) {
        source.sequence = UINT32_C(0x1000) + index;
        CHECK(fc_shared_queue_push(&queue, &source));
        CHECK(fc_shared_queue_pop(&queue, &result));
        CHECK(result.sequence == source.sequence);
    }

    CHECK(!fc_shared_queue_push(NULL, &source));
    CHECK(!fc_shared_queue_push(&queue, NULL));
    CHECK(!fc_shared_queue_pop(NULL, &result));
    CHECK(!fc_shared_queue_pop(&queue, NULL));
    CHECK(fc_shared_queue_dropped(NULL) == 0u);
    fc_shared_queue_initialize(NULL);
}

int main(void)
{
    test_protocol_golden_frame();
    test_protocol_fixed_frame();
    test_protocol_timing_block();
    test_shared_queue_wrap_and_full();

    if (failures != 0) {
        fprintf(stderr, "%d comprobaciones fallaron\n", failures);
        return 1;
    }

    puts("Protocolo y cola compartida: OK");
    return 0;
}
