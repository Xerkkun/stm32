#include "fc_protocol.h"

#include <string.h>

uint32_t fc_crc32(const void *data, size_t length)
{
    const uint8_t *bytes = (const uint8_t *)data;
    uint32_t crc = UINT32_C(0xFFFFFFFF);

    for (size_t i = 0u; i < length; ++i) {
        crc ^= bytes[i];
        for (uint32_t bit = 0u; bit < 8u; ++bit) {
            const uint32_t mask = (uint32_t)-(int32_t)(crc & UINT32_C(1));
            crc = (crc >> 1u) ^ (UINT32_C(0xEDB88320) & mask);
        }
    }

    return ~crc;
}

void fc_make_state_frame(
    fc_wire_frame_t *frame,
    const fc_sample_t *sample,
    uint8_t board_id,
    uint32_t dropped)
{
    if ((frame == NULL) || (sample == NULL)) {
        return;
    }

    memset(frame, 0, sizeof(*frame));
    frame->sync = FC_PROTOCOL_SYNC;
    frame->version = FC_PROTOCOL_VERSION;
    if (sample->representation == FC_REPRESENTATION_FIXED_Q14) {
        frame->kind = FC_FRAME_STATE_FIXED;
    } else if (
        sample->representation == FC_REPRESENTATION_TIMING_BLOCK) {
        frame->kind = FC_FRAME_TIMING_BLOCK;
    } else {
        frame->kind = FC_FRAME_STATE_FLOAT;
    }
    frame->board_id = board_id;
    frame->system_id = sample->system_id;
    frame->method_id = sample->method_id;
    frame->status = sample->status;
    frame->payload_bytes = UINT16_C(24);
    frame->sequence = sample->sequence;
    frame->cycles = sample->cycles;
    frame->dropped = dropped;
    frame->x_bits = sample->state_words[0];
    frame->y_bits = sample->state_words[1];
    frame->z_bits = sample->state_words[2];
    frame->crc32 = fc_crc32(frame, offsetof(fc_wire_frame_t, crc32));
}

bool fc_wire_frame_is_valid(const fc_wire_frame_t *frame)
{
    if (frame == NULL) {
        return false;
    }

    if ((frame->sync != FC_PROTOCOL_SYNC) ||
        (frame->version != FC_PROTOCOL_VERSION) ||
        ((frame->kind != FC_FRAME_STATE_FLOAT) &&
         (frame->kind != FC_FRAME_STATE_FIXED) &&
         (frame->kind != FC_FRAME_TIMING_BLOCK)) ||
        ((frame->status &
          (uint8_t)~FC_SAMPLE_STATUS_KNOWN_MASK) != 0u) ||
        (frame->payload_bytes != UINT16_C(24))) {
        return false;
    }

    return frame->crc32 ==
           fc_crc32(frame, offsetof(fc_wire_frame_t, crc32));
}
