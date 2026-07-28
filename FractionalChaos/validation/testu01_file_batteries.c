/*
 * Finite-file adapter for the TestU01 1.2.3 Rabbit and Alphabit batteries.
 *
 * The exact bit count is passed to TestU01.  This program rejects requests
 * larger than the file and never concatenates, repeats, or cycles input data
 * to reach a requested length.
 */

#include "bbattery.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>


static int usage(const char *program)
{
    fprintf(
        stderr,
        "usage: %s <rabbit|alphabit> <input.bin> <bit_count>\n",
        program);
    return 2;
}


static int file_size_bytes(const char *path, uint64_t *size)
{
    FILE *stream = fopen(path, "rb");
    long position;

    if (stream == NULL) {
        fprintf(stderr, "cannot open input file: %s\n", path);
        return 0;
    }
    if (fseek(stream, 0L, SEEK_END) != 0) {
        fclose(stream);
        fprintf(stderr, "cannot seek input file: %s\n", path);
        return 0;
    }
    position = ftell(stream);
    fclose(stream);
    if (position < 0L) {
        fprintf(stderr, "cannot measure input file: %s\n", path);
        return 0;
    }
    *size = (uint64_t)position;
    return 1;
}


int main(int argc, char **argv)
{
    const char *battery;
    const char *path;
    char *end = NULL;
    unsigned long long parsed_bits;
    uint64_t bytes;
    uint64_t bits;

    if (argc != 4) {
        return usage(argv[0]);
    }
    battery = argv[1];
    path = argv[2];
    if (strcmp(battery, "rabbit") != 0
        && strcmp(battery, "alphabit") != 0) {
        return usage(argv[0]);
    }

    errno = 0;
    parsed_bits = strtoull(argv[3], &end, 10);
    if (errno != 0 || end == argv[3] || *end != '\0' || parsed_bits == 0ULL) {
        fprintf(stderr, "invalid positive bit count: %s\n", argv[3]);
        return 2;
    }
    bits = (uint64_t)parsed_bits;
    if (!file_size_bytes(path, &bytes)) {
        return 2;
    }
    if (bytes > UINT64_MAX / 8U || bits > bytes * 8U) {
        fprintf(
            stderr,
            "requested bits exceed finite input: requested=%llu available=%llu\n",
            (unsigned long long)bits,
            (unsigned long long)(bytes * 8U));
        return 2;
    }

    printf("finite_file_adapter=testu01_file_batteries_v1\n");
    printf("testu01_version=1.2.3\n");
    printf("battery=%s\n", battery);
    printf("input=%s\n", path);
    printf("input_bytes=%llu\n", (unsigned long long)bytes);
    printf("requested_bits=%llu\n", (unsigned long long)bits);
    printf("input_repetition_or_recycling=disabled\n\n");
    fflush(stdout);

    if (strcmp(battery, "rabbit") == 0) {
        bbattery_RabbitFile((char *)path, (double)bits);
    } else {
        bbattery_AlphabitFile((char *)path, (double)bits);
    }
    return 0;
}
