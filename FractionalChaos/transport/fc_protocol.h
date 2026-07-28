#ifndef FC_PROTOCOL_H
#define FC_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FC_PROTOCOL_SYNC       UINT32_C(0x31434346) /* "FCC1" little-endian */
#define FC_PROTOCOL_VERSION    UINT8_C(1)
#define FC_FRAME_STATE_FLOAT   UINT8_C(1)
#define FC_FRAME_STATE         FC_FRAME_STATE_FLOAT
#define FC_FRAME_METADATA      UINT8_C(2)
#define FC_FRAME_STATE_FIXED   UINT8_C(3)
#define FC_FRAME_TIMING_BLOCK  UINT8_C(4)

#define FC_BOARD_F746          UINT8_C(1)
#define FC_BOARD_H755          UINT8_C(2)

/*
 * status is a cumulative bit mask. Fixed-point producers keep a diagnostic
 * bit asserted after its counter first becomes nonzero. A value of zero is
 * therefore the only arithmetically valid status.
 */
#define FC_SAMPLE_STATUS_OK \
    UINT8_C(0x00)
#define FC_SAMPLE_STATUS_NONFINITE \
    UINT8_C(0x01)
#define FC_SAMPLE_STATUS_QUEUE \
    UINT8_C(0x02)
#define FC_SAMPLE_STATUS_FIXED_STATE_SATURATION \
    UINT8_C(0x04)
#define FC_SAMPLE_STATUS_FIXED_COEFFICIENT_SATURATION \
    UINT8_C(0x08)
#define FC_SAMPLE_STATUS_FIXED_COEFFICIENT_ZEROED \
    UINT8_C(0x10)
#define FC_SAMPLE_STATUS_KNOWN_MASK \
    UINT8_C(0x1F)

typedef struct {
    uint32_t sequence;
    uint32_t cycles;
    union {
        float state[3];
        int32_t fixed_state[3];
        uint32_t state_words[3];
    };
    uint8_t system_id;
    uint8_t method_id;
    uint8_t status;
    uint8_t representation;
} fc_sample_t;

#define FC_REPRESENTATION_FLOAT32   UINT8_C(0)
#define FC_REPRESENTATION_FIXED_Q14 UINT8_C(1)
#define FC_REPRESENTATION_TIMING_BLOCK UINT8_C(2)

/*
 * Trama binaria de 40 bytes. Todos los enteros se transmiten little-endian y
 * Para kind=FC_FRAME_STATE_FLOAT, x_bits..z_bits son palabras IEEE-754
 * binary32. Para kind=FC_FRAME_STATE_FIXED son enteros Q1.14.14 con signo.
 * Para kind=FC_FRAME_TIMING_BLOCK, sequence es el índice base cero del primer
 * paso medido y cycles,x_bits,y_bits,z_bits conservan cuatro conteos DWT
 * uint32_t consecutivos. El CRC-32/ISO-HDLC cubre los primeros 36 bytes.
 */
typedef struct {
    uint32_t sync;
    uint8_t version;
    uint8_t kind;
    uint8_t board_id;
    uint8_t system_id;
    uint8_t method_id;
    uint8_t status;
    uint16_t payload_bytes;
    uint32_t sequence;
    uint32_t cycles;
    uint32_t dropped;
    uint32_t x_bits;
    uint32_t y_bits;
    uint32_t z_bits;
    uint32_t crc32;
} fc_wire_frame_t;

_Static_assert(sizeof(float) == sizeof(uint32_t),
               "El protocolo requiere float IEEE-754 de 32 bits");
_Static_assert(sizeof(fc_sample_t) == 24u,
               "El registro compartido debe conservar su ABI de 24 bytes");
_Static_assert(sizeof(fc_wire_frame_t) == 40u,
               "La trama UART debe conservar su ABI de 40 bytes");

uint32_t fc_crc32(const void *data, size_t length);

void fc_make_state_frame(
    fc_wire_frame_t *frame,
    const fc_sample_t *sample,
    uint8_t board_id,
    uint32_t dropped);

bool fc_wire_frame_is_valid(const fc_wire_frame_t *frame);

#ifdef __cplusplus
}
#endif

#endif
