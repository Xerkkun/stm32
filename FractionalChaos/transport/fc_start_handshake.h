#ifndef FC_START_HANDSHAKE_H
#define FC_START_HANDSHAKE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FC_START_REQUEST_ID_MAX 64U
#define FC_START_LINE_MAX       96U
#define FC_READY_LINE_MAX       (FC_START_REQUEST_ID_MAX + 8U)

typedef struct {
    char request_id[FC_START_REQUEST_ID_MAX + 1U];
    uint8_t kind;
    uint8_t board_id;
    uint8_t system_id;
    uint8_t method_id;
} fc_start_command_t;

bool fc_parse_start_command(
    const char *line,
    size_t length,
    fc_start_command_t *command);

bool fc_start_command_matches(
    const fc_start_command_t *command,
    uint8_t kind,
    uint8_t board_id,
    uint8_t system_id,
    uint8_t method_id);

size_t fc_format_ready_line(
    char *output,
    size_t capacity,
    const char *request_id);

#ifdef __cplusplus
}
#endif

#endif
