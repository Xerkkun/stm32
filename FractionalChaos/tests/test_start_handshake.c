#include "fc_start_handshake.h"

#ifdef NDEBUG
#undef NDEBUG
#endif
#include <assert.h>
#include <stdint.h>
#include <string.h>

static void test_valid_primary_command(void)
{
    static const char line[] =
        "START 0123456789abcdef 4 2 4 2\r\n";
    fc_start_command_t command;

    assert(fc_parse_start_command(
        line,
        sizeof(line) - 1U,
        &command));
    assert(strcmp(command.request_id, "0123456789abcdef") == 0);
    assert(fc_start_command_matches(&command, 4U, 2U, 4U, 2U));
    assert(!fc_start_command_matches(&command, 4U, 1U, 4U, 2U));
}

static void test_invalid_commands(void)
{
    fc_start_command_t command;

    assert(!fc_parse_start_command(
        "START bad/id 4 1 2 0\n",
        strlen("START bad/id 4 1 2 0\n"),
        &command));
    assert(!fc_parse_start_command(
        "START id 256 1 2 0\n",
        strlen("START id 256 1 2 0\n"),
        &command));
    assert(!fc_parse_start_command(
        "START id 4 1 2\n",
        strlen("START id 4 1 2\n"),
        &command));
    assert(!fc_parse_start_command(
        "START id 4 1 2 0 trailing\n",
        strlen("START id 4 1 2 0 trailing\n"),
        &command));
}

static void test_ready_format(void)
{
    char output[FC_READY_LINE_MAX] = {0};
    const size_t length = fc_format_ready_line(
        output,
        sizeof(output),
        "0123456789abcdef");

    assert(length == strlen("READY 0123456789abcdef\n"));
    assert(
        memcmp(
            output,
            "READY 0123456789abcdef\n",
            length) == 0);
    assert(fc_format_ready_line(output, 4U, "id") == 0U);
}

int main(void)
{
    test_valid_primary_command();
    test_invalid_commands();
    test_ready_format();
    return 0;
}
