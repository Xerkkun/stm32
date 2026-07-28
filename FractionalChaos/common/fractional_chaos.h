#ifndef FRACTIONAL_CHAOS_H
#define FRACTIONAL_CHAOS_H

#include <float.h>
#include <stddef.h>
#include <stdint.h>
#include <stdalign.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FC_STATE_DIMENSION       (3u)
#define FC_SYSTEM_COUNT          (3u)
#define FC_MAX_MEMORY_LENGTH     (2000u)
#define FC_SUM_BLOCK_LENGTH      (32u)

#ifndef FC_REQUIRE_PRECOMPUTED_TABLES
#define FC_REQUIRE_PRECOMPUTED_TABLES (0)
#endif

#if (FC_REQUIRE_PRECOMPUTED_TABLES != 0) && \
    (FC_REQUIRE_PRECOMPUTED_TABLES != 1)
#error "FC_REQUIRE_PRECOMPUTED_TABLES must be 0 or 1"
#endif

_Static_assert(sizeof(float) == 4u, "fractional_chaos requires IEEE-754 float32");
_Static_assert(FLT_RADIX == 2, "fractional_chaos requires a binary float");
_Static_assert(FLT_MANT_DIG == 24, "fractional_chaos requires binary32 precision");

typedef float fc_real_t;

typedef struct {
    fc_real_t v[FC_STATE_DIMENSION];
} fc_vec3f_t;

typedef enum {
    FC_SYSTEM_LORENZ = 0,
    FC_SYSTEM_ROSSLER = 1,
    FC_SYSTEM_CHEN = 2
} fc_system_t;

/*
 * GL_NATIVE is intentionally absent. The kernel implements only the
 * Caputo-aligned GL recurrence used in the primary experiment.
 */
typedef enum {
    FC_METHOD_EFORK3 = 0,
    FC_METHOD_GL_CAPUTO = 1
} fc_method_t;

typedef enum {
    FC_OK = 0,
    FC_ERR_NULL = -1,
    FC_ERR_CONFIG = -2,
    FC_ERR_WORKSPACE = -3,
    FC_ERR_COEFFICIENT = -4,
    FC_ERR_NONFINITE = -5,
    FC_ERR_NOT_INITIALIZED = -6,
    FC_ERR_METHOD = -7,
    FC_ERR_RANGE = -8
} fc_status_t;

typedef struct {
    fc_system_t system;
    const char *name;
    fc_real_t q;
    fc_real_t h;
    fc_real_t memory_seconds;
    uint32_t memory_length;
    fc_real_t parameters[FC_STATE_DIMENSION];
    fc_vec3f_t initial_state;
} fc_manifest_t;

typedef struct fc_precomputed_tables fc_precomputed_tables_t;

typedef struct {
    fc_system_t system;
    fc_method_t method;
    fc_real_t q;
    fc_real_t h;
    uint32_t memory_length;
    fc_real_t parameters[FC_STATE_DIMENSION];
    fc_vec3f_t initial_state;
    const fc_precomputed_tables_t *precomputed_tables;
} fc_config_t;

typedef struct {
    fc_real_t c2;
    fc_real_t c3;
    fc_real_t a21;
    fc_real_t a31;
    fc_real_t a32;
    fc_real_t w1;
    fc_real_t w2;
    fc_real_t w3;
} fc_efork_coefficients_t;

/*
 * Optional tables generated outside the target. When supplied, init validates
 * q, h, M and method, then copies the exact float32 words to the caller's
 * workspace (normally DTCM). A NULL table pointer selects the stable runtime
 * initializer unless FC_REQUIRE_PRECOMPUTED_TABLES=1; neither path evaluates
 * transcendental functions in step().
 */
struct fc_precomputed_tables {
    fc_method_t method;
    fc_real_t q;
    fc_real_t h;
    uint32_t memory_length;
    fc_real_t h_to_q;
    fc_efork_coefficients_t efork;
    const fc_real_t *efork_weights[3];
    const fc_real_t *gl_weights;
    const char *table_sha256;
};

typedef struct {
    uint64_t steps_completed;
    uint32_t active_memory_terms;
    fc_status_t last_status;
    fc_real_t max_abs_state;
    fc_real_t last_history_abs_max;
} fc_diagnostics_t;

typedef struct {
    fc_vec3f_t increments[FC_MAX_MEMORY_LENGTH];
    fc_real_t weights[3][FC_MAX_MEMORY_LENGTH];
} fc_efork_workspace_t;

typedef struct {
    fc_vec3f_t deviations[FC_MAX_MEMORY_LENGTH];
    fc_real_t weights[FC_MAX_MEMORY_LENGTH + 1u];
} fc_gl_workspace_t;

typedef union {
    fc_efork_workspace_t efork;
    fc_gl_workspace_t gl;
} fc_workspace_storage_t;

/*
 * The alignment is sufficient for a cache line on the target boards. The
 * platform linker may place the complete object in DTCM without changing the
 * numerical kernel.
 */
typedef struct {
    alignas(32) fc_workspace_storage_t storage;
} fc_workspace_t;

typedef struct {
    uint32_t magic;
    fc_config_t config;
    fc_workspace_t *workspace;
    fc_vec3f_t state;
    fc_vec3f_t gl_current_u;
    fc_real_t h_to_q;
    fc_efork_coefficients_t efork;
    uint64_t step_index;
    uint32_t ring_head;
    uint32_t ring_valid;
    fc_diagnostics_t diagnostics;
} fc_solver_t;

extern const fc_manifest_t FC_MANIFESTS[FC_SYSTEM_COUNT];

/*
 * Symbols emitted by tests/generate_tables.py when the corresponding
 * generated source files are linked into a platform target.
 */
extern const fc_precomputed_tables_t FC_LORENZ_EFORK_TABLES;
extern const fc_precomputed_tables_t FC_LORENZ_GL_TABLES;
extern const fc_precomputed_tables_t FC_ROSSLER_EFORK_TABLES;
extern const fc_precomputed_tables_t FC_ROSSLER_GL_TABLES;
extern const fc_precomputed_tables_t FC_CHEN_EFORK_TABLES;
extern const fc_precomputed_tables_t FC_CHEN_GL_TABLES;

const fc_manifest_t *fc_manifest(fc_system_t system);

fc_status_t fc_config_from_manifest(
    fc_system_t system,
    fc_method_t method,
    fc_config_t *config);

fc_status_t fc_rhs(
    fc_system_t system,
    const fc_real_t parameters[FC_STATE_DIMENSION],
    const fc_vec3f_t *state,
    fc_vec3f_t *derivative);

size_t fc_active_workspace_bytes(
    fc_method_t method,
    uint32_t memory_length);

fc_status_t fc_solver_init(
    fc_solver_t *solver,
    fc_workspace_t *workspace,
    const fc_config_t *config);

/*
 * Reset preserves the coefficient tables. It only restores the initial state
 * and invalidates the ring contents logically, so it is suitable for repeated
 * cold-start acquisitions.
 */
fc_status_t fc_solver_reset(fc_solver_t *solver);

fc_status_t fc_solver_step(
    fc_solver_t *solver,
    fc_vec3f_t *output);

const fc_vec3f_t *fc_solver_state(const fc_solver_t *solver);
const fc_config_t *fc_solver_config(const fc_solver_t *solver);
const fc_diagnostics_t *fc_solver_diagnostics(const fc_solver_t *solver);

fc_status_t fc_solver_efork_coefficients(
    const fc_solver_t *solver,
    fc_efork_coefficients_t *coefficients);

fc_status_t fc_solver_efork_weight(
    const fc_solver_t *solver,
    uint32_t stage,
    uint32_t lag,
    fc_real_t *weight);

fc_status_t fc_solver_gl_weight(
    const fc_solver_t *solver,
    uint32_t index,
    fc_real_t *weight);

#ifdef __cplusplus
}
#endif

#endif
