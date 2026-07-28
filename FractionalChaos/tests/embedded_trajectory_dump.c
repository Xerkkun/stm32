#include "fractional_chaos.h"
#include "fractional_chaos_fixed.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static fc_workspace_t float_workspace;
static fc_fixed_workspace_t fixed_workspace;

static uint32_t float_word(float value)
{
    uint32_t word = 0u;
    memcpy(&word, &value, sizeof(word));
    return word;
}

static int dump_float_cell(
    uint32_t system,
    uint32_t method,
    uint32_t steps)
{
    fc_config_t config;
    fc_solver_t solver;
    fc_vec3f_t state;
    uint32_t step;

    if (fc_config_from_manifest(
            (fc_system_t)system,
            (fc_method_t)method,
            &config) != FC_OK) {
        return 1;
    }
    if (fc_solver_init(
            &solver,
            (method == (uint32_t)FC_METHOD_M2SFRK) ?
                NULL :
                &float_workspace,
            &config) != FC_OK) {
        return 1;
    }
    state = *fc_solver_state(&solver);
    printf(
        "%" PRIu32 ",%" PRIu32 ",float32,0,"
        "%.9g,%.9g,%.9g,"
        "%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",0,0,0,0\n",
        system,
        method,
        (double)state.v[0],
        (double)state.v[1],
        (double)state.v[2],
        float_word(state.v[0]),
        float_word(state.v[1]),
        float_word(state.v[2]));

    for (step = 1u; step <= steps; ++step) {
        const fc_status_t status = fc_solver_step(&solver, &state);
        if (status != FC_OK) {
            return 1;
        }
        printf(
            "%" PRIu32 ",%" PRIu32 ",float32,%" PRIu32 ","
            "%.9g,%.9g,%.9g,"
            "%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%d,0,0,0\n",
            system,
            method,
            step,
            (double)state.v[0],
            (double)state.v[1],
            (double)state.v[2],
            float_word(state.v[0]),
            float_word(state.v[1]),
            float_word(state.v[2]),
            (int)status);
    }
    return 0;
}

static int dump_fixed_cell(
    uint32_t system,
    uint32_t method,
    uint32_t steps)
{
    fc_fixed_config_t config;
    fc_fixed_solver_t solver;
    fc_fixed_vec3_t state;
    uint32_t step;

    if (fc_fixed_config_from_manifest(
            (fc_fixed_system_t)system,
            (fc_fixed_method_t)method,
            &config) != FC_FIXED_OK) {
        return 1;
    }
    if (fc_fixed_solver_init(
            &solver,
            (method == (uint32_t)FC_FIXED_METHOD_M2SFRK) ?
                NULL :
                &fixed_workspace,
            &config) != FC_FIXED_OK) {
        return 1;
    }
    state = *fc_fixed_solver_state(&solver);
    printf(
        "%" PRIu32 ",%" PRIu32 ",fixed_q14_q30,0,"
        "%.17g,%.17g,%.17g,"
        "%" PRId32 ",%" PRId32 ",%" PRId32 ",0,0,0,0\n",
        system,
        method,
        fc_fixed_to_double(state.v[0]),
        fc_fixed_to_double(state.v[1]),
        fc_fixed_to_double(state.v[2]),
        state.v[0],
        state.v[1],
        state.v[2]);

    for (step = 1u; step <= steps; ++step) {
        const fc_fixed_status_t status =
            fc_fixed_solver_step(&solver, &state);
        const fc_fixed_diagnostics_t *diagnostics =
            fc_fixed_solver_diagnostics(&solver);
        if ((status != FC_FIXED_OK) || (diagnostics == NULL)) {
            return 1;
        }
        printf(
            "%" PRIu32 ",%" PRIu32 ",fixed_q14_q30,%" PRIu32 ","
            "%.17g,%.17g,%.17g,"
            "%" PRId32 ",%" PRId32 ",%" PRId32 ",%d,"
            "%" PRIu64 ",%" PRIu64 ",%" PRIu64 "\n",
            system,
            method,
            step,
            fc_fixed_to_double(state.v[0]),
            fc_fixed_to_double(state.v[1]),
            fc_fixed_to_double(state.v[2]),
            state.v[0],
            state.v[1],
            state.v[2],
            (int)status,
            diagnostics->saturation_count,
            diagnostics->coefficient_saturation_count,
            diagnostics->zeroed_nonzero_coefficient_count);
    }
    return 0;
}

int main(int argc, char **argv)
{
    uint32_t steps = 200u;
    uint32_t system;
    uint32_t method;

    if (argc == 2) {
        char *end = NULL;
        const unsigned long parsed = strtoul(argv[1], &end, 10);
        if ((end == argv[1]) || (*end != '\0') ||
            (parsed == 0u) || (parsed > UINT32_MAX)) {
            return 2;
        }
        steps = (uint32_t)parsed;
    } else if (argc != 1) {
        return 2;
    }

    puts("system,method,representation,step,x,y,z,"
         "x_raw,y_raw,z_raw,status,state_saturations,"
         "coefficient_saturations,zeroed_nonzero_coefficients");
    for (system = 0u; system < FC_SYSTEM_COUNT; ++system) {
        for (method = 0u; method < 3u; ++method) {
            if (dump_float_cell(system, method, steps) != 0) {
                return 3;
            }
            if (dump_fixed_cell(system, method, steps) != 0) {
                return 4;
            }
        }
    }
    return 0;
}
