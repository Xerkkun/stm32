#include "fractional_chaos.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t float_word(float value)
{
    uint32_t word = 0u;
    memcpy(&word, &value, sizeof(word));
    return word;
}

int main(int argc, char **argv)
{
    static fc_workspace_t workspace;
    fc_solver_t solver;
    fc_config_t config;
    fc_vec3f_t state;
    uint32_t system;
    uint32_t method;
    uint32_t step;
    uint32_t steps = 16u;

    if (argc == 2) {
        unsigned int parsed = 0u;
        if (sscanf(argv[1], "%u", &parsed) != 1) {
            return 2;
        }
        steps = (uint32_t)parsed;
    }

    for (system = 0u; system < FC_SYSTEM_COUNT; ++system) {
        for (method = 0u; method < 2u; ++method) {
            if (fc_config_from_manifest(
                    (fc_system_t)system,
                    (fc_method_t)method,
                    &config) != FC_OK) {
                return 3;
            }
            if (fc_solver_init(&solver, &workspace, &config) != FC_OK) {
                return 4;
            }
            for (step = 0u; step < steps; ++step) {
                if (fc_solver_step(&solver, &state) != FC_OK) {
                    return 5;
                }
            }
            printf(
                "%u,%u,%u,%.9g,%.9g,%.9g,%08x,%08x,%08x\n",
                system,
                method,
                steps,
                (double)state.v[0],
                (double)state.v[1],
                (double)state.v[2],
                float_word(state.v[0]),
                float_word(state.v[1]),
                float_word(state.v[2]));
        }
    }
    return 0;
}
