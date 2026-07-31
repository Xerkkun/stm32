#include "fractional_chaos_fixed.h"

#include <float.h>
#include <limits.h>
#include <math.h>
#include <stddef.h>
#include <string.h>

#define FC_FIXED_SOLVER_MAGIC (0x46585131u)
#define FC_FIXED_HALF_SCALE   (FC_FIXED_SCALE / 2)
#define FC_FIXED_COEFFICIENT_HALF_SCALE \
    (FC_FIXED_COEFFICIENT_SCALE / INT64_C(2))

_Static_assert(sizeof(fc_fixed_t) == 4u,
               "the Q1.14.14 container must be 32 bits");
_Static_assert(FC_FIXED_RAW_MIN > INT32_MIN,
               "Q1.14.14 requires guard bits in its int32_t container");
_Static_assert(FC_FIXED_RAW_MAX < INT32_MAX,
               "Q1.14.14 requires guard bits in its int32_t container");
_Static_assert(FC_FIXED_SYSTEM_LIU == 3,
               "fixed and float Liu IDs must match");
_Static_assert(FC_FIXED_SYSTEM_HAMMOUCH_MEKKAOUI == 4,
               "fixed and float Hammouch--Mekkaoui IDs must match");

typedef struct {
    double q;
    double h;
    uint32_t memory_length;
    double parameters[FC_FIXED_PARAMETER_COUNT];
    double initial_state[FC_FIXED_STATE_DIMENSION];
} fc_fixed_manifest_t;

static const fc_fixed_manifest_t FC_FIXED_MANIFESTS[
    FC_FIXED_SYSTEM_COUNT] = {
    {
        0.995,
        0.005,
        2000u,
        {10.0, 28.0, 8.0 / 3.0, 0.0, 0.0, 0.0},
        {0.1, 0.1, 0.1}
    },
    {
        0.9877,
        0.010,
        1000u,
        {0.2, 0.2, 5.7, 0.0, 0.0, 0.0},
        {1.0, 0.0, 0.0}
    },
    {
        0.900,
        0.005,
        2000u,
        {35.0, 3.0, 28.0, 0.0, 0.0, 0.0},
        {0.1, 0.1, 0.1}
    },
    {
        0.920,
        0.010,
        1000u,
        {1.0, 2.5, 5.0, 1.0, 4.0, 4.0},
        {0.2, 0.0, 0.5}
    },
    {
        0.980,
        0.010,
        1000u,
        {0.0, 0.0, 0.0, 0.0, 0.0, 0.0},
        {0.7, 0.1, 0.0}
    }
};

static int fc_fixed_valid_system(fc_fixed_system_t system)
{
    return (uint32_t)system < FC_FIXED_SYSTEM_COUNT;
}

static int fc_fixed_valid_method(fc_fixed_method_t method)
{
    return (method == FC_FIXED_METHOD_EFORK3) ||
           (method == FC_FIXED_METHOD_GL_CAPUTO) ||
           (method == FC_FIXED_METHOD_M2SFRK);
}

static int fc_fixed_isfinite(double value)
{
    /*
     * Some embedded C libraries expose isfinite only through a float helper.
     * Ordered DBL_MAX comparisons reject both infinities and NaNs without an
     * implicit narrowing conversion.
     */
    return (value <= DBL_MAX) && (value >= -DBL_MAX);
}

static void fc_fixed_record_saturation(
    fc_fixed_arithmetic_t *arithmetic)
{
    if ((arithmetic != NULL) &&
        (arithmetic->saturation_count != UINT64_MAX)) {
        ++arithmetic->saturation_count;
    }
}

static fc_fixed_t fc_fixed_saturate_i64(
    int64_t value,
    fc_fixed_arithmetic_t *arithmetic)
{
    if (value > (int64_t)FC_FIXED_RAW_MAX) {
        fc_fixed_record_saturation(arithmetic);
        return (fc_fixed_t)FC_FIXED_RAW_MAX;
    }
    if (value < (int64_t)FC_FIXED_RAW_MIN) {
        fc_fixed_record_saturation(arithmetic);
        return (fc_fixed_t)FC_FIXED_RAW_MIN;
    }
    return (fc_fixed_t)value;
}

fc_fixed_status_t fc_fixed_from_double(
    double value,
    fc_fixed_arithmetic_t *arithmetic,
    fc_fixed_t *output)
{
    double scaled;
    double rounded;

    if (output == NULL) {
        return FC_FIXED_ERR_NULL;
    }
    if (!fc_fixed_isfinite(value)) {
        *output = 0;
        return FC_FIXED_ERR_NONFINITE;
    }

    scaled = value * (double)FC_FIXED_SCALE;
    if (!fc_fixed_isfinite(scaled)) {
        fc_fixed_record_saturation(arithmetic);
        *output = (value < 0.0) ?
            (fc_fixed_t)FC_FIXED_RAW_MIN :
            (fc_fixed_t)FC_FIXED_RAW_MAX;
        return FC_FIXED_OK;
    }

    rounded = (scaled >= 0.0) ?
        floor(scaled + 0.5) :
        ceil(scaled - 0.5);
    if (rounded > (double)FC_FIXED_RAW_MAX) {
        fc_fixed_record_saturation(arithmetic);
        *output = (fc_fixed_t)FC_FIXED_RAW_MAX;
    } else if (rounded < (double)FC_FIXED_RAW_MIN) {
        fc_fixed_record_saturation(arithmetic);
        *output = (fc_fixed_t)FC_FIXED_RAW_MIN;
    } else {
        *output = (fc_fixed_t)rounded;
    }
    return FC_FIXED_OK;
}

double fc_fixed_to_double(fc_fixed_t value)
{
    return (double)value / (double)FC_FIXED_SCALE;
}

fc_fixed_t fc_fixed_add(
    fc_fixed_t left,
    fc_fixed_t right,
    fc_fixed_arithmetic_t *arithmetic)
{
    return fc_fixed_saturate_i64(
        (int64_t)left + (int64_t)right,
        arithmetic);
}

fc_fixed_t fc_fixed_sub(
    fc_fixed_t left,
    fc_fixed_t right,
    fc_fixed_arithmetic_t *arithmetic)
{
    return fc_fixed_saturate_i64(
        (int64_t)left - (int64_t)right,
        arithmetic);
}

static fc_fixed_t fc_fixed_neg(
    fc_fixed_t value,
    fc_fixed_arithmetic_t *arithmetic)
{
    return fc_fixed_saturate_i64(-(int64_t)value, arithmetic);
}

fc_fixed_t fc_fixed_mul(
    fc_fixed_t left,
    fc_fixed_t right,
    fc_fixed_arithmetic_t *arithmetic)
{
    const int64_t product = (int64_t)left * (int64_t)right;
    const uint64_t magnitude = (product < 0) ?
        (uint64_t)(-product) :
        (uint64_t)product;
    const uint64_t rounded =
        (magnitude + (uint64_t)FC_FIXED_HALF_SCALE) /
        (uint64_t)FC_FIXED_SCALE;
    const int64_t signed_result = (product < 0) ?
        -(int64_t)rounded :
        (int64_t)rounded;

    return fc_fixed_saturate_i64(signed_result, arithmetic);
}

fc_fixed_t fc_fixed_mul_coefficient(
    fc_fixed_coefficient_t coefficient,
    fc_fixed_t value,
    fc_fixed_arithmetic_t *arithmetic)
{
    const int64_t product =
        (int64_t)coefficient * (int64_t)value;
    const uint64_t magnitude = (product < 0) ?
        (uint64_t)(-product) :
        (uint64_t)product;
    const uint64_t rounded =
        (magnitude +
         (uint64_t)FC_FIXED_COEFFICIENT_HALF_SCALE) /
        (uint64_t)FC_FIXED_COEFFICIENT_SCALE;
    const int64_t signed_result = (product < 0) ?
        -(int64_t)rounded :
        (int64_t)rounded;

    return fc_fixed_saturate_i64(signed_result, arithmetic);
}

fc_fixed_status_t fc_fixed_config_from_manifest(
    fc_fixed_system_t system,
    fc_fixed_method_t method,
    fc_fixed_config_t *config)
{
    const fc_fixed_manifest_t *manifest;
    uint32_t component;
    uint32_t parameter;

    if (config == NULL) {
        return FC_FIXED_ERR_NULL;
    }
    if (!fc_fixed_valid_system(system)) {
        return FC_FIXED_ERR_CONFIG;
    }
    if (!fc_fixed_valid_method(method)) {
        return FC_FIXED_ERR_METHOD;
    }

    manifest = &FC_FIXED_MANIFESTS[(uint32_t)system];
    config->system = system;
    config->method = method;
    config->q = manifest->q;
    config->h = manifest->h;
    config->memory_length = manifest->memory_length;
    for (parameter = 0u;
         parameter < FC_FIXED_PARAMETER_COUNT;
         ++parameter) {
        config->parameters[parameter] =
            manifest->parameters[parameter];
    }
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        config->initial_state[component] =
            manifest->initial_state[component];
    }
    return FC_FIXED_OK;
}

fc_fixed_status_t fc_fixed_rhs(
    fc_fixed_system_t system,
    const fc_fixed_parameters_t *parameters,
    const fc_fixed_vec3_t *state,
    fc_fixed_arithmetic_t *arithmetic,
    fc_fixed_vec3_t *derivative)
{
    const fc_fixed_t x = (state != NULL) ? state->v[0] : 0;
    const fc_fixed_t y = (state != NULL) ? state->v[1] : 0;
    const fc_fixed_t z = (state != NULL) ? state->v[2] : 0;
    const fc_fixed_t p0 =
        (parameters != NULL) ? parameters->v[0] : 0;
    const fc_fixed_t p1 =
        (parameters != NULL) ? parameters->v[1] : 0;
    const fc_fixed_t p2 =
        (parameters != NULL) ? parameters->v[2] : 0;
    const fc_fixed_t p3 =
        (parameters != NULL) ? parameters->v[3] : 0;
    const fc_fixed_t p4 =
        (parameters != NULL) ? parameters->v[4] : 0;
    const fc_fixed_t p5 =
        (parameters != NULL) ? parameters->v[5] : 0;

    if ((parameters == NULL) ||
        (state == NULL) ||
        (derivative == NULL)) {
        return FC_FIXED_ERR_NULL;
    }
    if (!fc_fixed_valid_system(system)) {
        return FC_FIXED_ERR_CONFIG;
    }

    if (system == FC_FIXED_SYSTEM_LORENZ) {
        derivative->v[0] = fc_fixed_mul(
            p0,
            fc_fixed_sub(y, x, arithmetic),
            arithmetic);
        derivative->v[1] = fc_fixed_sub(
            fc_fixed_mul(
                x,
                fc_fixed_sub(p1, z, arithmetic),
                arithmetic),
            y,
            arithmetic);
        derivative->v[2] = fc_fixed_sub(
            fc_fixed_mul(x, y, arithmetic),
            fc_fixed_mul(p2, z, arithmetic),
            arithmetic);
    } else if (system == FC_FIXED_SYSTEM_ROSSLER) {
        derivative->v[0] = fc_fixed_sub(
            fc_fixed_neg(y, arithmetic),
            z,
            arithmetic);
        derivative->v[1] = fc_fixed_add(
            x,
            fc_fixed_mul(p0, y, arithmetic),
            arithmetic);
        derivative->v[2] = fc_fixed_add(
            p1,
            fc_fixed_mul(
                z,
                fc_fixed_sub(x, p2, arithmetic),
                arithmetic),
            arithmetic);
    } else if (system == FC_FIXED_SYSTEM_CHEN) {
        derivative->v[0] = fc_fixed_mul(
            p0,
            fc_fixed_sub(y, x, arithmetic),
            arithmetic);
        derivative->v[1] = fc_fixed_add(
            fc_fixed_sub(
                fc_fixed_mul(
                    fc_fixed_sub(p2, p0, arithmetic),
                    x,
                    arithmetic),
                fc_fixed_mul(x, z, arithmetic),
                arithmetic),
            fc_fixed_mul(p2, y, arithmetic),
            arithmetic);
        derivative->v[2] = fc_fixed_sub(
            fc_fixed_mul(x, y, arithmetic),
            fc_fixed_mul(p1, z, arithmetic),
            arithmetic);
    } else if (system == FC_FIXED_SYSTEM_LIU) {
        fc_fixed_t product;
        fc_fixed_t left;
        fc_fixed_t right;

        product = fc_fixed_mul(p0, x, arithmetic);
        left = fc_fixed_neg(product, arithmetic);
        product = fc_fixed_mul(y, y, arithmetic);
        right = fc_fixed_mul(p3, product, arithmetic);
        derivative->v[0] = fc_fixed_sub(
            left, right, arithmetic);

        left = fc_fixed_mul(p1, y, arithmetic);
        product = fc_fixed_mul(x, z, arithmetic);
        right = fc_fixed_mul(p4, product, arithmetic);
        derivative->v[1] = fc_fixed_sub(
            left, right, arithmetic);

        product = fc_fixed_mul(p2, z, arithmetic);
        left = fc_fixed_neg(product, arithmetic);
        product = fc_fixed_mul(x, y, arithmetic);
        right = fc_fixed_mul(p5, product, arithmetic);
        derivative->v[2] = fc_fixed_add(
            left, right, arithmetic);
    } else {
        const fc_fixed_t two =
            (fc_fixed_t)(2 * FC_FIXED_SCALE);
        const fc_fixed_t three =
            (fc_fixed_t)(3 * FC_FIXED_SCALE);
        const fc_fixed_t four =
            (fc_fixed_t)(4 * FC_FIXED_SCALE);
        const fc_fixed_t seven =
            (fc_fixed_t)(7 * FC_FIXED_SCALE);
        fc_fixed_t product;
        fc_fixed_t left;
        fc_fixed_t right;

        product = fc_fixed_mul(two, x, arithmetic);
        left = fc_fixed_neg(product, arithmetic);
        right = fc_fixed_mul(y, y, arithmetic);
        derivative->v[0] = fc_fixed_sub(
            left, right, arithmetic);

        product = fc_fixed_mul(x, z, arithmetic);
        product = fc_fixed_mul(four, product, arithmetic);
        left = fc_fixed_neg(product, arithmetic);
        right = fc_fixed_mul(three, y, arithmetic);
        left = fc_fixed_add(left, right, arithmetic);
        right = fc_fixed_mul(z, z, arithmetic);
        derivative->v[1] = fc_fixed_sub(
            left, right, arithmetic);

        product = fc_fixed_mul(x, y, arithmetic);
        left = fc_fixed_mul(four, product, arithmetic);
        right = fc_fixed_mul(seven, z, arithmetic);
        left = fc_fixed_sub(left, right, arithmetic);
        right = fc_fixed_mul(y, z, arithmetic);
        derivative->v[2] = fc_fixed_add(
            left, right, arithmetic);
    }
    return FC_FIXED_OK;
}

static int fc_fixed_config_is_valid(const fc_fixed_config_t *config)
{
    uint32_t component;
    uint32_t parameter;

    if ((config == NULL) ||
        !fc_fixed_valid_system(config->system) ||
        !fc_fixed_valid_method(config->method) ||
        !fc_fixed_isfinite(config->q) ||
        !fc_fixed_isfinite(config->h) ||
        (config->q <= 0.0) ||
        (config->q > 1.0) ||
        (config->h <= 0.0) ||
        (config->memory_length == 0u) ||
        (config->memory_length > FC_FIXED_MAX_MEMORY_LENGTH)) {
        return 0;
    }

    for (parameter = 0u;
         parameter < FC_FIXED_PARAMETER_COUNT;
         ++parameter) {
        if (!fc_fixed_isfinite(config->parameters[parameter])) {
            return 0;
        }
    }
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        if (!fc_fixed_isfinite(config->initial_state[component])) {
            return 0;
        }
    }
    return 1;
}

static void fc_fixed_increment_counter(uint64_t *counter)
{
    if (*counter != UINT64_MAX) {
        ++(*counter);
    }
}

static fc_fixed_status_t fc_fixed_coefficient_from_double(
    fc_fixed_solver_t *solver,
    double value,
    fc_fixed_coefficient_t *output)
{
    double scaled;
    double rounded;

    if ((solver == NULL) || (output == NULL)) {
        return FC_FIXED_ERR_NULL;
    }
    if (!fc_fixed_isfinite(value)) {
        *output = 0;
        return FC_FIXED_ERR_NONFINITE;
    }

    scaled = value * (double)FC_FIXED_COEFFICIENT_SCALE;
    if (!fc_fixed_isfinite(scaled)) {
        fc_fixed_increment_counter(
            &solver->coefficient_saturation_count);
        *output = (value < 0.0) ?
            (fc_fixed_coefficient_t)
                FC_FIXED_COEFFICIENT_RAW_MIN :
            (fc_fixed_coefficient_t)
                FC_FIXED_COEFFICIENT_RAW_MAX;
        return FC_FIXED_OK;
    }

    rounded = (scaled >= 0.0) ?
        floor(scaled + 0.5) :
        ceil(scaled - 0.5);
    if (rounded >
        (double)FC_FIXED_COEFFICIENT_RAW_MAX) {
        fc_fixed_increment_counter(
            &solver->coefficient_saturation_count);
        *output = (fc_fixed_coefficient_t)
            FC_FIXED_COEFFICIENT_RAW_MAX;
    } else if (rounded <
               (double)FC_FIXED_COEFFICIENT_RAW_MIN) {
        fc_fixed_increment_counter(
            &solver->coefficient_saturation_count);
        *output = (fc_fixed_coefficient_t)
            FC_FIXED_COEFFICIENT_RAW_MIN;
    } else {
        *output = (fc_fixed_coefficient_t)rounded;
    }

    if ((value != 0.0) && (*output == 0)) {
        fc_fixed_increment_counter(
            &solver->zeroed_nonzero_coefficient_count);
    }
    return FC_FIXED_OK;
}

static fc_fixed_status_t fc_fixed_prepare_efork(
    fc_fixed_solver_t *solver)
{
    const double q = solver->config.q;
    const double gamma_1 = tgamma(1.0 + q);
    const double gamma_2 = tgamma(1.0 + (2.0 * q));
    const double gamma_3 = tgamma(1.0 + (3.0 * q));
    const double denominator =
        (2.0 * gamma_2 * gamma_2) - gamma_3;
    const double c2 = pow(
        1.0 / (2.0 * gamma_1),
        1.0 / q);
    const double c3 = pow(
        1.0 / (4.0 * gamma_1),
        1.0 / q);
    const double a21 =
        1.0 / (2.0 * gamma_1 * gamma_1);
    const double a31 =
        ((gamma_1 * gamma_1 * gamma_2) +
         (2.0 * gamma_2 * gamma_2) -
         gamma_3) /
        (4.0 * gamma_1 * gamma_1 * denominator);
    const double a32 = -gamma_2 / (4.0 * denominator);
    const double w1 =
        ((8.0 * gamma_1 * gamma_1 * gamma_1 *
          gamma_2 * gamma_2) -
         (6.0 * gamma_1 * gamma_1 * gamma_1 * gamma_3) +
         (gamma_2 * gamma_3)) /
        (gamma_1 * gamma_2 * gamma_3);
    const double w2 =
        (2.0 * gamma_1 * gamma_1 *
         ((4.0 * gamma_2 * gamma_2) - gamma_3)) /
        (gamma_2 * gamma_3);
    const double w3 =
        (-8.0 * gamma_1 * gamma_1 * denominator) /
        (gamma_2 * gamma_3);
    const double h_to_q = pow(solver->config.h, q);
    const double exponent = 1.0 - q;
    const double inverse_history_gamma =
        1.0 / tgamma(2.0 - q);
    const double offsets[3] = {0.0, c2, c3};
    fc_fixed_status_t status;
    uint32_t stage;
    uint32_t lag;

    if (!fc_fixed_isfinite(gamma_1) ||
        !fc_fixed_isfinite(gamma_2) ||
        !fc_fixed_isfinite(gamma_3) ||
        !fc_fixed_isfinite(denominator) ||
        (denominator == 0.0) ||
        !fc_fixed_isfinite(h_to_q) ||
        !fc_fixed_isfinite(inverse_history_gamma)) {
        return FC_FIXED_ERR_COEFFICIENT;
    }

    status = fc_fixed_coefficient_from_double(
        solver, h_to_q, &solver->h_to_q);
    if (status != FC_FIXED_OK) {
        return status;
    }
    status = fc_fixed_coefficient_from_double(
        solver, a21, &solver->efork.a21);
    if (status != FC_FIXED_OK) {
        return status;
    }
    status = fc_fixed_coefficient_from_double(
        solver, a31, &solver->efork.a31);
    if (status != FC_FIXED_OK) {
        return status;
    }
    status = fc_fixed_coefficient_from_double(
        solver, a32, &solver->efork.a32);
    if (status != FC_FIXED_OK) {
        return status;
    }
    status = fc_fixed_coefficient_from_double(
        solver, w1, &solver->efork.w1);
    if (status != FC_FIXED_OK) {
        return status;
    }
    status = fc_fixed_coefficient_from_double(
        solver, w2, &solver->efork.w2);
    if (status != FC_FIXED_OK) {
        return status;
    }
    status = fc_fixed_coefficient_from_double(
        solver, w3, &solver->efork.w3);
    if (status != FC_FIXED_OK) {
        return status;
    }

    for (stage = 0u; stage < 3u; ++stage) {
        for (lag = 0u;
             lag < solver->config.memory_length;
             ++lag) {
            const double lower = (double)lag + offsets[stage];
            const double difference =
                pow(lower + 1.0, exponent) -
                pow(lower, exponent);
            const double weight =
                difference * inverse_history_gamma;

            status = fc_fixed_coefficient_from_double(
                solver,
                weight,
                &solver->workspace->efork.weights[stage][lag]);
            if (status != FC_FIXED_OK) {
                return FC_FIXED_ERR_COEFFICIENT;
            }
        }
    }
    return FC_FIXED_OK;
}

static fc_fixed_status_t fc_fixed_prepare_gl(
    fc_fixed_solver_t *solver)
{
    double weight = 1.0;
    fc_fixed_status_t status;
    uint32_t index;

    status = fc_fixed_coefficient_from_double(
        solver,
        pow(solver->config.h, solver->config.q),
        &solver->h_to_q);
    if (status != FC_FIXED_OK) {
        return FC_FIXED_ERR_COEFFICIENT;
    }

    status = fc_fixed_coefficient_from_double(
        solver,
        weight,
        &solver->workspace->gl.weights[0]);
    if (status != FC_FIXED_OK) {
        return FC_FIXED_ERR_COEFFICIENT;
    }
    for (index = 1u;
         index <= solver->config.memory_length;
         ++index) {
        weight *=
            1.0 -
            ((solver->config.q + 1.0) / (double)index);
        status = fc_fixed_coefficient_from_double(
            solver,
            weight,
            &solver->workspace->gl.weights[index]);
        if (status != FC_FIXED_OK) {
            return FC_FIXED_ERR_COEFFICIENT;
        }
    }
    return FC_FIXED_OK;
}

static fc_fixed_status_t fc_fixed_prepare_m2sfrk(
    fc_fixed_solver_t *solver)
{
    const double gamma_1 = tgamma(solver->config.q + 1.0);
    const double gamma_2 =
        tgamma((2.0 * solver->config.q) + 1.0);
    const double h_to_q =
        pow(solver->config.h, solver->config.q);
    fc_fixed_status_t status;

    if (!fc_fixed_isfinite(gamma_1) ||
        !fc_fixed_isfinite(gamma_2) ||
        !fc_fixed_isfinite(h_to_q) ||
        (gamma_1 <= 0.0) ||
        (gamma_2 <= 0.0)) {
        return FC_FIXED_ERR_COEFFICIENT;
    }

    status = fc_fixed_coefficient_from_double(
        solver,
        h_to_q / gamma_1,
        &solver->m2sfrk.c2);
    if (status != FC_FIXED_OK) {
        return FC_FIXED_ERR_COEFFICIENT;
    }
    status = fc_fixed_coefficient_from_double(
        solver,
        (h_to_q * gamma_1) / gamma_2,
        &solver->m2sfrk.c4);
    if (status != FC_FIXED_OK) {
        return FC_FIXED_ERR_COEFFICIENT;
    }
    return FC_FIXED_OK;
}

fc_fixed_status_t fc_fixed_solver_init(
    fc_fixed_solver_t *solver,
    fc_fixed_workspace_t *workspace,
    const fc_fixed_config_t *config)
{
    fc_fixed_status_t status;
    uint32_t component;
    uint32_t parameter;

    if ((solver == NULL) || (config == NULL)) {
        return FC_FIXED_ERR_NULL;
    }
    if (!fc_fixed_config_is_valid(config)) {
        return FC_FIXED_ERR_CONFIG;
    }
    if ((config->method != FC_FIXED_METHOD_M2SFRK) &&
        (workspace == NULL)) {
        return FC_FIXED_ERR_WORKSPACE;
    }

    memset(solver, 0, sizeof(*solver));
    solver->config = *config;
    solver->workspace = workspace;

    for (parameter = 0u;
         parameter < FC_FIXED_PARAMETER_COUNT;
         ++parameter) {
        status = fc_fixed_from_double(
            config->parameters[parameter],
            &solver->arithmetic,
            &solver->parameters.v[parameter]);
        if (status != FC_FIXED_OK) {
            return status;
        }
    }
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        status = fc_fixed_from_double(
            config->initial_state[component],
            &solver->arithmetic,
            &solver->initial_state.v[component]);
        if (status != FC_FIXED_OK) {
            return status;
        }
    }

    if (config->method == FC_FIXED_METHOD_EFORK3) {
        status = fc_fixed_prepare_efork(solver);
    } else if (config->method == FC_FIXED_METHOD_GL_CAPUTO) {
        status = fc_fixed_prepare_gl(solver);
    } else {
        status = fc_fixed_prepare_m2sfrk(solver);
    }
    if (status != FC_FIXED_OK) {
        return status;
    }

    solver->initial_saturation_count =
        solver->arithmetic.saturation_count;
    solver->magic = FC_FIXED_SOLVER_MAGIC;
    return fc_fixed_solver_reset(solver);
}

static uint32_t fc_fixed_max_abs_raw(
    const fc_fixed_vec3_t *value)
{
    uint32_t maximum = 0u;
    uint32_t component;

    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        const int64_t raw = (int64_t)value->v[component];
        const uint32_t magnitude = (uint32_t)(
            (raw < 0) ? -raw : raw);
        if (magnitude > maximum) {
            maximum = magnitude;
        }
    }
    return maximum;
}

fc_fixed_status_t fc_fixed_solver_reset(fc_fixed_solver_t *solver)
{
    if (solver == NULL) {
        return FC_FIXED_ERR_NULL;
    }
    if ((solver->magic != FC_FIXED_SOLVER_MAGIC) ||
        ((solver->workspace == NULL) &&
         (solver->config.method != FC_FIXED_METHOD_M2SFRK))) {
        return FC_FIXED_ERR_NOT_INITIALIZED;
    }

    solver->state = solver->initial_state;
    solver->step_index = 0u;
    solver->ring_head = 0u;
    solver->ring_valid = 0u;
    solver->arithmetic.saturation_count =
        solver->initial_saturation_count;
    solver->diagnostics.steps_completed = 0u;
    solver->diagnostics.saturation_count =
        solver->arithmetic.saturation_count;
    solver->diagnostics.coefficient_saturation_count =
        solver->coefficient_saturation_count;
    solver->diagnostics.zeroed_nonzero_coefficient_count =
        solver->zeroed_nonzero_coefficient_count;
    solver->diagnostics.active_memory_terms = 0u;
    solver->diagnostics.max_abs_raw =
        fc_fixed_max_abs_raw(&solver->state);
    solver->diagnostics.last_status = FC_FIXED_OK;
    return FC_FIXED_OK;
}

static uint32_t fc_fixed_ring_index_at_lag(
    const fc_fixed_solver_t *solver,
    uint32_t lag)
{
    if (lag <= solver->ring_head) {
        return solver->ring_head - lag;
    }
    return solver->config.memory_length -
           (lag - solver->ring_head);
}

static void fc_fixed_ring_push(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t *ring,
    const fc_fixed_vec3_t *value)
{
    if (solver->ring_valid == 0u) {
        solver->ring_head = 0u;
    } else {
        ++solver->ring_head;
        if (solver->ring_head == solver->config.memory_length) {
            solver->ring_head = 0u;
        }
    }
    ring[solver->ring_head] = *value;
    if (solver->ring_valid < solver->config.memory_length) {
        ++solver->ring_valid;
    }
}

static void fc_fixed_efork_history(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t history[3])
{
    uint32_t stage;
    uint32_t lag;

    memset(history, 0, 3u * sizeof(history[0]));
    for (stage = 0u; stage < 3u; ++stage) {
        for (lag = 0u; lag < solver->ring_valid; ++lag) {
            const uint32_t ring_index =
                fc_fixed_ring_index_at_lag(solver, lag);
            uint32_t component;

            for (component = 0u;
                 component < FC_FIXED_STATE_DIMENSION;
                 ++component) {
                history[stage].v[component] = fc_fixed_add(
                    history[stage].v[component],
                    fc_fixed_mul_coefficient(
                        solver->workspace->efork.weights[stage][lag],
                        solver->workspace->efork
                            .increments[ring_index].v[component],
                        &solver->arithmetic),
                    &solver->arithmetic);
            }
        }
    }
}

static void fc_fixed_gl_history(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t *history)
{
    uint32_t lag;

    memset(history, 0, sizeof(*history));
    for (lag = 0u; lag < solver->ring_valid; ++lag) {
        const uint32_t ring_index =
            fc_fixed_ring_index_at_lag(solver, lag);
        uint32_t component;

        for (component = 0u;
             component < FC_FIXED_STATE_DIMENSION;
             ++component) {
            history->v[component] = fc_fixed_add(
                history->v[component],
                fc_fixed_mul_coefficient(
                    solver->workspace->gl.weights[lag + 1u],
                    solver->workspace->gl
                        .deviations[ring_index].v[component],
                    &solver->arithmetic),
                &solver->arithmetic);
        }
    }
}

static void fc_fixed_finish_step(
    fc_fixed_solver_t *solver,
    uint32_t terms_used,
    fc_fixed_vec3_t *output)
{
    ++solver->step_index;
    solver->diagnostics.steps_completed = solver->step_index;
    solver->diagnostics.saturation_count =
        solver->arithmetic.saturation_count;
    solver->diagnostics.coefficient_saturation_count =
        solver->coefficient_saturation_count;
    solver->diagnostics.zeroed_nonzero_coefficient_count =
        solver->zeroed_nonzero_coefficient_count;
    solver->diagnostics.active_memory_terms = terms_used;
    solver->diagnostics.max_abs_raw =
        fc_fixed_max_abs_raw(&solver->state);
    solver->diagnostics.last_status = FC_FIXED_OK;
    if (output != NULL) {
        *output = solver->state;
    }
}

static fc_fixed_status_t fc_fixed_step_efork(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t *output)
{
    fc_fixed_vec3_t history[3];
    fc_fixed_vec3_t rhs;
    fc_fixed_vec3_t k1;
    fc_fixed_vec3_t k2;
    fc_fixed_vec3_t k3;
    fc_fixed_vec3_t stage_state;
    fc_fixed_vec3_t next_state;
    fc_fixed_vec3_t increment;
    const uint32_t terms_used = solver->ring_valid;
    fc_fixed_status_t status;
    uint32_t component;

    fc_fixed_efork_history(solver, history);
    status = fc_fixed_rhs(
        solver->config.system,
        &solver->parameters,
        &solver->state,
        &solver->arithmetic,
        &rhs);
    if (status != FC_FIXED_OK) {
        return status;
    }

    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        k1.v[component] = fc_fixed_sub(
            fc_fixed_mul_coefficient(
                solver->h_to_q,
                rhs.v[component],
                &solver->arithmetic),
            history[0].v[component],
            &solver->arithmetic);
        stage_state.v[component] = fc_fixed_add(
            solver->state.v[component],
            fc_fixed_mul_coefficient(
                solver->efork.a21,
                k1.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
    }

    status = fc_fixed_rhs(
        solver->config.system,
        &solver->parameters,
        &stage_state,
        &solver->arithmetic,
        &rhs);
    if (status != FC_FIXED_OK) {
        return status;
    }
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        k2.v[component] = fc_fixed_sub(
            fc_fixed_mul_coefficient(
                solver->h_to_q,
                rhs.v[component],
                &solver->arithmetic),
            history[1].v[component],
            &solver->arithmetic);
        stage_state.v[component] = fc_fixed_mul_coefficient(
            (fc_fixed_coefficient_t)
                FC_FIXED_COEFFICIENT_SCALE,
            solver->state.v[component],
            &solver->arithmetic);
        stage_state.v[component] = fc_fixed_add(
            stage_state.v[component],
            fc_fixed_mul_coefficient(
                solver->efork.a31,
                k1.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
        stage_state.v[component] = fc_fixed_add(
            stage_state.v[component],
            fc_fixed_mul_coefficient(
                solver->efork.a32,
                k2.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
    }

    status = fc_fixed_rhs(
        solver->config.system,
        &solver->parameters,
        &stage_state,
        &solver->arithmetic,
        &rhs);
    if (status != FC_FIXED_OK) {
        return status;
    }
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        k3.v[component] = fc_fixed_sub(
            fc_fixed_mul_coefficient(
                solver->h_to_q,
                rhs.v[component],
                &solver->arithmetic),
            history[2].v[component],
            &solver->arithmetic);
        next_state.v[component] = fc_fixed_mul_coefficient(
            (fc_fixed_coefficient_t)
                FC_FIXED_COEFFICIENT_SCALE,
            solver->state.v[component],
            &solver->arithmetic);
        next_state.v[component] = fc_fixed_add(
            next_state.v[component],
            fc_fixed_mul_coefficient(
                solver->efork.w1,
                k1.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
        next_state.v[component] = fc_fixed_add(
            next_state.v[component],
            fc_fixed_mul_coefficient(
                solver->efork.w2,
                k2.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
        next_state.v[component] = fc_fixed_add(
            next_state.v[component],
            fc_fixed_mul_coefficient(
                solver->efork.w3,
                k3.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
        increment.v[component] = fc_fixed_sub(
            next_state.v[component],
            solver->state.v[component],
            &solver->arithmetic);
    }

    fc_fixed_ring_push(
        solver,
        solver->workspace->efork.increments,
        &increment);
    solver->state = next_state;
    fc_fixed_finish_step(solver, terms_used, output);
    return FC_FIXED_OK;
}

static fc_fixed_status_t fc_fixed_step_gl(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t *output)
{
    fc_fixed_vec3_t history;
    fc_fixed_vec3_t rhs;
    fc_fixed_vec3_t next_u;
    fc_fixed_vec3_t next_state;
    const uint32_t terms_used = solver->ring_valid;
    fc_fixed_status_t status;
    uint32_t component;

    fc_fixed_gl_history(solver, &history);
    status = fc_fixed_rhs(
        solver->config.system,
        &solver->parameters,
        &solver->state,
        &solver->arithmetic,
        &rhs);
    if (status != FC_FIXED_OK) {
        return status;
    }

    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        next_u.v[component] = fc_fixed_sub(
            fc_fixed_mul_coefficient(
                solver->h_to_q,
                rhs.v[component],
                &solver->arithmetic),
            history.v[component],
            &solver->arithmetic);
        next_state.v[component] = fc_fixed_add(
            solver->initial_state.v[component],
            next_u.v[component],
            &solver->arithmetic);
    }

    fc_fixed_ring_push(
        solver,
        solver->workspace->gl.deviations,
        &next_u);
    solver->state = next_state;
    fc_fixed_finish_step(solver, terms_used, output);
    return FC_FIXED_OK;
}

static fc_fixed_status_t fc_fixed_step_m2sfrk(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t *output)
{
    fc_fixed_vec3_t rhs;
    fc_fixed_vec3_t stage_state;
    fc_fixed_vec3_t next_state;
    fc_fixed_status_t status;
    uint32_t component;

    status = fc_fixed_rhs(
        solver->config.system,
        &solver->parameters,
        &solver->state,
        &solver->arithmetic,
        &rhs);
    if (status != FC_FIXED_OK) {
        return status;
    }
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        stage_state.v[component] = fc_fixed_add(
            solver->state.v[component],
            fc_fixed_mul_coefficient(
                solver->m2sfrk.c4,
                rhs.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
    }

    status = fc_fixed_rhs(
        solver->config.system,
        &solver->parameters,
        &stage_state,
        &solver->arithmetic,
        &rhs);
    if (status != FC_FIXED_OK) {
        return status;
    }
    for (component = 0u;
         component < FC_FIXED_STATE_DIMENSION;
         ++component) {
        next_state.v[component] = fc_fixed_add(
            solver->state.v[component],
            fc_fixed_mul_coefficient(
                solver->m2sfrk.c2,
                rhs.v[component],
                &solver->arithmetic),
            &solver->arithmetic);
    }

    solver->state = next_state;
    fc_fixed_finish_step(solver, 0u, output);
    return FC_FIXED_OK;
}

fc_fixed_status_t fc_fixed_solver_step(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t *output)
{
    fc_fixed_status_t status;

    if (solver == NULL) {
        return FC_FIXED_ERR_NULL;
    }
    if ((solver->magic != FC_FIXED_SOLVER_MAGIC) ||
        ((solver->workspace == NULL) &&
         (solver->config.method != FC_FIXED_METHOD_M2SFRK))) {
        return FC_FIXED_ERR_NOT_INITIALIZED;
    }

    if (solver->config.method == FC_FIXED_METHOD_EFORK3) {
        status = fc_fixed_step_efork(solver, output);
    } else if (solver->config.method ==
               FC_FIXED_METHOD_GL_CAPUTO) {
        status = fc_fixed_step_gl(solver, output);
    } else if (solver->config.method ==
               FC_FIXED_METHOD_M2SFRK) {
        status = fc_fixed_step_m2sfrk(solver, output);
    } else {
        status = FC_FIXED_ERR_METHOD;
    }

    solver->diagnostics.last_status = status;
    solver->diagnostics.saturation_count =
        solver->arithmetic.saturation_count;
    solver->diagnostics.coefficient_saturation_count =
        solver->coefficient_saturation_count;
    solver->diagnostics.zeroed_nonzero_coefficient_count =
        solver->zeroed_nonzero_coefficient_count;
    return status;
}

const fc_fixed_vec3_t *fc_fixed_solver_state(
    const fc_fixed_solver_t *solver)
{
    if ((solver == NULL) ||
        (solver->magic != FC_FIXED_SOLVER_MAGIC)) {
        return NULL;
    }
    return &solver->state;
}

const fc_fixed_diagnostics_t *fc_fixed_solver_diagnostics(
    const fc_fixed_solver_t *solver)
{
    if ((solver == NULL) ||
        (solver->magic != FC_FIXED_SOLVER_MAGIC)) {
        return NULL;
    }
    return &solver->diagnostics;
}
