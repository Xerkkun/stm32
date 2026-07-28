#include "main.h"

#include "fractional_chaos.h"
#include "fractional_chaos_fixed.h"
#include "h755_shared_memory.h"

#include <stddef.h>
#include <stdint.h>

#ifndef FC_SYSTEM_ID
#define FC_SYSTEM_ID 0
#endif

#ifndef FC_METHOD_ID
#define FC_METHOD_ID 0
#endif

#ifndef FC_H755_FIXED_POINT
#define FC_H755_FIXED_POINT 0
#endif

#if !FC_H755_FIXED_POINT && !defined(FC_PRECOMPUTED_TABLES_SYMBOL)
#error "Each H755 M7 target must select one generated coefficient table"
#endif

#ifndef FC_H755_OUTPUT_DECIMATION
#define FC_H755_OUTPUT_DECIMATION 16U
#endif

#ifndef FC_H755_BENCHMARK_MODE
#define FC_H755_BENCHMARK_MODE 0
#endif

#ifndef FC_H755_CLOCK_480
#define FC_H755_CLOCK_480 0
#endif

#if FC_H755_CLOCK_480 && !defined(FC_H755_LDO_MODIFICATION_CONFIRMED)
#error "480/240 MHz requires the explicit LDO hardware-modification guard"
#endif

#define FC_H755_HSEM_BOOT_ID       0U
#define FC_H755_BOOT_TIMEOUT       0xFFFFU
#define FC_H755_DTCM_BYTES         (128U * 1024U)
#define FC_BENCHMARK_TIMED_STEPS   10000U
#define FC_BENCHMARK_BLOCK_VALUES  4U

#if FC_SYSTEM_ID == 1
#define FC_BENCHMARK_WARMUP_STEPS  5000U
#else
#define FC_BENCHMARK_WARMUP_STEPS  2000U
#endif

#if FC_H755_CLOCK_480
#define FC_H755_M7_CLOCK_HZ        480000000U
#define FC_H755_HCLK_HZ            240000000U
#else
#define FC_H755_M7_CLOCK_HZ        400000000U
#define FC_H755_HCLK_HZ            200000000U
#endif

_Static_assert(
    (FC_SYSTEM_ID >= FC_SYSTEM_LORENZ) &&
    (FC_SYSTEM_ID <= FC_SYSTEM_CHEN),
    "FC_SYSTEM_ID debe seleccionar Lorenz, Rossler o Chen");
_Static_assert(
    (FC_METHOD_ID == FC_METHOD_EFORK3) ||
    (FC_METHOD_ID == FC_METHOD_GL_CAPUTO) ||
    (FC_METHOD_ID == FC_METHOD_M2SFRK),
    "FC_METHOD_ID debe seleccionar EFORK3, GL_CAPUTO o M2SFRK");
_Static_assert(
    FC_H755_OUTPUT_DECIMATION > 0U,
    "La decimación debe ser mayor que cero");
_Static_assert(
    (FC_H755_BENCHMARK_MODE == 0) ||
    (FC_H755_BENCHMARK_MODE == 1),
    "FC_H755_BENCHMARK_MODE debe ser cero o uno");
_Static_assert(
    (FC_BENCHMARK_TIMED_STEPS % FC_BENCHMARK_BLOCK_VALUES) == 0U,
    "El benchmark debe emitir bloques completos de cuatro conteos");

typedef struct {
#if FC_H755_FIXED_POINT
    fc_fixed_solver_t solver;
#if FC_METHOD_ID != 2
    fc_fixed_workspace_t workspace;
#endif
#else
    fc_solver_t solver;
#if FC_METHOD_ID != 2
    fc_workspace_t workspace;
#endif
#endif
} fc_h755_solver_storage_t;

#if FC_H755_FIXED_POINT
typedef fc_fixed_solver_t fc_h755_solver_t;
typedef fc_fixed_vec3_t fc_h755_state_t;
typedef fc_fixed_status_t fc_h755_status_t;
#define FC_H755_STATUS_OK FC_FIXED_OK
#else
typedef fc_solver_t fc_h755_solver_t;
typedef fc_vec3f_t fc_h755_state_t;
typedef fc_status_t fc_h755_status_t;
#define FC_H755_STATUS_OK FC_OK
#endif

_Static_assert(
    sizeof(fc_h755_solver_storage_t) <= FC_H755_DTCM_BYTES,
    "El integrador y el historial deben caber en DTCM");
_Static_assert(
    (sizeof(fc_h755_solver_storage_t) % sizeof(uint32_t)) == 0U,
    "El almacenamiento del integrador debe poder borrarse por palabras");

static fc_h755_solver_storage_t g_solver_storage
    __attribute__((section(".solver"), aligned(32), used));
#if FC_H755_BENCHMARK_MODE
static uint32_t g_benchmark_cycles[FC_BENCHMARK_TIMED_STEPS]
    __attribute__((aligned(32)));
#endif
#if FC_H755_FIXED_POINT
static uint8_t g_fixed_status_flags;
#endif

static void MPU_Config_Shared(void);
static void SystemClock_Config(void);
static void configure_floating_point(void);
static void cycle_counter_init(void);
static void clear_solver_storage(void);
static void wait_for_cm4_stop(void);
static void release_cm4(void);
static void wait_for_cm4_ready(void);
static uint32_t solver_step_cycles(
    fc_h755_solver_t *solver,
    fc_h755_state_t *state,
    fc_h755_status_t *status);
static uint8_t sample_status_with_fixed_diagnostics(uint8_t status);
static void publish_sample(
    const fc_h755_state_t *state,
    uint32_t sequence,
    uint32_t cycles,
    uint8_t status);
#if FC_H755_BENCHMARK_MODE
static void run_timing_benchmark(
    fc_h755_solver_t *solver,
    fc_h755_state_t *state);
static void publish_timing_block(
    uint32_t first_index,
    const uint32_t cycles[FC_BENCHMARK_BLOCK_VALUES],
    uint8_t status);
#endif

int main(void)
{
#if FC_H755_FIXED_POINT
    fc_fixed_config_t config;
#else
    fc_config_t config;
#endif
    fc_h755_state_t state = {{0}};
#if !FC_H755_BENCHMARK_MODE
    uint32_t sequence = 0U;
    uint32_t decimation_counter = 0U;
#endif

    MPU_Config_Shared();
    SCB_EnableICache();
    SCB_EnableDCache();

    wait_for_cm4_stop();
    HAL_Init();
    SystemClock_Config();
    configure_floating_point();
    cycle_counter_init();

    g_fc_h755_cm4_ready = 0U;
    g_fc_h755_cm4_diagnostics.magic = 0U;
    fc_shared_queue_initialize(&g_fc_h755_queue);
    __DMB();

    release_cm4();
    wait_for_cm4_ready();

    /*
     * Ya no se usa una base de tiempo HAL. Así se evita que SysTick altere las
     * mediciones DWT y se deja activo únicamente el trabajo solicitado.
     */
    HAL_SuspendTick();

    clear_solver_storage();
#if FC_H755_FIXED_POINT
    if (fc_fixed_config_from_manifest(
            (fc_fixed_system_t)FC_SYSTEM_ID,
            (fc_fixed_method_t)FC_METHOD_ID,
            &config) != FC_FIXED_OK) {
        Error_Handler();
    }
    if (fc_fixed_solver_init(
            &g_solver_storage.solver,
#if FC_METHOD_ID == 2
            NULL,
#else
            &g_solver_storage.workspace,
#endif
            &config) != FC_FIXED_OK) {
        Error_Handler();
    }
#else
    if (fc_config_from_manifest(
            (fc_system_t)FC_SYSTEM_ID,
            (fc_method_t)FC_METHOD_ID,
            &config) != FC_OK) {
        Error_Handler();
    }
    config.precomputed_tables = &FC_PRECOMPUTED_TABLES_SYMBOL;
    if (fc_solver_init(
            &g_solver_storage.solver,
#if FC_METHOD_ID == 2
            NULL,
#else
            &g_solver_storage.workspace,
#endif
            &config) != FC_OK) {
        Error_Handler();
    }
#endif

#if FC_H755_BENCHMARK_MODE
    run_timing_benchmark(&g_solver_storage.solver, &state);
#else
    for (;;) {
        fc_h755_status_t status;
        const uint32_t cycles = solver_step_cycles(
            &g_solver_storage.solver,
            &state,
            &status);

        ++sequence;
        if (status != FC_H755_STATUS_OK) {
#if FC_H755_FIXED_POINT
            const fc_fixed_vec3_t *last =
                fc_fixed_solver_state(&g_solver_storage.solver);
#else
            const fc_vec3f_t *last =
                fc_solver_state(&g_solver_storage.solver);
#endif
            publish_sample(
                (last != NULL) ? last : &state,
                sequence,
                cycles,
                FC_SAMPLE_STATUS_NONFINITE);
            for (;;) {
                __WFE();
            }
        }

        ++decimation_counter;
        if (decimation_counter == FC_H755_OUTPUT_DECIMATION) {
            decimation_counter = 0U;
            publish_sample(
                &state,
                sequence,
                cycles,
                FC_SAMPLE_STATUS_OK);
        }
    }
#endif
}

static void MPU_Config_Shared(void)
{
    MPU_Region_InitTypeDef region = {0};

    HAL_MPU_Disable();

    region.Enable = MPU_REGION_ENABLE;
    region.Number = MPU_REGION_NUMBER0;
    region.BaseAddress = FC_H755_SHARED_BASE;
    region.Size = MPU_REGION_SIZE_64KB;
    region.SubRegionDisable = 0x00U;
    region.TypeExtField = MPU_TEX_LEVEL0;
    region.AccessPermission = MPU_REGION_FULL_ACCESS;
    region.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
    region.IsShareable = MPU_ACCESS_SHAREABLE;
    region.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
    region.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;

    HAL_MPU_ConfigRegion(&region);
    HAL_MPU_Enable(MPU_PRIVILEGED_DEFAULT);
    __DSB();
    __ISB();
}

static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef oscillator = {0};
    RCC_ClkInitTypeDef clocks = {0};

#if FC_H755_CLOCK_480
    if (HAL_PWREx_ConfigSupply(PWR_LDO_SUPPLY) != HAL_OK) {
        Error_Handler();
    }
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE0);
#else
    if (HAL_PWREx_ConfigSupply(PWR_DIRECT_SMPS_SUPPLY) != HAL_OK) {
        Error_Handler();
    }
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);
#endif
    while (!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {
    }

    oscillator.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    oscillator.HSEState = RCC_HSE_BYPASS;
    oscillator.PLL.PLLState = RCC_PLL_ON;
    oscillator.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    oscillator.PLL.PLLM = 4U;
#if FC_H755_CLOCK_480
    oscillator.PLL.PLLN = 480U;
#else
    oscillator.PLL.PLLN = 400U;
#endif
    oscillator.PLL.PLLP = 2U;
    oscillator.PLL.PLLQ = 4U;
    oscillator.PLL.PLLR = 2U;
    oscillator.PLL.PLLRGE = RCC_PLL1VCIRANGE_1;
    oscillator.PLL.PLLVCOSEL = RCC_PLL1VCOWIDE;
    oscillator.PLL.PLLFRACN = 0U;
    if (HAL_RCC_OscConfig(&oscillator) != HAL_OK) {
        Error_Handler();
    }

    clocks.ClockType =
        RCC_CLOCKTYPE_SYSCLK |
        RCC_CLOCKTYPE_HCLK |
        RCC_CLOCKTYPE_D1PCLK1 |
        RCC_CLOCKTYPE_PCLK1 |
        RCC_CLOCKTYPE_PCLK2 |
        RCC_CLOCKTYPE_D3PCLK1;
    clocks.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clocks.SYSCLKDivider = RCC_SYSCLK_DIV1;
    clocks.AHBCLKDivider = RCC_HCLK_DIV2;
    clocks.APB3CLKDivider = RCC_APB3_DIV2;
    clocks.APB1CLKDivider = RCC_APB1_DIV2;
    clocks.APB2CLKDivider = RCC_APB2_DIV2;
    clocks.APB4CLKDivider = RCC_APB4_DIV2;
    if (HAL_RCC_ClockConfig(&clocks, FLASH_LATENCY_4) != HAL_OK) {
        Error_Handler();
    }

    SystemCoreClockUpdate();
    if ((SystemCoreClock != FC_H755_M7_CLOCK_HZ) ||
        (HAL_RCC_GetHCLKFreq() != FC_H755_HCLK_HZ)) {
        Error_Handler();
    }
}

static void configure_floating_point(void)
{
#if (__FPU_PRESENT == 1U) && (__FPU_USED == 1U)
    const uint32_t non_ieee_mask =
        (3UL << 22U) |
        (1UL << 24U) |
        (1UL << 25U);
    uint32_t fpscr = __get_FPSCR();

    fpscr &= ~non_ieee_mask;
    __set_FPSCR(fpscr);
    __DSB();
    __ISB();
#endif
}

static void cycle_counter_init(void)
{
    uint32_t probe;

    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
#if defined(__CORTEX_M) && (__CORTEX_M == 7U)
    DWT->LAR = 0xC5ACCE55UL;
#endif
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    __DSB();
    __ISB();
    probe = DWT->CYCCNT;
    __NOP();
    __NOP();
    __NOP();
    if (DWT->CYCCNT == probe) {
        Error_Handler();
    }
}

static void clear_solver_storage(void)
{
    volatile uint32_t *word =
        (volatile uint32_t *)(void *)&g_solver_storage;
    const size_t count =
        sizeof(g_solver_storage) / sizeof(uint32_t);
    size_t index;

    for (index = 0U; index < count; ++index) {
        word[index] = 0U;
    }
}

static void wait_for_cm4_stop(void)
{
    uint32_t timeout = FC_H755_BOOT_TIMEOUT;

    while ((__HAL_RCC_GET_FLAG(RCC_FLAG_D2CKRDY) != RESET) &&
           (timeout > 0U)) {
        --timeout;
    }
    if (timeout == 0U) {
        Error_Handler();
    }
}

static void release_cm4(void)
{
    uint32_t timeout = FC_H755_BOOT_TIMEOUT;

    __HAL_RCC_HSEM_CLK_ENABLE();
    if (HAL_HSEM_FastTake(FC_H755_HSEM_BOOT_ID) != HAL_OK) {
        Error_Handler();
    }
    HAL_HSEM_Release(FC_H755_HSEM_BOOT_ID, 0U);

    while ((__HAL_RCC_GET_FLAG(RCC_FLAG_D2CKRDY) == RESET) &&
           (timeout > 0U)) {
        --timeout;
    }
    if (timeout == 0U) {
        Error_Handler();
    }
}

static void wait_for_cm4_ready(void)
{
    while (g_fc_h755_cm4_ready != FC_H755_CM4_READY_MAGIC) {
        __WFE();
    }
    __DMB();
}

static uint32_t solver_step_cycles(
    fc_h755_solver_t *solver,
    fc_h755_state_t *state,
    fc_h755_status_t *status)
{
    const uint32_t saved_primask = __get_PRIMASK();
    uint32_t start;
    uint32_t elapsed;

    __disable_irq();
    __DSB();
    __ISB();
    start = DWT->CYCCNT;
#if FC_H755_FIXED_POINT
    *status = fc_fixed_solver_step(solver, state);
#else
    *status = fc_solver_step(solver, state);
#endif
    elapsed = DWT->CYCCNT - start;
    __DSB();
    __ISB();
    if (saved_primask == 0U) {
        __enable_irq();
    }
    return elapsed;
}

static uint8_t sample_status_with_fixed_diagnostics(uint8_t status)
{
#if FC_H755_FIXED_POINT
    const fc_fixed_diagnostics_t *diagnostics =
        fc_fixed_solver_diagnostics(&g_solver_storage.solver);

    if (diagnostics != NULL) {
        if (diagnostics->saturation_count != 0U) {
            g_fixed_status_flags |=
                FC_SAMPLE_STATUS_FIXED_STATE_SATURATION;
        }
        if (diagnostics->coefficient_saturation_count != 0U) {
            g_fixed_status_flags |=
                FC_SAMPLE_STATUS_FIXED_COEFFICIENT_SATURATION;
        }
        if (diagnostics->zeroed_nonzero_coefficient_count != 0U) {
            g_fixed_status_flags |=
                FC_SAMPLE_STATUS_FIXED_COEFFICIENT_ZEROED;
        }
    }
    status |= g_fixed_status_flags;
#endif
    return status;
}

#if FC_H755_BENCHMARK_MODE
static void run_timing_benchmark(
    fc_h755_solver_t *solver,
    fc_h755_state_t *state)
{
    uint32_t solver_sequence = 0U;
    uint32_t index;
    fc_h755_status_t solver_status = FC_H755_STATUS_OK;
    uint8_t output_status;

    for (index = 0U; index < FC_BENCHMARK_WARMUP_STEPS; ++index) {
        const uint32_t cycles =
            solver_step_cycles(solver, state, &solver_status);
        ++solver_sequence;
        if (solver_status != FC_H755_STATUS_OK) {
            publish_sample(
                state,
                solver_sequence,
                cycles,
                FC_SAMPLE_STATUS_NONFINITE);
            for (;;) {
                __WFE();
            }
        }
    }

    /*
     * No se publica nada en SRAM4 durante esta ventana. Los 10000 valores DWT
     * se conservan en RAM_D1 y se envían al CM4 únicamente al terminar.
     */
    for (index = 0U; index < FC_BENCHMARK_TIMED_STEPS; ++index) {
        const uint32_t cycles =
            solver_step_cycles(solver, state, &solver_status);
        ++solver_sequence;
        if (solver_status != FC_H755_STATUS_OK) {
            publish_sample(
                state,
                solver_sequence,
                cycles,
                FC_SAMPLE_STATUS_NONFINITE);
            for (;;) {
                __WFE();
            }
        }
        g_benchmark_cycles[index] = cycles;
    }

    output_status =
        sample_status_with_fixed_diagnostics(FC_SAMPLE_STATUS_OK);
    for (index = 0U;
         index < FC_BENCHMARK_TIMED_STEPS;
         index += FC_BENCHMARK_BLOCK_VALUES) {
        publish_timing_block(
            index,
            &g_benchmark_cycles[index],
            output_status);
    }
    for (;;) {
        __WFE();
    }
}

static void publish_timing_block(
    uint32_t first_index,
    const uint32_t cycles[FC_BENCHMARK_BLOCK_VALUES],
    uint8_t status)
{
    fc_sample_t sample = {0};

    sample.sequence = first_index;
    sample.cycles = cycles[0];
    sample.state_words[0] = cycles[1];
    sample.state_words[1] = cycles[2];
    sample.state_words[2] = cycles[3];
    sample.system_id = (uint8_t)FC_SYSTEM_ID;
    sample.method_id = (uint8_t)FC_METHOD_ID;
    sample.status = status;
    sample.representation = FC_REPRESENTATION_TIMING_BLOCK;

    while (!fc_shared_queue_has_space(&g_fc_h755_queue)) {
        if (g_fc_h755_queue.magic != FC_SHARED_QUEUE_MAGIC) {
            Error_Handler();
        }
        /*
         * This wait is outside the timed solver window.  Do not sleep on WFE:
         * CM4 DMA-completion events are sufficient for its local transmit
         * loop, but are not a reliable reverse doorbell from D2 to D1 on all
         * reset paths.  Polling the non-cacheable read index prevents the
         * bulk timing dump from stalling after the 64-slot ring fills.
         */
    }
    if (!fc_shared_queue_push(&g_fc_h755_queue, &sample)) {
        Error_Handler();
    }
    __SEV();
}
#endif

static void publish_sample(
    const fc_h755_state_t *state,
    uint32_t sequence,
    uint32_t cycles,
    uint8_t status)
{
    fc_sample_t sample;

    sample.sequence = sequence;
    sample.cycles = cycles;
#if FC_H755_FIXED_POINT
    sample.fixed_state[0] = state->v[0];
    sample.fixed_state[1] = state->v[1];
    sample.fixed_state[2] = state->v[2];
#else
    sample.state[0] = state->v[0];
    sample.state[1] = state->v[1];
    sample.state[2] = state->v[2];
#endif
    sample.system_id = (uint8_t)FC_SYSTEM_ID;
    sample.method_id = (uint8_t)FC_METHOD_ID;
    sample.status = sample_status_with_fixed_diagnostics(status);
    sample.representation =
#if FC_H755_FIXED_POINT
        FC_REPRESENTATION_FIXED_Q14;
#else
        FC_REPRESENTATION_FLOAT32;
#endif

    if (fc_shared_queue_push(&g_fc_h755_queue, &sample)) {
        __SEV();
    }
}

void Error_Handler(void)
{
    __disable_irq();
    for (;;) {
        __NOP();
    }
}
