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
#define FC_FRAME_STATE         UINT8_C(1)
#define FC_FRAME_METADATA      UINT8_C(2)

#define FC_BOARD_F746          UINT8_C(1)
#define FC_BOARD_H755          UINT8_C(2)

#define FC_SAMPLE_STATUS_OK        UINT8_C(0)
#define FC_SAMPLE_STATUS_NONFINITE UINT8_C(1)
#define FC_SAMPLE_STATUS_QUEUE     UINT8_C(2)

typedef struct {
    uint32_t sequence;
    uint32_t cycles;
    float state[3];
    uint8_t system_id;
    uint8_t method_id;
    uint8_t status;
    uint8_t reserved;
} fc_sample_t;

/*
 * Trama binaria de 40 bytes. Todos los enteros se transmiten little-endian y
 * los estados se transportan como sus palabras IEEE-754 binary32, no como
 * texto decimal. El CRC-32/ISO-HDLC cubre los primeros 36 bytes.
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
