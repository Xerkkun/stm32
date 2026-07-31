#include "fractional_chaos.h"

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;

#define CHECK(condition)                                                     \
    do {                                                                     \
        if (!(condition)) {                                                  \
            fprintf(stderr, "FAIL %s:%d: %s\n",                             \
                    __FILE__, __LINE__, #condition);                         \
            ++failures;                                                      \
        }                                                                    \
    } while (0)

#define CHECK_STATUS(expression) CHECK((expression) == FC_OK)

static uint32_t float_word(float value)
{
    uint32_t word = 0u;
    memcpy(&word, &value, sizeof(word));
    return word;
}

static int close_float(float actual, float expected, float tolerance)
{
    const float scale = fmaxf(1.0f, fabsf(expected));
    return fabsf(actual - expected) <= (tolerance * scale);
}

static void test_selected_manifest_hash(void)
{
    uint32_t index;

    CHECK(strlen(FC_SELECTED_SYSTEM_MANIFEST_SHA256_TEXT) == 64u);
    for (index = 0u; index < 64u; ++index) {
        const char value =
            FC_SELECTED_SYSTEM_MANIFEST_SHA256_TEXT[index];
        CHECK(((value >= '0') && (value <= '9')) ||
              ((value >= 'a') && (value <= 'f')));
    }
}

static void test_manifests(void)
{
    const fc_manifest_t *lorenz = fc_manifest(FC_SYSTEM_LORENZ);
    const fc_manifest_t *rossler = fc_manifest(FC_SYSTEM_ROSSLER);
    const fc_manifest_t *chen = fc_manifest(FC_SYSTEM_CHEN);
    const fc_manifest_t *liu = fc_manifest(FC_SYSTEM_LIU);
    const fc_manifest_t *hammouch =
        fc_manifest(FC_SYSTEM_HAMMOUCH_MEKKAOUI);

    CHECK(lorenz != NULL);
    CHECK(rossler != NULL);
    CHECK(chen != NULL);
    CHECK(liu != NULL);
    CHECK(hammouch != NULL);
    CHECK(fc_manifest((fc_system_t)99) == NULL);

    CHECK(lorenz->memory_length == 2000u);
    CHECK(float_word(lorenz->q) == float_word(0.995f));
    CHECK(float_word(lorenz->h) == float_word(0.005f));
    CHECK(float_word(lorenz->initial_state.v[0]) == 0x3dcccccdu);

    CHECK(rossler->memory_length == 1000u);
    CHECK(float_word(rossler->q) == float_word(0.9877f));
    CHECK(float_word(rossler->h) == float_word(0.010f));
    CHECK(float_word(rossler->memory_seconds) == float_word(10.0f));
    CHECK(float_word(rossler->parameters[0]) == float_word(0.2f));
    CHECK(float_word(rossler->parameters[1]) == float_word(0.2f));
    CHECK(float_word(rossler->parameters[2]) == float_word(5.7f));
    CHECK(float_word(rossler->initial_state.v[0]) == 0x3f800000u);
    CHECK(float_word(rossler->initial_state.v[1]) == 0x00000000u);
    CHECK(float_word(rossler->initial_state.v[2]) == 0x00000000u);

    CHECK(chen->memory_length == 2000u);
    CHECK(float_word(chen->q) == float_word(0.900f));
    CHECK(float_word(chen->h) == float_word(0.005f));

    CHECK(liu->memory_length == 1000u);
    CHECK(float_word(liu->q) == float_word(0.920f));
    CHECK(float_word(liu->h) == float_word(0.010f));
    CHECK(float_word(liu->parameters[0]) == float_word(1.0f));
    CHECK(float_word(liu->parameters[1]) == float_word(2.5f));
    CHECK(float_word(liu->parameters[2]) == float_word(5.0f));
    CHECK(float_word(liu->parameters[3]) == float_word(1.0f));
    CHECK(float_word(liu->parameters[4]) == float_word(4.0f));
    CHECK(float_word(liu->parameters[5]) == float_word(4.0f));
    CHECK(float_word(liu->initial_state.v[0]) == float_word(0.2f));
    CHECK(float_word(liu->initial_state.v[1]) == 0u);
    CHECK(float_word(liu->initial_state.v[2]) == float_word(0.5f));

    CHECK(hammouch->memory_length == 1000u);
    CHECK(float_word(hammouch->q) == float_word(0.980f));
    CHECK(float_word(hammouch->h) == float_word(0.010f));
    CHECK(float_word(hammouch->initial_state.v[0]) == float_word(0.7f));
    CHECK(float_word(hammouch->initial_state.v[1]) == float_word(0.1f));
    CHECK(float_word(hammouch->initial_state.v[2]) == 0u);

    CHECK(fc_active_workspace_bytes(
              FC_METHOD_EFORK3, 2000u) == 48000u);
    CHECK(fc_active_workspace_bytes(
              FC_METHOD_GL_CAPUTO, 2000u) == 32004u);
    CHECK(fc_active_workspace_bytes(
              FC_METHOD_EFORK3, 1000u) == 24000u);
    CHECK(fc_active_workspace_bytes(
              FC_METHOD_GL_CAPUTO, 1000u) == 16004u);
    CHECK(fc_active_workspace_bytes(
              FC_METHOD_M2SFRK, 2000u) == 0u);
}

static void test_rhs(void)
{
    fc_vec3f_t state = {{1.0f, 2.0f, 3.0f}};
    fc_vec3f_t derivative;

    CHECK_STATUS(fc_rhs(
        FC_SYSTEM_LORENZ,
        FC_MANIFESTS[FC_SYSTEM_LORENZ].parameters,
        &state,
        &derivative));
    CHECK(close_float(derivative.v[0], 10.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[1], 23.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[2], -6.0f, 1.0e-6f));

    CHECK_STATUS(fc_rhs(
        FC_SYSTEM_ROSSLER,
        FC_MANIFESTS[FC_SYSTEM_ROSSLER].parameters,
        &state,
        &derivative));
    CHECK(close_float(derivative.v[0], -5.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[1], 1.4f, 1.0e-6f));
    CHECK(close_float(derivative.v[2], -13.9f, 1.0e-6f));

    CHECK_STATUS(fc_rhs(
        FC_SYSTEM_CHEN,
        FC_MANIFESTS[FC_SYSTEM_CHEN].parameters,
        &state,
        &derivative));
    CHECK(close_float(derivative.v[0], 35.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[1], 46.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[2], -7.0f, 1.0e-6f));

    CHECK_STATUS(fc_rhs(
        FC_SYSTEM_LIU,
        FC_MANIFESTS[FC_SYSTEM_LIU].parameters,
        &state,
        &derivative));
    CHECK(close_float(derivative.v[0], -5.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[1], -7.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[2], -7.0f, 1.0e-6f));

    CHECK_STATUS(fc_rhs(
        FC_SYSTEM_HAMMOUCH_MEKKAOUI,
        FC_MANIFESTS[FC_SYSTEM_HAMMOUCH_MEKKAOUI].parameters,
        &state,
        &derivative));
    CHECK(close_float(derivative.v[0], -6.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[1], -15.0f, 1.0e-6f));
    CHECK(close_float(derivative.v[2], -7.0f, 1.0e-6f));
}

static void test_zero_equilibrium(fc_method_t method)
{
    static fc_workspace_t workspace;
    fc_solver_t solver;
    fc_config_t config;
    fc_vec3f_t state;
    uint32_t step;

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_LORENZ, method, &config));
    config.memory_length = 17u;
    config.initial_state =
        (fc_vec3f_t){{0.0f, 0.0f, 0.0f}};

    CHECK_STATUS(fc_solver_init(&solver, &workspace, &config));
    for (step = 0u; step < 100u; ++step) {
        CHECK_STATUS(fc_solver_step(&solver, &state));
        CHECK(float_word(state.v[0]) == 0u);
        CHECK(float_word(state.v[1]) == 0u);
        CHECK(float_word(state.v[2]) == 0u);
    }
}

static void test_gl_first_step(void)
{
    static fc_workspace_t workspace;
    fc_solver_t solver;
    fc_config_t config;
    fc_vec3f_t derivative;
    fc_vec3f_t expected;
    fc_vec3f_t actual;
    uint32_t component;

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_LORENZ, FC_METHOD_GL_CAPUTO, &config));
    CHECK_STATUS(fc_solver_init(&solver, &workspace, &config));
    CHECK_STATUS(fc_rhs(
        config.system,
        config.parameters,
        &config.initial_state,
        &derivative));

    for (component = 0u; component < 3u; ++component) {
        expected.v[component] = fmaf(
            solver.h_to_q,
            derivative.v[component],
            config.initial_state.v[component]);
    }

    CHECK_STATUS(fc_solver_step(&solver, &actual));
    for (component = 0u; component < 3u; ++component) {
        CHECK(float_word(actual.v[component]) ==
              float_word(expected.v[component]));
    }
}

static uint32_t fnv1a_words(
    const float *values,
    size_t count)
{
    uint32_t hash = 2166136261u;
    size_t index;

    for (index = 0u; index < count; ++index) {
        uint32_t word = float_word(values[index]);
        uint32_t byte;
        for (byte = 0u; byte < 4u; ++byte) {
            hash ^= word & 0xffu;
            hash *= 16777619u;
            word >>= 8u;
        }
    }
    return hash;
}

static void test_weights_and_reset(void)
{
    static fc_workspace_t workspace;
    fc_solver_t solver;
    fc_config_t config;
    fc_efork_coefficients_t coefficients;
    float previous;
    float current;
    uint32_t index;
    uint32_t stage;
    uint32_t hash_before;
    uint32_t hash_after;

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_LORENZ, FC_METHOD_GL_CAPUTO, &config));
    CHECK_STATUS(fc_solver_init(&solver, &workspace, &config));
    CHECK_STATUS(fc_solver_gl_weight(&solver, 0u, &current));
    CHECK(float_word(current) == float_word(1.0f));
    CHECK_STATUS(fc_solver_gl_weight(&solver, 1u, &current));
    CHECK(close_float(current, -config.q, 2.0e-7f));
    previous = fabsf(current);
    for (index = 2u; index <= config.memory_length; ++index) {
        CHECK_STATUS(fc_solver_gl_weight(&solver, index, &current));
        CHECK(current < 0.0f);
        CHECK(fabsf(current) <= previous);
        previous = fabsf(current);
    }
    hash_before = fnv1a_words(
        workspace.storage.gl.weights,
        (size_t)config.memory_length + 1u);
    CHECK_STATUS(fc_solver_reset(&solver));
    hash_after = fnv1a_words(
        workspace.storage.gl.weights,
        (size_t)config.memory_length + 1u);
    CHECK(hash_before == hash_after);

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_CHEN, FC_METHOD_EFORK3, &config));
    CHECK_STATUS(fc_solver_init(&solver, &workspace, &config));
    CHECK_STATUS(fc_solver_efork_coefficients(
        &solver, &coefficients));
    CHECK(close_float(
        coefficients.w1 + coefficients.w2 + coefficients.w3,
        1.0f / tgammaf(1.0f + config.q),
        8.0e-6f));
    CHECK(coefficients.c2 > coefficients.c3);
    CHECK(coefficients.c3 > 0.0f);

    for (stage = 0u; stage < 3u; ++stage) {
        CHECK_STATUS(fc_solver_efork_weight(
            &solver, stage, 0u, &previous));
        CHECK(previous > 0.0f);
        for (index = 1u; index < config.memory_length; ++index) {
            CHECK_STATUS(fc_solver_efork_weight(
                &solver, stage, index, &current));
            CHECK(current > 0.0f);
            CHECK(current <= previous);
            previous = current;
        }
    }
}

static void test_precomputed_table_copy(void)
{
    static fc_workspace_t source_workspace;
    static fc_workspace_t copied_workspace;
    static float gl_weights[18];
    static float efork_weights[3][17];
    fc_solver_t source_solver;
    fc_solver_t copied_solver;
    fc_config_t config;
    fc_precomputed_tables_t tables = {0};
    fc_vec3f_t source_state;
    fc_vec3f_t copied_state;
    uint32_t index;
    uint32_t step;
    uint32_t component;

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_LORENZ, FC_METHOD_GL_CAPUTO, &config));
    config.memory_length = 17u;
    CHECK_STATUS(fc_solver_init(
        &source_solver, &source_workspace, &config));

    for (index = 0u; index <= config.memory_length; ++index) {
        gl_weights[index] =
            source_workspace.storage.gl.weights[index];
    }

    tables.method = config.method;
    tables.q = config.q;
    tables.h = config.h;
    tables.memory_length = config.memory_length;
    tables.h_to_q = source_solver.h_to_q;
    tables.gl_weights = gl_weights;
    tables.table_sha256 =
        "0000000000000000000000000000000000000000000000000000000000000000";
    config.precomputed_tables = &tables;

    CHECK_STATUS(fc_solver_init(
        &copied_solver, &copied_workspace, &config));
    for (index = 0u; index <= config.memory_length; ++index) {
        CHECK(float_word(copied_workspace.storage.gl.weights[index]) ==
              float_word(gl_weights[index]));
    }

    for (step = 0u; step < 40u; ++step) {
        CHECK_STATUS(fc_solver_step(&source_solver, &source_state));
        CHECK_STATUS(fc_solver_step(&copied_solver, &copied_state));
        for (component = 0u; component < 3u; ++component) {
            CHECK(float_word(source_state.v[component]) ==
                  float_word(copied_state.v[component]));
        }
    }

    tables.h = 0.006f;
    CHECK(fc_solver_init(
              &copied_solver, &copied_workspace, &config) ==
          FC_ERR_COEFFICIENT);

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_CHEN, FC_METHOD_EFORK3, &config));
    config.memory_length = 17u;
    CHECK_STATUS(fc_solver_init(
        &source_solver, &source_workspace, &config));

    tables = (fc_precomputed_tables_t){0};
    tables.method = config.method;
    tables.q = config.q;
    tables.h = config.h;
    tables.memory_length = config.memory_length;
    tables.h_to_q = source_solver.h_to_q;
    tables.efork = source_solver.efork;
    tables.table_sha256 =
        "0000000000000000000000000000000000000000000000000000000000000000";
    for (index = 0u; index < config.memory_length; ++index) {
        uint32_t stage;
        for (stage = 0u; stage < 3u; ++stage) {
            efork_weights[stage][index] =
                source_workspace.storage.efork.weights[stage][index];
        }
    }
    tables.efork_weights[0] = efork_weights[0];
    tables.efork_weights[1] = efork_weights[1];
    tables.efork_weights[2] = efork_weights[2];
    config.precomputed_tables = &tables;
    CHECK_STATUS(fc_solver_init(
        &copied_solver, &copied_workspace, &config));

    for (step = 0u; step < 40u; ++step) {
        CHECK_STATUS(fc_solver_step(&source_solver, &source_state));
        CHECK_STATUS(fc_solver_step(&copied_solver, &copied_state));
        for (component = 0u; component < 3u; ++component) {
            CHECK(float_word(source_state.v[component]) ==
                  float_word(copied_state.v[component]));
        }
    }
}

static void reference_gl_step(
    const fc_solver_t *solver,
    fc_vec3f_t *state,
    fc_vec3f_t *current_u,
    fc_vec3f_t history_store[],
    uint32_t completed_steps)
{
    fc_vec3f_t derivative;
    fc_vec3f_t memory = {{0.0f, 0.0f, 0.0f}};
    fc_vec3f_t next_u;
    const uint32_t length = solver->config.memory_length;
    const uint32_t valid =
        (completed_steps < length) ? completed_steps : length;
    const uint32_t oldest = completed_steps - valid;
    uint32_t item;
    uint32_t component;

    for (item = 0u; item < valid; ++item) {
        const uint32_t weight_index = valid - item;
        const float weight =
            solver->workspace->storage.gl.weights[weight_index];
        for (component = 0u; component < 3u; ++component) {
            memory.v[component] = fmaf(
                weight,
                history_store[oldest + item].v[component],
                memory.v[component]);
        }
    }

    CHECK_STATUS(fc_rhs(
        solver->config.system,
        solver->config.parameters,
        state,
        &derivative));
    for (component = 0u; component < 3u; ++component) {
        next_u.v[component] = fmaf(
            solver->h_to_q,
            derivative.v[component],
            -memory.v[component]);
        state->v[component] =
            solver->config.initial_state.v[component] +
            next_u.v[component];
    }
    history_store[completed_steps] = next_u;
    *current_u = next_u;
}

static void reference_efork_step(
    const fc_solver_t *solver,
    fc_vec3f_t *state,
    fc_vec3f_t history_store[],
    uint32_t completed_steps)
{
    fc_vec3f_t memory[3] = {
        {{0.0f, 0.0f, 0.0f}},
        {{0.0f, 0.0f, 0.0f}},
        {{0.0f, 0.0f, 0.0f}}
    };
    fc_vec3f_t derivative;
    fc_vec3f_t k1;
    fc_vec3f_t k2;
    fc_vec3f_t k3;
    fc_vec3f_t stage_state;
    fc_vec3f_t next_state;
    const uint32_t length = solver->config.memory_length;
    const uint32_t valid =
        (completed_steps < length) ? completed_steps : length;
    const uint32_t oldest = completed_steps - valid;
    uint32_t item;
    uint32_t stage;
    uint32_t component;

    for (item = 0u; item < valid; ++item) {
        const uint32_t lag = valid - 1u - item;
        for (stage = 0u; stage < 3u; ++stage) {
            const float weight =
                solver->workspace->storage.efork.weights[stage][lag];
            for (component = 0u; component < 3u; ++component) {
                memory[stage].v[component] = fmaf(
                    weight,
                    history_store[oldest + item].v[component],
                    memory[stage].v[component]);
            }
        }
    }

    CHECK_STATUS(fc_rhs(
        solver->config.system,
        solver->config.parameters,
        state,
        &derivative));
    for (component = 0u; component < 3u; ++component) {
        k1.v[component] = fmaf(
            solver->h_to_q,
            derivative.v[component],
            -memory[0].v[component]);
        stage_state.v[component] = fmaf(
            solver->efork.a21,
            k1.v[component],
            state->v[component]);
    }

    CHECK_STATUS(fc_rhs(
        solver->config.system,
        solver->config.parameters,
        &stage_state,
        &derivative));
    for (component = 0u; component < 3u; ++component) {
        k2.v[component] = fmaf(
            solver->h_to_q,
            derivative.v[component],
            -memory[1].v[component]);
        stage_state.v[component] = fmaf(
            solver->efork.a32,
            k2.v[component],
            fmaf(solver->efork.a31,
                 k1.v[component],
                 state->v[component]));
    }

    CHECK_STATUS(fc_rhs(
        solver->config.system,
        solver->config.parameters,
        &stage_state,
        &derivative));
    for (component = 0u; component < 3u; ++component) {
        float combined;
        k3.v[component] = fmaf(
            solver->h_to_q,
            derivative.v[component],
            -memory[2].v[component]);
        combined = fmaf(
            solver->efork.w1,
            k1.v[component],
            state->v[component]);
        combined = fmaf(
            solver->efork.w2,
            k2.v[component],
            combined);
        next_state.v[component] = fmaf(
            solver->efork.w3,
            k3.v[component],
            combined);
        history_store[completed_steps].v[component] =
            next_state.v[component] - state->v[component];
    }
    *state = next_state;
}

static void test_ring_against_linear(fc_method_t method)
{
    static fc_workspace_t workspace;
    fc_solver_t solver;
    fc_config_t config;
    fc_vec3f_t linear_history[128];
    fc_vec3f_t linear_state;
    fc_vec3f_t linear_u = {{0.0f, 0.0f, 0.0f}};
    fc_vec3f_t actual;
    uint32_t step;
    uint32_t component;

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_CHEN, method, &config));
    config.memory_length = 41u;
    CHECK_STATUS(fc_solver_init(&solver, &workspace, &config));
    linear_state = config.initial_state;

    for (step = 0u; step < 100u; ++step) {
        if (method == FC_METHOD_EFORK3) {
            reference_efork_step(
                &solver,
                &linear_state,
                linear_history,
                step);
        } else {
            reference_gl_step(
                &solver,
                &linear_state,
                &linear_u,
                linear_history,
                step);
        }
        CHECK_STATUS(fc_solver_step(&solver, &actual));
        for (component = 0u; component < 3u; ++component) {
            if (!close_float(
                    actual.v[component],
                    linear_state.v[component],
                    1.0e-4f)) {
                fprintf(
                    stderr,
                    "ring mismatch method=%u step=%u component=%u: "
                    "%.9g vs %.9g\n",
                    (unsigned int)method,
                    (unsigned int)step,
                    (unsigned int)component,
                    (double)actual.v[component],
                    (double)linear_state.v[component]);
                ++failures;
            }
        }
    }
    CHECK(solver.ring_valid == 41u);
    CHECK(solver.ring_head < 41u);
}

typedef struct {
    uint32_t words[3];
} golden_state_t;

/*
 * These words are generated after the coefficient tables are frozen. They
 * protect the exact float32 operation order and are complemented by the
 * independent double-precision Python check.
 */
static const golden_state_t GOLDEN_32[3][2] = {
    {
        {{0x3e634864u, 0x3ef0d4e7u, 0x3da5342eu}},
        {{0x3ed952aau, 0x3f6c467fu, 0x3da233c5u}}
    },
    {
        {{0x3f79a75eu, 0x3e52542cu, 0x3cd35bafu}},
        {{0x3f70c410u, 0x3ea92e79u, 0x3d08c65cu}}
    },
    {
        {{0x403b2ec7u, 0x409cce6eu, 0x3ec0e891u}},
        {{0x41058ad4u, 0x415d6aceu, 0x4016828bu}}
    }
};

static void test_six_smoke_and_golden(void)
{
    static fc_workspace_t workspace;
    fc_solver_t solver;
    fc_config_t config;
    fc_vec3f_t state;
    uint32_t system;
    uint32_t method;
    uint32_t step;
    uint32_t component;

    for (system = 0u; system < FC_SYSTEM_COUNT; ++system) {
        for (method = 0u; method < 2u; ++method) {
            CHECK_STATUS(fc_config_from_manifest(
                (fc_system_t)system,
                (fc_method_t)method,
                &config));
            CHECK_STATUS(fc_solver_init(
                &solver, &workspace, &config));
            for (step = 0u; step < 32u; ++step) {
                CHECK_STATUS(fc_solver_step(&solver, &state));
                CHECK(isfinite(state.v[0]));
                CHECK(isfinite(state.v[1]));
                CHECK(isfinite(state.v[2]));
            }
            CHECK(fc_solver_diagnostics(&solver)->steps_completed == 32u);
            CHECK(fc_solver_diagnostics(&solver)->active_memory_terms == 31u);
            if (system <= (uint32_t)FC_SYSTEM_CHEN) {
                for (component = 0u; component < 3u; ++component) {
                    CHECK(float_word(state.v[component]) ==
                          GOLDEN_32[system][method].words[component]);
                }
            }
        }
    }
}

static void test_m2sfrk_reduces_to_midpoint_at_q_one(void)
{
    fc_config_t config;
    fc_workspace_t workspace;
    fc_solver_t solver;
    fc_vec3f_t first_rhs;
    fc_vec3f_t second_rhs;
    fc_vec3f_t stage;
    fc_vec3f_t expected;
    fc_vec3f_t actual;
    uint32_t component;

    CHECK_STATUS(fc_config_from_manifest(
        FC_SYSTEM_LORENZ, FC_METHOD_M2SFRK, &config));
    config.q = 1.0f;
    config.h = 0.01f;
    config.precomputed_tables = NULL;
    CHECK_STATUS(fc_rhs(
        config.system,
        config.parameters,
        &config.initial_state,
        &first_rhs));
    for (component = 0u; component < 3u; ++component) {
        stage.v[component] = fmaf(
            0.5f * config.h,
            first_rhs.v[component],
            config.initial_state.v[component]);
    }
    CHECK_STATUS(fc_rhs(
        config.system, config.parameters, &stage, &second_rhs));
    for (component = 0u; component < 3u; ++component) {
        expected.v[component] = fmaf(
            config.h,
            second_rhs.v[component],
            config.initial_state.v[component]);
    }

    CHECK_STATUS(fc_solver_init(&solver, &workspace, &config));
    CHECK_STATUS(fc_solver_step(&solver, &actual));
    for (component = 0u; component < 3u; ++component) {
        CHECK(float_word(actual.v[component]) ==
              float_word(expected.v[component]));
    }
    CHECK(fc_solver_diagnostics(&solver)->active_memory_terms == 0u);
}

int main(void)
{
    test_selected_manifest_hash();
    test_manifests();
    test_rhs();
    test_zero_equilibrium(FC_METHOD_EFORK3);
    test_zero_equilibrium(FC_METHOD_GL_CAPUTO);
    test_zero_equilibrium(FC_METHOD_M2SFRK);
    test_m2sfrk_reduces_to_midpoint_at_q_one();
    test_gl_first_step();
    test_weights_and_reset();
    test_precomputed_table_copy();
    test_ring_against_linear(FC_METHOD_EFORK3);
    test_ring_against_linear(FC_METHOD_GL_CAPUTO);
    test_six_smoke_and_golden();

    if (failures != 0) {
        fprintf(stderr, "%d test assertion(s) failed\n", failures);
        return 1;
    }
    puts("fractional_chaos: all host tests passed");
    return 0;
}
