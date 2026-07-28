#include "fractional_chaos.h"

#include <math.h>
#include <string.h>

#define FC_SOLVER_MAGIC (0x46434348u)

const fc_manifest_t FC_MANIFESTS[FC_SYSTEM_COUNT] = {
    {
        FC_SYSTEM_LORENZ,
        "lorenz",
        0.995f,
        0.005f,
        10.0f,
        2000u,
        {10.0f, 28.0f, 8.0f / 3.0f},
        {{0.1f, 0.1f, 0.1f}}
    },
    {
        FC_SYSTEM_ROSSLER,
        "rossler",
        0.970f,
        0.010f,
        10.0f,
        1000u,
        {0.2f, 0.2f, 6.0f},
        {{0.5f, 1.5f, 0.1f}}
    },
    {
        FC_SYSTEM_CHEN,
        "chen",
        0.900f,
        0.005f,
        10.0f,
        2000u,
        {35.0f, 3.0f, 28.0f},
        {{0.1f, 0.1f, 0.1f}}
    }
};

static int fc_valid_system(fc_system_t system)
{
    return (uint32_t)system < FC_SYSTEM_COUNT;
}

static int fc_valid_method(fc_method_t method)
{
    return (method == FC_METHOD_EFORK3) ||
           (method == FC_METHOD_GL_CAPUTO);
}

static int fc_vec_isfinite(const fc_vec3f_t *value)
{
    return isfinite(value->v[0]) &&
           isfinite(value->v[1]) &&
           isfinite(value->v[2]);
}

static fc_real_t fc_vec_max_abs(const fc_vec3f_t *value)
{
    fc_real_t result = fabsf(value->v[0]);
    const fc_real_t ay = fabsf(value->v[1]);
    const fc_real_t az = fabsf(value->v[2]);

    if (ay > result) {
        result = ay;
    }
    if (az > result) {
        result = az;
    }
    return result;
}

static void fc_neumaier_add(
    fc_real_t value,
    fc_real_t *sum,
    fc_real_t *correction)
{
    const fc_real_t previous = *sum;
    const fc_real_t next = previous + value;

    if (fabsf(previous) >= fabsf(value)) {
        *correction += (previous - next) + value;
    } else {
        *correction += (value - next) + previous;
    }
    *sum = next;
}

static uint32_t fc_oldest_ring_index(const fc_solver_t *solver)
{
    const uint32_t distance = solver->ring_valid - 1u;

    if (solver->ring_head >= distance) {
        return solver->ring_head - distance;
    }
    return solver->config.memory_length -
           (distance - solver->ring_head);
}

static uint32_t fc_next_ring_index(
    uint32_t index,
    uint32_t memory_length)
{
    ++index;
    return (index == memory_length) ? 0u : index;
}

static void fc_ring_push(
    fc_solver_t *solver,
    fc_vec3f_t *ring,
    const fc_vec3f_t *value)
{
    if (solver->ring_valid == 0u) {
        solver->ring_head = 0u;
    } else {
        solver->ring_head = fc_next_ring_index(
            solver->ring_head,
            solver->config.memory_length);
    }

    ring[solver->ring_head] = *value;
    if (solver->ring_valid < solver->config.memory_length) {
        ++solver->ring_valid;
    }
}

const fc_manifest_t *fc_manifest(fc_system_t system)
{
    if (!fc_valid_system(system)) {
        return NULL;
    }
    return &FC_MANIFESTS[(uint32_t)system];
}

fc_status_t fc_config_from_manifest(
    fc_system_t system,
    fc_method_t method,
    fc_config_t *config)
{
    const fc_manifest_t *manifest;
    uint32_t component;

    if (config == NULL) {
        return FC_ERR_NULL;
    }
    if (!fc_valid_method(method)) {
        return FC_ERR_METHOD;
    }

    manifest = fc_manifest(system);
    if (manifest == NULL) {
        return FC_ERR_CONFIG;
    }

    config->system = manifest->system;
    config->method = method;
    config->q = manifest->q;
    config->h = manifest->h;
    config->memory_length = manifest->memory_length;
    config->initial_state = manifest->initial_state;
    config->precomputed_tables = NULL;
    for (component = 0u; component < FC_STATE_DIMENSION; ++component) {
        config->parameters[component] = manifest->parameters[component];
    }
    return FC_OK;
}

fc_status_t fc_rhs(
    fc_system_t system,
    const fc_real_t parameters[FC_STATE_DIMENSION],
    const fc_vec3f_t *state,
    fc_vec3f_t *derivative)
{
    fc_real_t x;
    fc_real_t y;
    fc_real_t z;
    fc_real_t p0;
    fc_real_t p1;
    fc_real_t p2;

    if ((parameters == NULL) || (state == NULL) || (derivative == NULL)) {
        return FC_ERR_NULL;
    }
    if (!fc_valid_system(system) || !fc_vec_isfinite(state)) {
        return FC_ERR_CONFIG;
    }

    x = state->v[0];
    y = state->v[1];
    z = state->v[2];
    p0 = parameters[0];
    p1 = parameters[1];
    p2 = parameters[2];

    switch (system) {
    case FC_SYSTEM_LORENZ:
        derivative->v[0] = p0 * (y - x);
        derivative->v[1] = fmaf(x, p1 - z, -y);
        derivative->v[2] = fmaf(x, y, -(p2 * z));
        break;
    case FC_SYSTEM_ROSSLER:
        derivative->v[0] = -y - z;
        derivative->v[1] = fmaf(p0, y, x);
        derivative->v[2] = fmaf(z, x - p2, p1);
        break;
    case FC_SYSTEM_CHEN:
        derivative->v[0] = p0 * (y - x);
        derivative->v[1] = fmaf(
            -x,
            z,
            fmaf(p2 - p0, x, p2 * y));
        derivative->v[2] = fmaf(x, y, -(p1 * z));
        break;
    default:
        return FC_ERR_CONFIG;
    }

    return fc_vec_isfinite(derivative) ? FC_OK : FC_ERR_NONFINITE;
}

size_t fc_active_workspace_bytes(
    fc_method_t method,
    uint32_t memory_length)
{
    if ((memory_length == 0u) ||
        (memory_length > FC_MAX_MEMORY_LENGTH)) {
        return 0u;
    }

    if (method == FC_METHOD_EFORK3) {
        return (size_t)memory_length *
               (sizeof(fc_vec3f_t) + (3u * sizeof(fc_real_t)));
    }
    if (method == FC_METHOD_GL_CAPUTO) {
        return ((size_t)memory_length * sizeof(fc_vec3f_t)) +
               (((size_t)memory_length + 1u) * sizeof(fc_real_t));
    }
    return 0u;
}

static int fc_config_is_valid(const fc_config_t *config)
{
    uint32_t component;

    if ((config == NULL) ||
        !fc_valid_system(config->system) ||
        !fc_valid_method(config->method) ||
        !isfinite(config->q) ||
        !isfinite(config->h) ||
        (config->q <= 0.0f) ||
        (config->q > 1.0f) ||
        (config->h <= 0.0f) ||
        (config->memory_length == 0u) ||
        (config->memory_length > FC_MAX_MEMORY_LENGTH) ||
        !fc_vec_isfinite(&config->initial_state)) {
        return 0;
    }

    for (component = 0u; component < FC_STATE_DIMENSION; ++component) {
        if (!isfinite(config->parameters[component])) {
            return 0;
        }
    }
    return 1;
}

static int fc_same_float_word(fc_real_t first, fc_real_t second)
{
    uint32_t first_word;
    uint32_t second_word;

    memcpy(&first_word, &first, sizeof(first_word));
    memcpy(&second_word, &second, sizeof(second_word));
    return first_word == second_word;
}

static int fc_valid_sha256_text(const char *text)
{
    uint32_t index;

    if (text == NULL) {
        return 0;
    }
    for (index = 0u; index < 64u; ++index) {
        const char value = text[index];
        const int decimal = (value >= '0') && (value <= '9');
        const int lower = (value >= 'a') && (value <= 'f');
        const int upper = (value >= 'A') && (value <= 'F');
        if (!(decimal || lower || upper)) {
            return 0;
        }
    }
    return text[64] == '\0';
}

static int fc_precomputed_header_is_valid(
    const fc_config_t *config,
    const fc_precomputed_tables_t *tables)
{
    return (tables != NULL) &&
           (tables->method == config->method) &&
           fc_same_float_word(tables->q, config->q) &&
           fc_same_float_word(tables->h, config->h) &&
           (tables->memory_length == config->memory_length) &&
           isfinite(tables->h_to_q) &&
           (tables->h_to_q > 0.0f) &&
           fc_valid_sha256_text(tables->table_sha256);
}

#if !FC_REQUIRE_PRECOMPUTED_TABLES

static fc_real_t fc_stable_power_difference(
    fc_real_t lower,
    fc_real_t exponent)
{
    fc_real_t ratio_log;

    if (lower == 0.0f) {
        return 1.0f;
    }

    ratio_log = log1pf(1.0f / lower);
    return powf(lower, exponent) *
           expm1f(exponent * ratio_log);
}

static fc_status_t fc_prepare_efork(fc_solver_t *solver)
{
    fc_efork_workspace_t *workspace =
        &solver->workspace->storage.efork;
    fc_efork_coefficients_t *coefficient = &solver->efork;
    const fc_real_t q = solver->config.q;
    const fc_real_t one_minus_q = 1.0f - q;
    const fc_real_t gamma_1 = tgammaf(1.0f + q);
    const fc_real_t gamma_2 = tgammaf(1.0f + (2.0f * q));
    const fc_real_t gamma_3 = tgammaf(1.0f + (3.0f * q));
    const fc_real_t history_gamma = tgammaf(2.0f - q);
    fc_real_t denominator;
    fc_real_t gamma_1_sq;
    fc_real_t gamma_1_cu;
    fc_real_t gamma_2_sq;
    fc_real_t stages[3];
    uint32_t stage;
    uint32_t lag;

    if (!isfinite(gamma_1) ||
        !isfinite(gamma_2) ||
        !isfinite(gamma_3) ||
        !isfinite(history_gamma) ||
        (history_gamma <= 0.0f)) {
        return FC_ERR_COEFFICIENT;
    }

    gamma_1_sq = gamma_1 * gamma_1;
    gamma_1_cu = gamma_1_sq * gamma_1;
    gamma_2_sq = gamma_2 * gamma_2;
    denominator = (2.0f * gamma_2_sq) - gamma_3;
    if (!isfinite(denominator) || (fabsf(denominator) <= FLT_EPSILON)) {
        return FC_ERR_COEFFICIENT;
    }

    coefficient->c2 = powf(
        1.0f / (2.0f * gamma_1),
        1.0f / q);
    coefficient->c3 = powf(
        1.0f / (4.0f * gamma_1),
        1.0f / q);
    coefficient->a21 = 1.0f / (2.0f * gamma_1_sq);
    coefficient->a31 =
        ((gamma_1_sq * gamma_2) + (2.0f * gamma_2_sq) - gamma_3) /
        (4.0f * gamma_1_sq * denominator);
    coefficient->a32 = -gamma_2 / (4.0f * denominator);
    coefficient->w1 =
        ((8.0f * gamma_1_cu * gamma_2_sq) -
         (6.0f * gamma_1_cu * gamma_3) +
         (gamma_2 * gamma_3)) /
        (gamma_1 * gamma_2 * gamma_3);
    coefficient->w2 =
        (2.0f * gamma_1_sq * ((4.0f * gamma_2_sq) - gamma_3)) /
        (gamma_2 * gamma_3);
    coefficient->w3 =
        (-8.0f * gamma_1_sq * denominator) /
        (gamma_2 * gamma_3);

    stages[0] = 0.0f;
    stages[1] = coefficient->c2;
    stages[2] = coefficient->c3;

    for (stage = 0u; stage < 3u; ++stage) {
        for (lag = 0u; lag < solver->config.memory_length; ++lag) {
            fc_real_t difference;

            if (one_minus_q == 0.0f) {
                difference = 0.0f;
            } else {
                difference = fc_stable_power_difference(
                    (fc_real_t)lag + stages[stage],
                    one_minus_q);
            }
            workspace->weights[stage][lag] =
                difference / history_gamma;

            if (!isfinite(workspace->weights[stage][lag]) ||
                (workspace->weights[stage][lag] < 0.0f)) {
                return FC_ERR_COEFFICIENT;
            }
        }
    }
    return FC_OK;
}

static fc_status_t fc_prepare_gl(fc_solver_t *solver)
{
    fc_gl_workspace_t *workspace = &solver->workspace->storage.gl;
    const fc_real_t q_plus_one = solver->config.q + 1.0f;
    uint32_t index;

    workspace->weights[0] = 1.0f;
    for (index = 1u; index <= solver->config.memory_length; ++index) {
        const fc_real_t factor =
            1.0f - (q_plus_one / (fc_real_t)index);
        workspace->weights[index] =
            workspace->weights[index - 1u] * factor;
        if (!isfinite(workspace->weights[index])) {
            return FC_ERR_COEFFICIENT;
        }
    }
    return FC_OK;
}

#endif

static fc_status_t fc_copy_precomputed_efork(
    fc_solver_t *solver,
    const fc_precomputed_tables_t *tables)
{
    fc_efork_workspace_t *workspace =
        &solver->workspace->storage.efork;
    uint32_t stage;
    uint32_t lag;

    if (!fc_precomputed_header_is_valid(&solver->config, tables)) {
        return FC_ERR_COEFFICIENT;
    }
    for (stage = 0u; stage < 3u; ++stage) {
        if (tables->efork_weights[stage] == NULL) {
            return FC_ERR_COEFFICIENT;
        }
    }
    if (!isfinite(tables->efork.c2) ||
        !isfinite(tables->efork.c3) ||
        !isfinite(tables->efork.a21) ||
        !isfinite(tables->efork.a31) ||
        !isfinite(tables->efork.a32) ||
        !isfinite(tables->efork.w1) ||
        !isfinite(tables->efork.w2) ||
        !isfinite(tables->efork.w3)) {
        return FC_ERR_COEFFICIENT;
    }

    solver->efork = tables->efork;
    solver->h_to_q = tables->h_to_q;
    for (stage = 0u; stage < 3u; ++stage) {
        for (lag = 0u; lag < solver->config.memory_length; ++lag) {
            const fc_real_t weight = tables->efork_weights[stage][lag];
            if (!isfinite(weight) || (weight < 0.0f)) {
                return FC_ERR_COEFFICIENT;
            }
            workspace->weights[stage][lag] = weight;
        }
    }
    return FC_OK;
}

static fc_status_t fc_copy_precomputed_gl(
    fc_solver_t *solver,
    const fc_precomputed_tables_t *tables)
{
    fc_gl_workspace_t *workspace = &solver->workspace->storage.gl;
    uint32_t index;

    if (!fc_precomputed_header_is_valid(&solver->config, tables) ||
        (tables->gl_weights == NULL)) {
        return FC_ERR_COEFFICIENT;
    }
    if (!fc_same_float_word(tables->gl_weights[0], 1.0f)) {
        return FC_ERR_COEFFICIENT;
    }

    solver->h_to_q = tables->h_to_q;
    for (index = 0u; index <= solver->config.memory_length; ++index) {
        const fc_real_t weight = tables->gl_weights[index];
        if (!isfinite(weight)) {
            return FC_ERR_COEFFICIENT;
        }
        if ((index > 0u) && (weight > 0.0f)) {
            return FC_ERR_COEFFICIENT;
        }
        workspace->weights[index] = weight;
    }
    return FC_OK;
}

fc_status_t fc_solver_init(
    fc_solver_t *solver,
    fc_workspace_t *workspace,
    const fc_config_t *config)
{
    fc_status_t status;

    if ((solver == NULL) || (workspace == NULL) || (config == NULL)) {
        return FC_ERR_NULL;
    }
    if (!fc_config_is_valid(config)) {
        return FC_ERR_CONFIG;
    }

    solver->magic = 0u;
    solver->config = *config;
    solver->workspace = workspace;

    if (config->precomputed_tables != NULL) {
        if (config->method == FC_METHOD_EFORK3) {
            status = fc_copy_precomputed_efork(
                solver, config->precomputed_tables);
        } else if (config->method == FC_METHOD_GL_CAPUTO) {
            status = fc_copy_precomputed_gl(
                solver, config->precomputed_tables);
        } else {
            return FC_ERR_METHOD;
        }
    } else {
#if FC_REQUIRE_PRECOMPUTED_TABLES
        return FC_ERR_COEFFICIENT;
#else
        solver->h_to_q = powf(config->h, config->q);
        if (!isfinite(solver->h_to_q) || (solver->h_to_q <= 0.0f)) {
            return FC_ERR_COEFFICIENT;
        }

        if (config->method == FC_METHOD_EFORK3) {
            status = fc_prepare_efork(solver);
        } else if (config->method == FC_METHOD_GL_CAPUTO) {
            status = fc_prepare_gl(solver);
        } else {
            return FC_ERR_METHOD;
        }
#endif
    }

    if (status != FC_OK) {
        return status;
    }

    solver->magic = FC_SOLVER_MAGIC;
    return fc_solver_reset(solver);
}

fc_status_t fc_solver_reset(fc_solver_t *solver)
{
    if (solver == NULL) {
        return FC_ERR_NULL;
    }
    if ((solver->magic != FC_SOLVER_MAGIC) ||
        (solver->workspace == NULL)) {
        return FC_ERR_NOT_INITIALIZED;
    }

    solver->state = solver->config.initial_state;
    solver->gl_current_u.v[0] = 0.0f;
    solver->gl_current_u.v[1] = 0.0f;
    solver->gl_current_u.v[2] = 0.0f;
    solver->step_index = 0u;
    solver->ring_head = 0u;
    solver->ring_valid = 0u;
    solver->diagnostics.steps_completed = 0u;
    solver->diagnostics.active_memory_terms = 0u;
    solver->diagnostics.last_status = FC_OK;
    solver->diagnostics.max_abs_state =
        fc_vec_max_abs(&solver->state);
    solver->diagnostics.last_history_abs_max = 0.0f;
    return FC_OK;
}

static void fc_efork_history(
    const fc_solver_t *solver,
    fc_vec3f_t history[3])
{
    const fc_efork_workspace_t *workspace =
        &solver->workspace->storage.efork;
    fc_real_t sum[9] = {0.0f};
    fc_real_t correction[9] = {0.0f};
    uint32_t remaining = solver->ring_valid;
    uint32_t ring_index;
    uint32_t lag;

    history[0] = (fc_vec3f_t){{0.0f, 0.0f, 0.0f}};
    history[1] = (fc_vec3f_t){{0.0f, 0.0f, 0.0f}};
    history[2] = (fc_vec3f_t){{0.0f, 0.0f, 0.0f}};

    if (remaining == 0u) {
        return;
    }

    ring_index = fc_oldest_ring_index(solver);
    lag = remaining - 1u;

    while (remaining > 0u) {
        fc_real_t block_sum[9] = {0.0f};
        const uint32_t block_length =
            (remaining > FC_SUM_BLOCK_LENGTH) ?
            FC_SUM_BLOCK_LENGTH : remaining;
        uint32_t item;

        for (item = 0u; item < block_length; ++item) {
            const fc_vec3f_t increment =
                workspace->increments[ring_index];
            uint32_t stage;

            for (stage = 0u; stage < 3u; ++stage) {
                const fc_real_t weight =
                    workspace->weights[stage][lag];
                const uint32_t base = stage * 3u;

                block_sum[base] =
                    fmaf(weight, increment.v[0], block_sum[base]);
                block_sum[base + 1u] =
                    fmaf(weight, increment.v[1], block_sum[base + 1u]);
                block_sum[base + 2u] =
                    fmaf(weight, increment.v[2], block_sum[base + 2u]);
            }

            ring_index = fc_next_ring_index(
                ring_index,
                solver->config.memory_length);
            if (lag > 0u) {
                --lag;
            }
        }

        for (item = 0u; item < 9u; ++item) {
            fc_neumaier_add(
                block_sum[item],
                &sum[item],
                &correction[item]);
        }
        remaining -= block_length;
    }

    {
        uint32_t stage;
        uint32_t component;
        for (stage = 0u; stage < 3u; ++stage) {
            for (component = 0u;
                 component < FC_STATE_DIMENSION;
                 ++component) {
                const uint32_t index = (stage * 3u) + component;
                history[stage].v[component] =
                    sum[index] + correction[index];
            }
        }
    }
}

static void fc_gl_history(
    const fc_solver_t *solver,
    fc_vec3f_t *history)
{
    const fc_gl_workspace_t *workspace =
        &solver->workspace->storage.gl;
    fc_real_t sum[3] = {0.0f};
    fc_real_t correction[3] = {0.0f};
    uint32_t remaining = solver->ring_valid;
    uint32_t ring_index;
    uint32_t weight_index;

    *history = (fc_vec3f_t){{0.0f, 0.0f, 0.0f}};
    if (remaining == 0u) {
        return;
    }

    ring_index = fc_oldest_ring_index(solver);
    weight_index = remaining;

    while (remaining > 0u) {
        fc_real_t block_sum[3] = {0.0f};
        const uint32_t block_length =
            (remaining > FC_SUM_BLOCK_LENGTH) ?
            FC_SUM_BLOCK_LENGTH : remaining;
        uint32_t item;

        for (item = 0u; item < block_length; ++item) {
            const fc_vec3f_t deviation =
                workspace->deviations[ring_index];
            const fc_real_t weight =
                workspace->weights[weight_index];

            block_sum[0] =
                fmaf(weight, deviation.v[0], block_sum[0]);
            block_sum[1] =
                fmaf(weight, deviation.v[1], block_sum[1]);
            block_sum[2] =
                fmaf(weight, deviation.v[2], block_sum[2]);

            ring_index = fc_next_ring_index(
                ring_index,
                solver->config.memory_length);
            if (weight_index > 1u) {
                --weight_index;
            }
        }

        for (item = 0u; item < 3u; ++item) {
            fc_neumaier_add(
                block_sum[item],
                &sum[item],
                &correction[item]);
        }
        remaining -= block_length;
    }

    history->v[0] = sum[0] + correction[0];
    history->v[1] = sum[1] + correction[1];
    history->v[2] = sum[2] + correction[2];
}

static fc_status_t fc_step_efork(
    fc_solver_t *solver,
    fc_vec3f_t *output)
{
    fc_vec3f_t history[3];
    fc_vec3f_t rhs;
    fc_vec3f_t k1;
    fc_vec3f_t k2;
    fc_vec3f_t k3;
    fc_vec3f_t stage_state;
    fc_vec3f_t next_state;
    fc_vec3f_t increment;
    fc_real_t history_max = 0.0f;
    uint32_t component;
    fc_status_t status;

    fc_efork_history(solver, history);
    for (component = 0u; component < 3u; ++component) {
        const fc_real_t magnitude = fc_vec_max_abs(&history[component]);
        if (magnitude > history_max) {
            history_max = magnitude;
        }
    }

    status = fc_rhs(
        solver->config.system,
        solver->config.parameters,
        &solver->state,
        &rhs);
    if (status != FC_OK) {
        return status;
    }

    for (component = 0u; component < 3u; ++component) {
        k1.v[component] = fmaf(
            solver->h_to_q,
            rhs.v[component],
            -history[0].v[component]);
        stage_state.v[component] = fmaf(
            solver->efork.a21,
            k1.v[component],
            solver->state.v[component]);
    }

    status = fc_rhs(
        solver->config.system,
        solver->config.parameters,
        &stage_state,
        &rhs);
    if (status != FC_OK) {
        return status;
    }

    for (component = 0u; component < 3u; ++component) {
        k2.v[component] = fmaf(
            solver->h_to_q,
            rhs.v[component],
            -history[1].v[component]);
        stage_state.v[component] = fmaf(
            solver->efork.a32,
            k2.v[component],
            fmaf(
                solver->efork.a31,
                k1.v[component],
                solver->state.v[component]));
    }

    status = fc_rhs(
        solver->config.system,
        solver->config.parameters,
        &stage_state,
        &rhs);
    if (status != FC_OK) {
        return status;
    }

    for (component = 0u; component < 3u; ++component) {
        fc_real_t combined;

        k3.v[component] = fmaf(
            solver->h_to_q,
            rhs.v[component],
            -history[2].v[component]);
        combined = fmaf(
            solver->efork.w1,
            k1.v[component],
            solver->state.v[component]);
        combined = fmaf(
            solver->efork.w2,
            k2.v[component],
            combined);
        next_state.v[component] = fmaf(
            solver->efork.w3,
            k3.v[component],
            combined);
        increment.v[component] =
            next_state.v[component] - solver->state.v[component];
    }

    if (!fc_vec_isfinite(&next_state) ||
        !fc_vec_isfinite(&increment)) {
        return FC_ERR_NONFINITE;
    }

    fc_ring_push(
        solver,
        solver->workspace->storage.efork.increments,
        &increment);
    solver->state = next_state;
    ++solver->step_index;
    solver->diagnostics.steps_completed = solver->step_index;
    solver->diagnostics.active_memory_terms =
        (uint32_t)((solver->step_index - 1u) <
                   solver->config.memory_length ?
                   (solver->step_index - 1u) :
                   solver->config.memory_length);
    solver->diagnostics.last_history_abs_max = history_max;
    solver->diagnostics.max_abs_state =
        fc_vec_max_abs(&solver->state);
    solver->diagnostics.last_status = FC_OK;

    if (output != NULL) {
        *output = solver->state;
    }
    return FC_OK;
}

static fc_status_t fc_step_gl(
    fc_solver_t *solver,
    fc_vec3f_t *output)
{
    fc_vec3f_t history;
    fc_vec3f_t rhs;
    fc_vec3f_t next_u;
    fc_vec3f_t next_state;
    uint32_t component;
    fc_status_t status;

    fc_gl_history(solver, &history);
    status = fc_rhs(
        solver->config.system,
        solver->config.parameters,
        &solver->state,
        &rhs);
    if (status != FC_OK) {
        return status;
    }

    for (component = 0u; component < 3u; ++component) {
        next_u.v[component] = fmaf(
            solver->h_to_q,
            rhs.v[component],
            -history.v[component]);
        next_state.v[component] =
            solver->config.initial_state.v[component] +
            next_u.v[component];
    }

    if (!fc_vec_isfinite(&next_state) ||
        !fc_vec_isfinite(&next_u)) {
        return FC_ERR_NONFINITE;
    }

    fc_ring_push(
        solver,
        solver->workspace->storage.gl.deviations,
        &next_u);
    solver->gl_current_u = next_u;
    solver->state = next_state;
    ++solver->step_index;
    solver->diagnostics.steps_completed = solver->step_index;
    solver->diagnostics.active_memory_terms =
        (uint32_t)((solver->step_index - 1u) <
                   solver->config.memory_length ?
                   (solver->step_index - 1u) :
                   solver->config.memory_length);
    solver->diagnostics.last_history_abs_max =
        fc_vec_max_abs(&history);
    solver->diagnostics.max_abs_state =
        fc_vec_max_abs(&solver->state);
    solver->diagnostics.last_status = FC_OK;

    if (output != NULL) {
        *output = solver->state;
    }
    return FC_OK;
}

fc_status_t fc_solver_step(
    fc_solver_t *solver,
    fc_vec3f_t *output)
{
    fc_status_t status;

    if (solver == NULL) {
        return FC_ERR_NULL;
    }
    if ((solver->magic != FC_SOLVER_MAGIC) ||
        (solver->workspace == NULL)) {
        return FC_ERR_NOT_INITIALIZED;
    }

    if (solver->config.method == FC_METHOD_EFORK3) {
        status = fc_step_efork(solver, output);
    } else if (solver->config.method == FC_METHOD_GL_CAPUTO) {
        status = fc_step_gl(solver, output);
    } else {
        status = FC_ERR_METHOD;
    }

    solver->diagnostics.last_status = status;
    return status;
}

const fc_vec3f_t *fc_solver_state(const fc_solver_t *solver)
{
    if ((solver == NULL) || (solver->magic != FC_SOLVER_MAGIC)) {
        return NULL;
    }
    return &solver->state;
}

const fc_config_t *fc_solver_config(const fc_solver_t *solver)
{
    if ((solver == NULL) || (solver->magic != FC_SOLVER_MAGIC)) {
        return NULL;
    }
    return &solver->config;
}

const fc_diagnostics_t *fc_solver_diagnostics(const fc_solver_t *solver)
{
    if ((solver == NULL) || (solver->magic != FC_SOLVER_MAGIC)) {
        return NULL;
    }
    return &solver->diagnostics;
}

fc_status_t fc_solver_efork_coefficients(
    const fc_solver_t *solver,
    fc_efork_coefficients_t *coefficients)
{
    if ((solver == NULL) || (coefficients == NULL)) {
        return FC_ERR_NULL;
    }
    if (solver->magic != FC_SOLVER_MAGIC) {
        return FC_ERR_NOT_INITIALIZED;
    }
    if (solver->config.method != FC_METHOD_EFORK3) {
        return FC_ERR_METHOD;
    }
    *coefficients = solver->efork;
    return FC_OK;
}

fc_status_t fc_solver_efork_weight(
    const fc_solver_t *solver,
    uint32_t stage,
    uint32_t lag,
    fc_real_t *weight)
{
    if ((solver == NULL) || (weight == NULL)) {
        return FC_ERR_NULL;
    }
    if (solver->magic != FC_SOLVER_MAGIC) {
        return FC_ERR_NOT_INITIALIZED;
    }
    if (solver->config.method != FC_METHOD_EFORK3) {
        return FC_ERR_METHOD;
    }
    if ((stage >= 3u) || (lag >= solver->config.memory_length)) {
        return FC_ERR_RANGE;
    }
    *weight = solver->workspace->storage.efork.weights[stage][lag];
    return FC_OK;
}

fc_status_t fc_solver_gl_weight(
    const fc_solver_t *solver,
    uint32_t index,
    fc_real_t *weight)
{
    if ((solver == NULL) || (weight == NULL)) {
        return FC_ERR_NULL;
    }
    if (solver->magic != FC_SOLVER_MAGIC) {
        return FC_ERR_NOT_INITIALIZED;
    }
    if (solver->config.method != FC_METHOD_GL_CAPUTO) {
        return FC_ERR_METHOD;
    }
    if (index > solver->config.memory_length) {
        return FC_ERR_RANGE;
    }
    *weight = solver->workspace->storage.gl.weights[index];
    return FC_OK;
}
