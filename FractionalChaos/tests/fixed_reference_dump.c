#include "fractional_chaos_fixed.h"

#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

static fc_fixed_workspace_t workspace;

static int dump_cell(uint32_t system, uint32_t method)
{
    fc_fixed_config_t config;
    fc_fixed_solver_t solver;
    fc_fixed_vec3_t state;
    fc_fixed_workspace_t *selected_workspace =
        (method == (uint32_t)FC_FIXED_METHOD_M2SFRK) ?
        NULL : &workspace;
    fc_fixed_status_t status;
    uint32_t step;

    status = fc_fixed_config_from_manifest(
        (fc_fixed_system_t)system,
        (fc_fixed_method_t)method,
        &config);
    if (status != FC_FIXED_OK) {
        fprintf(stderr,
                "config failed for system=%" PRIu32
                " method=%" PRIu32 ": %d\n",
                system, method, (int)status);
        return 1;
    }

    status = fc_fixed_solver_init(
        &solver, selected_workspace, &config);
    if (status != FC_FIXED_OK) {
        fprintf(stderr,
                "init failed for system=%" PRIu32
                " method=%" PRIu32 ": %d\n",
                system, method, (int)status);
        return 1;
    }

    for (step = 1u; step <= 32u; ++step) {
        status = fc_fixed_solver_step(&solver, &state);
        if (status != FC_FIXED_OK) {
            fprintf(stderr,
                    "step failed for system=%" PRIu32
                    " method=%" PRIu32
                    " step=%" PRIu32 ": %d\n",
                    system, method, step, (int)status);
            return 1;
        }
        if ((step == 1u) || (step == 32u)) {
            const fc_fixed_diagnostics_t *diagnostics =
                fc_fixed_solver_diagnostics(&solver);

            if (diagnostics == NULL) {
                fputs("missing diagnostics\n", stderr);
                return 1;
            }
            printf(
                "%" PRIu32 ",%" PRIu32 ",%" PRIu32
                ",%" PRId32 ",%" PRId32 ",%" PRId32
                ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 "\n",
                system,
                method,
                step,
                state.v[0],
                state.v[1],
                state.v[2],
                diagnostics->saturation_count,
                diagnostics->coefficient_saturation_count,
                diagnostics->zeroed_nonzero_coefficient_count);
        }
    }
    return 0;
}

int main(void)
{
    uint32_t system;
    uint32_t method;

    puts("system,method,step,x_raw,y_raw,z_raw,"
         "state_saturations,coefficient_saturations,"
         "zeroed_nonzero_coefficients");
    for (system = 0u; system < FC_FIXED_SYSTEM_COUNT; ++system) {
        for (method = 0u; method < 3u; ++method) {
            if (dump_cell(system, method) != 0) {
                return 1;
            }
        }
    }
    return 0;
}
