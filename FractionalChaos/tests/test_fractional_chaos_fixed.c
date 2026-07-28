#include "fractional_chaos_fixed.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>

static int failures = 0;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            fprintf(stderr, "FAIL %s:%d: %s\n",                             \
                    __FILE__, __LINE__, #condition);                         \
            ++failures;                                                      \
        }                                                                    \
    } while (0)

#define CHECK_STATUS(expression) CHECK((expression) == FC_FIXED_OK)

static void test_arithmetic_contract(void)
{
    fc_fixed_arithmetic_t arithmetic = {0u};
    fc_fixed_t value = 0;

    CHECK_STATUS(fc_fixed_from_double(
        0.5 / (double)FC_FIXED_SCALE,
        &arithmetic,
        &value));
    CHECK(value == 1);
    CHECK_STATUS(fc_fixed_from_double(
        -0.5 / (double)FC_FIXED_SCALE,
        &arithmetic,
        &value));
    CHECK(value == -1);

    CHECK(fc_fixed_mul(1, 8192, &arithmetic) == 1);
    CHECK(fc_fixed_mul(-1, 8192, &arithmetic) == -1);
    CHECK(fc_fixed_mul(8192, 8192, &arithmetic) == 4096);
    CHECK(fc_fixed_mul_coefficient(
        (fc_fixed_coefficient_t)
            (FC_FIXED_COEFFICIENT_SCALE / 2),
        1,
        &arithmetic) == 1);
    CHECK(fc_fixed_mul_coefficient(
        (fc_fixed_coefficient_t)
            -(FC_FIXED_COEFFICIENT_SCALE / 2),
        1,
        &arithmetic) == -1);
    CHECK(fc_fixed_add(
        (fc_fixed_t)FC_FIXED_RAW_MAX,
        1,
        &arithmetic) == (fc_fixed_t)FC_FIXED_RAW_MAX);
    CHECK(fc_fixed_sub(
        (fc_fixed_t)FC_FIXED_RAW_MIN,
        1,
        &arithmetic) == (fc_fixed_t)FC_FIXED_RAW_MIN);
    CHECK(fc_fixed_mul(
        (fc_fixed_t)FC_FIXED_RAW_MAX,
        (fc_fixed_t)(2 * FC_FIXED_SCALE),
        &arithmetic) == (fc_fixed_t)FC_FIXED_RAW_MAX);
    CHECK(arithmetic.saturation_count == 3u);

    CHECK_STATUS(fc_fixed_from_double(1.25, &arithmetic, &value));
    CHECK(value == 20480);
    CHECK(fabs(fc_fixed_to_double(value) - 1.25) < 1.0e-15);
    CHECK(fc_fixed_from_double(
        INFINITY, &arithmetic, &value) == FC_FIXED_ERR_NONFINITE);
    CHECK(fc_fixed_from_double(
        0.0, &arithmetic, NULL) == FC_FIXED_ERR_NULL);
}

static void test_manifest_and_validation(void)
{
    fc_fixed_config_t config;
    fc_fixed_solver_t solver;

    CHECK_STATUS(fc_fixed_config_from_manifest(
        FC_FIXED_SYSTEM_LORENZ,
        FC_FIXED_METHOD_EFORK3,
        &config));
    CHECK(config.q == 0.995);
    CHECK(config.h == 0.005);
    CHECK(config.memory_length == 2000u);
    CHECK(config.parameters[0] == 10.0);
    CHECK(config.parameters[1] == 28.0);
    CHECK(config.initial_state[0] == 0.1);

    CHECK(fc_fixed_config_from_manifest(
        (fc_fixed_system_t)99,
        FC_FIXED_METHOD_EFORK3,
        &config) == FC_FIXED_ERR_CONFIG);
    CHECK(fc_fixed_config_from_manifest(
        FC_FIXED_SYSTEM_LORENZ,
        (fc_fixed_method_t)99,
        &config) == FC_FIXED_ERR_METHOD);
    CHECK(fc_fixed_config_from_manifest(
        FC_FIXED_SYSTEM_LORENZ,
        FC_FIXED_METHOD_EFORK3,
        NULL) == FC_FIXED_ERR_NULL);

    CHECK_STATUS(fc_fixed_config_from_manifest(
        FC_FIXED_SYSTEM_LORENZ,
        FC_FIXED_METHOD_EFORK3,
        &config));
    CHECK(fc_fixed_solver_init(
        &solver, NULL, &config) == FC_FIXED_ERR_WORKSPACE);
    config.q = 0.0;
    CHECK(fc_fixed_solver_init(
        &solver, NULL, &config) == FC_FIXED_ERR_CONFIG);
    CHECK(fc_fixed_solver_step(NULL, NULL) == FC_FIXED_ERR_NULL);
    CHECK(fc_fixed_solver_reset(&solver) ==
          FC_FIXED_ERR_NOT_INITIALIZED);
}

static void test_rhs_lorenz(void)
{
    fc_fixed_arithmetic_t arithmetic = {0u};
    fc_fixed_vec3_t parameters;
    fc_fixed_vec3_t state;
    fc_fixed_vec3_t derivative;

    CHECK_STATUS(fc_fixed_from_double(
        10.0, &arithmetic, &parameters.v[0]));
    CHECK_STATUS(fc_fixed_from_double(
        28.0, &arithmetic, &parameters.v[1]));
    CHECK_STATUS(fc_fixed_from_double(
        8.0 / 3.0, &arithmetic, &parameters.v[2]));
    CHECK_STATUS(fc_fixed_from_double(
        1.0, &arithmetic, &state.v[0]));
    CHECK_STATUS(fc_fixed_from_double(
        2.0, &arithmetic, &state.v[1]));
    CHECK_STATUS(fc_fixed_from_double(
        3.0, &arithmetic, &state.v[2]));

    CHECK_STATUS(fc_fixed_rhs(
        FC_FIXED_SYSTEM_LORENZ,
        &parameters,
        &state,
        &arithmetic,
        &derivative));
    CHECK(derivative.v[0] == (10 * FC_FIXED_SCALE));
    CHECK(derivative.v[1] == (23 * FC_FIXED_SCALE));
    CHECK(derivative.v[2] == -98305);
    CHECK(arithmetic.saturation_count == 0u);

    CHECK(fc_fixed_rhs(
        (fc_fixed_system_t)99,
        &parameters,
        &state,
        &arithmetic,
        &derivative) == FC_FIXED_ERR_CONFIG);
    CHECK(fc_fixed_rhs(
        FC_FIXED_SYSTEM_LORENZ,
        NULL,
        &state,
        &arithmetic,
        &derivative) == FC_FIXED_ERR_NULL);
}

static void test_m2_q_one_midpoint(void)
{
    fc_fixed_config_t config = {
        FC_FIXED_SYSTEM_LORENZ,
        FC_FIXED_METHOD_M2SFRK,
        1.0,
        0.125,
        8u,
        {10.0, 28.0, 8.0 / 3.0},
        {0.25, -0.5, 0.75}
    };
    fc_fixed_solver_t solver;
    fc_fixed_vec3_t state;
    const fc_fixed_vec3_t expected = {
        {7696, -20372, 8936}
    };
    uint32_t component;

    CHECK_STATUS(fc_fixed_solver_init(&solver, NULL, &config));
    CHECK(solver.m2sfrk.c2 == 134217728);
    CHECK(solver.m2sfrk.c4 == 67108864);
    CHECK_STATUS(fc_fixed_solver_step(&solver, &state));
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        CHECK(state.v[component] == expected.v[component]);
    }
    CHECK(fc_fixed_solver_diagnostics(&solver)->steps_completed == 1u);
    CHECK(fc_fixed_solver_diagnostics(&solver)->active_memory_terms == 0u);
    CHECK(fc_fixed_solver_diagnostics(&solver)->saturation_count == 0u);
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          coefficient_saturation_count == 0u);
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          zeroed_nonzero_coefficient_count == 0u);
}

static void test_coefficient_diagnostics(void)
{
    fc_fixed_config_t config = {
        FC_FIXED_SYSTEM_LORENZ,
        FC_FIXED_METHOD_M2SFRK,
        1.0,
        1.0e-12,
        1u,
        {0.0, 0.0, 0.0},
        {0.0, 0.0, 0.0}
    };
    fc_fixed_solver_t solver;

    CHECK_STATUS(fc_fixed_solver_init(&solver, NULL, &config));
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          zeroed_nonzero_coefficient_count == 2u);
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          coefficient_saturation_count == 0u);

    config.h = 3.0;
    CHECK_STATUS(fc_fixed_solver_init(&solver, NULL, &config));
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          coefficient_saturation_count == 1u);
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          zeroed_nonzero_coefficient_count == 0u);

    config.h = 0.125;
    config.parameters[0] = 20000.0;
    config.initial_state[0] = -20000.0;
    CHECK_STATUS(fc_fixed_solver_init(&solver, NULL, &config));
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          saturation_count == 2u);
    CHECK_STATUS(fc_fixed_solver_reset(&solver));
    CHECK(fc_fixed_solver_diagnostics(&solver)->
          saturation_count == 2u);
}

static void test_short_memory_ring_wrap(void)
{
    static fc_fixed_workspace_t workspace;
    static const fc_fixed_t expected[2][FC_FIXED_STATE_DIMENSION] = {
        {1948, 3414, 1491},
        {2300, 4570, 1414}
    };
    fc_fixed_config_t config;
    fc_fixed_solver_t solver;
    fc_fixed_vec3_t state;
    uint32_t method;
    uint32_t step;
    uint32_t component;

    for (method = 0u; method < 2u; ++method) {
        CHECK_STATUS(fc_fixed_config_from_manifest(
            FC_FIXED_SYSTEM_LORENZ,
            (fc_fixed_method_t)method,
            &config));
        config.memory_length = 4u;
        CHECK_STATUS(fc_fixed_solver_init(
            &solver, &workspace, &config));
        for (step = 0u; step < 12u; ++step) {
            CHECK_STATUS(fc_fixed_solver_step(&solver, &state));
        }
        for (component = 0u;
             component < FC_FIXED_STATE_DIMENSION;
             ++component) {
            CHECK(state.v[component] ==
                  expected[method][component]);
        }
        CHECK(fc_fixed_solver_diagnostics(&solver)->
              active_memory_terms == 4u);
        CHECK(fc_fixed_solver_diagnostics(&solver)->
              saturation_count == 0u);
    }
}

static const fc_fixed_t GOLDEN_32
    [FC_FIXED_SYSTEM_COUNT][3][FC_FIXED_STATE_DIMENSION] = {
    {
        {3654, 7747, 1319},
        {6927, 15069, 1307},
        {7542, 16401, 1340}
    },
    {
        {2336, 26840, 883},
        {-957, 27541, 734},
        {-2021, 27718, 679}
    },
    {
        {47922, 80290, 6158},
        {136682, 226630, 38516},
        {493214, 547510, 665170}
    }
};

static void test_nine_candidate_golden_states(void)
{
    static fc_fixed_workspace_t workspace;
    fc_fixed_solver_t solver;
    fc_fixed_config_t config;
    fc_fixed_vec3_t state;
    uint32_t system;
    uint32_t method;
    uint32_t step;
    uint32_t component;

    for (system = 0u; system < FC_FIXED_SYSTEM_COUNT; ++system) {
        for (method = 0u; method < 3u; ++method) {
            fc_fixed_workspace_t *selected_workspace =
                (method == (uint32_t)FC_FIXED_METHOD_M2SFRK) ?
                NULL : &workspace;

            CHECK_STATUS(fc_fixed_config_from_manifest(
                (fc_fixed_system_t)system,
                (fc_fixed_method_t)method,
                &config));
            CHECK_STATUS(fc_fixed_solver_init(
                &solver, selected_workspace, &config));
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  coefficient_saturation_count == 0u);
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  zeroed_nonzero_coefficient_count == 0u);
            if (method ==
                (uint32_t)FC_FIXED_METHOD_EFORK3) {
                uint32_t stage;
                uint32_t lag;
                for (stage = 0u; stage < 3u; ++stage) {
                    for (lag = 0u;
                         lag < config.memory_length;
                         ++lag) {
                        CHECK(workspace.efork.weights[stage][lag] != 0);
                    }
                }
            } else if (method ==
                       (uint32_t)FC_FIXED_METHOD_GL_CAPUTO) {
                uint32_t index;
                for (index = 0u;
                     index <= config.memory_length;
                     ++index) {
                    CHECK(workspace.gl.weights[index] != 0);
                }
            }
            for (step = 0u; step < 32u; ++step) {
                CHECK_STATUS(fc_fixed_solver_step(&solver, &state));
            }
            for (component = 0u;
                 component < FC_FIXED_STATE_DIMENSION;
                 ++component) {
                CHECK(state.v[component] ==
                      GOLDEN_32[system][method][component]);
            }
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  steps_completed == 32u);
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  active_memory_terms ==
                  ((method ==
                    (uint32_t)FC_FIXED_METHOD_M2SFRK) ? 0u : 31u));
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  saturation_count == 0u);
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  coefficient_saturation_count == 0u);
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  zeroed_nonzero_coefficient_count == 0u);

            CHECK_STATUS(fc_fixed_solver_reset(&solver));
            CHECK_STATUS(fc_fixed_solver_step(&solver, &state));
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  steps_completed == 1u);
            CHECK(fc_fixed_solver_diagnostics(&solver)->
                  saturation_count == 0u);
        }
    }
}

int main(void)
{
    test_arithmetic_contract();
    test_manifest_and_validation();
    test_rhs_lorenz();
    test_m2_q_one_midpoint();
    test_coefficient_diagnostics();
    test_short_memory_ring_wrap();
    test_nine_candidate_golden_states();

    if (failures != 0) {
        fprintf(stderr, "%d test assertion(s) failed\n", failures);
        return 1;
    }
    puts("fractional_chaos_fixed: all host tests passed");
    return 0;
}
