#ifndef FRACTIONAL_CHAOS_FIXED_H
#define FRACTIONAL_CHAOS_FIXED_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Mixed fixed-point contract used by the experiment. States and system
 * parameters are signed Q1.14.14 (one sign, fourteen integer and fourteen
 * fractional bits). Integration coefficients and history weights are signed
 * Q1.30, preserving the long-memory tail while keeping all products in int64.
 */
#define FC_FIXED_FRACTION_BITS       (14u)
#define FC_FIXED_SCALE               (16384)
#define FC_FIXED_RAW_MIN             (-268435456)
#define FC_FIXED_RAW_MAX             (268435455)
#define FC_FIXED_COEFFICIENT_BITS    (30u)
#define FC_FIXED_COEFFICIENT_SCALE   (INT64_C(1073741824))
#define FC_FIXED_COEFFICIENT_RAW_MIN (INT32_MIN)
#define FC_FIXED_COEFFICIENT_RAW_MAX (INT32_MAX)
#define FC_FIXED_STATE_DIMENSION     (3u)
#define FC_FIXED_SYSTEM_COUNT        (3u)
#define FC_FIXED_MAX_MEMORY_LENGTH   (2000u)

typedef int32_t fc_fixed_t;
typedef int32_t fc_fixed_coefficient_t;

typedef struct {
    fc_fixed_t v[FC_FIXED_STATE_DIMENSION];
} fc_fixed_vec3_t;

typedef enum {
    FC_FIXED_SYSTEM_LORENZ = 0,
    FC_FIXED_SYSTEM_ROSSLER = 1,
    FC_FIXED_SYSTEM_CHEN = 2
} fc_fixed_system_t;

typedef enum {
    FC_FIXED_METHOD_EFORK3 = 0,
    FC_FIXED_METHOD_GL_CAPUTO = 1,
    FC_FIXED_METHOD_M2SFRK = 2
} fc_fixed_method_t;

typedef enum {
    FC_FIXED_OK = 0,
    FC_FIXED_ERR_NULL = -1,
    FC_FIXED_ERR_CONFIG = -2,
    FC_FIXED_ERR_WORKSPACE = -3,
    FC_FIXED_ERR_COEFFICIENT = -4,
    FC_FIXED_ERR_NONFINITE = -5,
    FC_FIXED_ERR_NOT_INITIALIZED = -6,
    FC_FIXED_ERR_METHOD = -7,
    FC_FIXED_ERR_RANGE = -8
} fc_fixed_status_t;

typedef struct {
    uint64_t saturation_count;
} fc_fixed_arithmetic_t;

typedef struct {
    fc_fixed_system_t system;
    fc_fixed_method_t method;
    double q;
    double h;
    uint32_t memory_length;
    double parameters[FC_FIXED_STATE_DIMENSION];
    double initial_state[FC_FIXED_STATE_DIMENSION];
} fc_fixed_config_t;

typedef struct {
    fc_fixed_coefficient_t a21;
    fc_fixed_coefficient_t a31;
    fc_fixed_coefficient_t a32;
    fc_fixed_coefficient_t w1;
    fc_fixed_coefficient_t w2;
    fc_fixed_coefficient_t w3;
} fc_fixed_efork_coefficients_t;

typedef struct {
    fc_fixed_coefficient_t c2;
    fc_fixed_coefficient_t c4;
} fc_fixed_m2sfrk_coefficients_t;

typedef struct {
    fc_fixed_vec3_t increments[FC_FIXED_MAX_MEMORY_LENGTH];
    fc_fixed_coefficient_t weights[3][FC_FIXED_MAX_MEMORY_LENGTH];
} fc_fixed_efork_workspace_t;

typedef struct {
    fc_fixed_vec3_t deviations[FC_FIXED_MAX_MEMORY_LENGTH];
    fc_fixed_coefficient_t weights[FC_FIXED_MAX_MEMORY_LENGTH + 1u];
} fc_fixed_gl_workspace_t;

typedef union {
    fc_fixed_efork_workspace_t efork;
    fc_fixed_gl_workspace_t gl;
} fc_fixed_workspace_t;

typedef struct {
    uint64_t steps_completed;
    uint64_t saturation_count;
    uint64_t coefficient_saturation_count;
    uint64_t zeroed_nonzero_coefficient_count;
    uint32_t active_memory_terms;
    uint32_t max_abs_raw;
    fc_fixed_status_t last_status;
} fc_fixed_diagnostics_t;

typedef struct {
    uint32_t magic;
    fc_fixed_config_t config;
    fc_fixed_workspace_t *workspace;
    fc_fixed_vec3_t parameters;
    fc_fixed_vec3_t initial_state;
    fc_fixed_vec3_t state;
    fc_fixed_coefficient_t h_to_q;
    fc_fixed_efork_coefficients_t efork;
    fc_fixed_m2sfrk_coefficients_t m2sfrk;
    uint64_t step_index;
    uint64_t initial_saturation_count;
    uint64_t coefficient_saturation_count;
    uint64_t zeroed_nonzero_coefficient_count;
    uint32_t ring_head;
    uint32_t ring_valid;
    fc_fixed_arithmetic_t arithmetic;
    fc_fixed_diagnostics_t diagnostics;
} fc_fixed_solver_t;

/*
 * All arithmetic primitives round to nearest with exact half cases away from
 * zero and saturate to FC_FIXED_RAW_MIN..FC_FIXED_RAW_MAX. Passing a NULL
 * arithmetic context is allowed for isolated calculations, but suppresses the
 * saturation counter.
 */
fc_fixed_status_t fc_fixed_from_double(
    double value,
    fc_fixed_arithmetic_t *arithmetic,
    fc_fixed_t *output);

double fc_fixed_to_double(fc_fixed_t value);

fc_fixed_t fc_fixed_add(
    fc_fixed_t left,
    fc_fixed_t right,
    fc_fixed_arithmetic_t *arithmetic);

fc_fixed_t fc_fixed_sub(
    fc_fixed_t left,
    fc_fixed_t right,
    fc_fixed_arithmetic_t *arithmetic);

fc_fixed_t fc_fixed_mul(
    fc_fixed_t left,
    fc_fixed_t right,
    fc_fixed_arithmetic_t *arithmetic);

/*
 * Mixed-format product used by every integration coefficient and history
 * weight: Q1.30 coefficient times Q1.14.14 state, rounded back to Q1.14.14.
 */
fc_fixed_t fc_fixed_mul_coefficient(
    fc_fixed_coefficient_t coefficient,
    fc_fixed_t value,
    fc_fixed_arithmetic_t *arithmetic);

fc_fixed_status_t fc_fixed_config_from_manifest(
    fc_fixed_system_t system,
    fc_fixed_method_t method,
    fc_fixed_config_t *config);

fc_fixed_status_t fc_fixed_rhs(
    fc_fixed_system_t system,
    const fc_fixed_vec3_t *parameters,
    const fc_fixed_vec3_t *state,
    fc_fixed_arithmetic_t *arithmetic,
    fc_fixed_vec3_t *derivative);

fc_fixed_status_t fc_fixed_solver_init(
    fc_fixed_solver_t *solver,
    fc_fixed_workspace_t *workspace,
    const fc_fixed_config_t *config);

fc_fixed_status_t fc_fixed_solver_reset(fc_fixed_solver_t *solver);

fc_fixed_status_t fc_fixed_solver_step(
    fc_fixed_solver_t *solver,
    fc_fixed_vec3_t *output);

const fc_fixed_vec3_t *fc_fixed_solver_state(
    const fc_fixed_solver_t *solver);

const fc_fixed_diagnostics_t *fc_fixed_solver_diagnostics(
    const fc_fixed_solver_t *solver);

#ifdef __cplusplus
}
#endif

#endif
