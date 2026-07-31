#include "fc_start_handshake.h"

#include <string.h>

static bool request_character_is_valid(char value)
{
    return
        ((value >= 'a') && (value <= 'z')) ||
        ((value >= 'A') && (value <= 'Z')) ||
        ((value >= '0') && (value <= '9')) ||
        (value == '_') ||
        (value == '-');
}

static bool parse_u8_token(
    const char *line,
    size_t length,
    size_t *position,
    uint8_t *value)
{
    uint32_t parsed = 0U;
    size_t digits = 0U;

    if ((line == NULL) || (position == NULL) || (value == NULL)) {
        return false;
    }
    while ((*position < length) && (line[*position] == ' ')) {
        ++(*position);
    }
    while ((*position < length) &&
           (line[*position] >= '0') &&
           (line[*position] <= '9')) {
        parsed = parsed * 10U +
            (uint32_t)(line[*position] - '0');
        if (parsed > UINT8_MAX) {
            return false;
        }
        ++(*position);
        ++digits;
    }
    if (digits == 0U) {
        return false;
    }
    *value = (uint8_t)parsed;
    return true;
}

bool fc_parse_start_command(
    const char *line,
    size_t length,
    fc_start_command_t *command)
{
    static const char prefix[] = "START ";
    size_t position = sizeof(prefix) - 1U;
    size_t request_length = 0U;

    if ((line == NULL) || (command == NULL)) {
        return false;
    }
    if ((length > 0U) && (line[length - 1U] == '\n')) {
        --length;
    }
    if ((length > 0U) && (line[length - 1U] == '\r')) {
        --length;
    }
    if ((length <= position) ||
        (memcmp(line, prefix, position) != 0)) {
        return false;
    }

    memset(command, 0, sizeof(*command));
    while ((position < length) && (line[position] != ' ')) {
        if ((request_length >= FC_START_REQUEST_ID_MAX) ||
            !request_character_is_valid(line[position])) {
            return false;
        }
        command->request_id[request_length] = line[position];
        ++position;
        ++request_length;
    }
    if (request_length == 0U) {
        return false;
    }
    command->request_id[request_length] = '\0';

    if (!parse_u8_token(
            line, length, &position, &command->kind) ||
        !parse_u8_token(
            line, length, &position, &command->board_id) ||
        !parse_u8_token(
            line, length, &position, &command->system_id) ||
        !parse_u8_token(
            line, length, &position, &command->method_id)) {
        return false;
    }
    while ((position < length) && (line[position] == ' ')) {
        ++position;
    }
    return position == length;
}

bool fc_start_command_matches(
    const fc_start_command_t *command,
    uint8_t kind,
    uint8_t board_id,
    uint8_t system_id,
    uint8_t method_id)
{
    return
        (command != NULL) &&
        (command->kind == kind) &&
        (command->board_id == board_id) &&
        (command->system_id == system_id) &&
        (command->method_id == method_id);
}

size_t fc_format_ready_line(
    char *output,
    size_t capacity,
    const char *request_id)
{
    static const char prefix[] = "READY ";
    size_t request_length;
    size_t total;

    if ((output == NULL) || (request_id == NULL)) {
        return 0U;
    }
    request_length = strlen(request_id);
    if ((request_length == 0U) ||
        (request_length > FC_START_REQUEST_ID_MAX)) {
        return 0U;
    }
    for (size_t index = 0U; index < request_length; ++index) {
        if (!request_character_is_valid(request_id[index])) {
            return 0U;
        }
    }
    total = (sizeof(prefix) - 1U) + request_length + 1U;
    if (capacity < total) {
        return 0U;
    }
    memcpy(output, prefix, sizeof(prefix) - 1U);
    memcpy(
        &output[sizeof(prefix) - 1U],
        request_id,
        request_length);
    output[total - 1U] = '\n';
    return total;
}
