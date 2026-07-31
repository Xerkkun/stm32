#include "main.h"

#include "fc_protocol.h"
#include "fc_runtime_probe.h"
#include "fc_start_handshake.h"
#include "fractional_chaos.h"
#include "fractional_chaos_fixed.h"

#include <stddef.h>
#include <stdint.h>

#ifndef FC_F746_SYSTEM
#define FC_F746_SYSTEM FC_SYSTEM_LORENZ
#endif

#ifndef FC_F746_METHOD
#define FC_F746_METHOD FC_METHOD_EFORK3
#endif

#ifndef FC_F746_FIXED_POINT
#define FC_F746_FIXED_POINT 0
#endif

#if !FC_F746_FIXED_POINT && !defined(FC_F746_PRECOMPUTED_TABLES)
#error "FC_F746_PRECOMPUTED_TABLES must select the linked frozen table"
#endif

#ifndef FC_F746_OUTPUT_DECIMATION
#define FC_F746_OUTPUT_DECIMATION 16U
#endif

#ifndef FC_F746_BENCHMARK_MODE
#define FC_F746_BENCHMARK_MODE 0
#endif

#ifndef FC_F746_ENERGY_MARKER
#define FC_F746_ENERGY_MARKER 0
#endif

#ifndef FC_F746_ENERGY_WORK_MULTIPLIER
#define FC_F746_ENERGY_WORK_MULTIPLIER 1U
#endif

#ifndef FC_F746_CLOCK_REFERENCE
#define FC_F746_CLOCK_REFERENCE 0
#endif

#ifndef FC_F746_RUNTIME_PROBE
#define FC_F746_RUNTIME_PROBE 0
#endif

#ifndef FC_F746_PRIMARY_HANDSHAKE
#define FC_F746_PRIMARY_HANDSHAKE 0
#endif

#ifndef FC_F746_BUFFERED_CAPTURE_MODE
#define FC_F746_BUFFERED_CAPTURE_MODE 0
#endif

#ifndef FC_F746_BUFFERED_CAPTURE_SAMPLES
#define FC_F746_BUFFERED_CAPTURE_SAMPLES 12000U
#endif

#define FC_F746_CORE_CLOCK_HZ       216000000U
#define FC_F746_UART_BAUD           921600U
#define FC_F746_CACHE_LINE_BYTES    32U
#define FC_F746_DMA_BUFFER_BYTES    (2U * FC_F746_CACHE_LINE_BYTES)
#define FC_F746_DTCM_BYTES          (64U * 1024U)
#define FC_BENCHMARK_TIMED_STEPS    10000U
#define FC_BENCHMARK_ENERGY_STEPS   \
    (FC_BENCHMARK_TIMED_STEPS * FC_F746_ENERGY_WORK_MULTIPLIER)
#define FC_BENCHMARK_BLOCK_VALUES   4U
#define FC_ENERGY_MARKER_GPIO       GPIOE
#define FC_ENERGY_MARKER_PIN        GPIO_PIN_0
#define FC_CLOCK_REFERENCE_GPIO     GPIOA
#define FC_CLOCK_REFERENCE_PIN      GPIO_PIN_0
#define FC_CLOCK_REFERENCE_CYCLES   (FC_F746_CORE_CLOCK_HZ / 10U)
#define FC_INA226_PRE_ENERGY_IDLE_CYCLES \
    ((FC_F746_CORE_CLOCK_HZ / 10U) * 3U)

#if FC_F746_SYSTEM == 1
#define FC_BENCHMARK_WARMUP_STEPS   5000U
#elif (FC_F746_SYSTEM == 3) || (FC_F746_SYSTEM == 4)
#define FC_BENCHMARK_WARMUP_STEPS   1000U
#else
#define FC_BENCHMARK_WARMUP_STEPS   2000U
#endif

_Static_assert(
    (FC_F746_SYSTEM >= FC_SYSTEM_LORENZ) &&
    (FC_F746_SYSTEM <= FC_SYSTEM_HAMMOUCH_MEKKAOUI),
    "FC_F746_SYSTEM must select a float manifest or a supported fixed manifest");
_Static_assert(
    (FC_F746_METHOD == FC_METHOD_EFORK3) ||
    (FC_F746_METHOD == FC_METHOD_GL_CAPUTO) ||
    (FC_F746_METHOD == FC_METHOD_M2SFRK),
    "FC_F746_METHOD must select EFORK3, GL_CAPUTO, or M2SFRK");
_Static_assert(
    FC_F746_OUTPUT_DECIMATION > 0U,
    "FC_F746_OUTPUT_DECIMATION must be greater than zero");
_Static_assert(
    (FC_F746_BENCHMARK_MODE == 0) ||
    (FC_F746_BENCHMARK_MODE == 1),
    "FC_F746_BENCHMARK_MODE must be zero or one");
_Static_assert(
    (FC_F746_ENERGY_MARKER == 0) ||
    (FC_F746_ENERGY_MARKER == 1),
    "FC_F746_ENERGY_MARKER must be zero or one");
_Static_assert(
    !FC_F746_ENERGY_MARKER || FC_F746_BENCHMARK_MODE,
    "The energy marker is only valid in benchmark mode");
_Static_assert(
    (FC_F746_ENERGY_WORK_MULTIPLIER >= 1U) &&
    (FC_F746_ENERGY_WORK_MULTIPLIER <= 512U),
    "FC_F746_ENERGY_WORK_MULTIPLIER must be in [1, 512]");
_Static_assert(
    FC_F746_ENERGY_MARKER ||
    (FC_F746_ENERGY_WORK_MULTIPLIER == 1U),
    "An expanded energy workload requires the energy marker");
_Static_assert(
    FC_F746_PRIMARY_HANDSHAKE ||
    (FC_F746_ENERGY_WORK_MULTIPLIER == 1U),
    "An expanded energy workload requires the primary handshake");
_Static_assert(
    (FC_F746_CLOCK_REFERENCE == 0) ||
    (FC_F746_CLOCK_REFERENCE == 1),
    "FC_F746_CLOCK_REFERENCE must be zero or one");
_Static_assert(
    !FC_F746_CLOCK_REFERENCE || FC_F746_PRIMARY_HANDSHAKE,
    "The clock reference requires the primary handshake");
_Static_assert(
    (FC_F746_RUNTIME_PROBE == 0) ||
    (FC_F746_RUNTIME_PROBE == 1),
    "FC_F746_RUNTIME_PROBE must be zero or one");
_Static_assert(
    !FC_F746_RUNTIME_PROBE || FC_F746_PRIMARY_HANDSHAKE,
    "The runtime probe requires the primary handshake");
_Static_assert(
    (FC_F746_PRIMARY_HANDSHAKE == 0) ||
    (FC_F746_PRIMARY_HANDSHAKE == 1),
    "FC_F746_PRIMARY_HANDSHAKE must be zero or one");
_Static_assert(
    !FC_F746_PRIMARY_HANDSHAKE || FC_F746_BENCHMARK_MODE,
    "The primary handshake is only valid in benchmark mode");
_Static_assert(
    (FC_F746_BUFFERED_CAPTURE_MODE == 0) ||
    (FC_F746_BUFFERED_CAPTURE_MODE == 1),
    "FC_F746_BUFFERED_CAPTURE_MODE must be zero or one");
_Static_assert(
    FC_F746_BUFFERED_CAPTURE_SAMPLES > 0U,
    "FC_F746_BUFFERED_CAPTURE_SAMPLES must be greater than zero");
_Static_assert(
    !FC_F746_BUFFERED_CAPTURE_MODE || (FC_F746_OUTPUT_DECIMATION == 1U),
    "Buffered capture represents every solver step and requires decimation 1");
_Static_assert(
    !(FC_F746_BUFFERED_CAPTURE_MODE && FC_F746_BENCHMARK_MODE),
    "Buffered capture and benchmark modes are mutually exclusive");
_Static_assert(
    (FC_BENCHMARK_TIMED_STEPS % FC_BENCHMARK_BLOCK_VALUES) == 0U,
    "The benchmark must contain complete four-value timing blocks");
_Static_assert(
    sizeof(fc_wire_frame_t) <= FC_F746_DMA_BUFFER_BYTES,
    "The DMA storage must contain one complete wire frame");

typedef struct {
#if FC_F746_FIXED_POINT
    fc_fixed_solver_t solver;
#if FC_F746_METHOD != 2
    fc_fixed_workspace_t workspace;
#endif
#else
    fc_solver_t solver;
#if FC_F746_METHOD != 2
    fc_workspace_t workspace;
#endif
#endif
} fc_f746_solver_storage_t;

#if FC_F746_FIXED_POINT
typedef fc_fixed_solver_t fc_f746_solver_t;
typedef fc_fixed_vec3_t fc_f746_state_t;
typedef fc_fixed_status_t fc_f746_status_t;
#define FC_F746_STATUS_OK FC_FIXED_OK
#else
typedef fc_solver_t fc_f746_solver_t;
typedef fc_vec3f_t fc_f746_state_t;
typedef fc_status_t fc_f746_status_t;
#define FC_F746_STATUS_OK FC_OK
#endif

typedef struct {
    fc_f746_state_t state;
    uint32_t cycles;
} fc_f746_capture_record_t;

typedef union {
    fc_wire_frame_t frame;
    uint8_t cache_lines[FC_F746_DMA_BUFFER_BYTES];
} fc_f746_dma_buffer_t;

_Static_assert(
    sizeof(fc_f746_solver_storage_t) <= FC_F746_DTCM_BYTES,
    "Solver state and maximum history must fit in DTCM");
_Static_assert(
    (sizeof(fc_f746_solver_storage_t) % sizeof(uint32_t)) == 0U,
    "Solver storage must be word-clearable");
_Static_assert(
    sizeof(fc_f746_dma_buffer_t) == FC_F746_DMA_BUFFER_BYTES,
    "The TX buffer must span two complete M7 cache lines");
_Static_assert(
    sizeof(fc_f746_capture_record_t) == 16U,
    "Buffered capture records must retain the compact 16-byte layout");

UART_HandleTypeDef huart3;
DMA_HandleTypeDef hdma_usart3_tx;

static fc_f746_solver_storage_t g_solver_storage
    __attribute__((section(".solver"), aligned(32)));
static fc_f746_dma_buffer_t g_uart_tx_buffer
    __attribute__((section(".dma_buffer"), aligned(32)));
#if FC_F746_BENCHMARK_MODE
static uint32_t g_benchmark_cycles[FC_BENCHMARK_TIMED_STEPS]
    __attribute__((aligned(32)));
#endif
#if FC_F746_BUFFERED_CAPTURE_MODE
static fc_f746_capture_record_t
    g_buffered_capture[FC_F746_BUFFERED_CAPTURE_SAMPLES]
    __attribute__((section(".capture"), aligned(32), used));
#endif
#if FC_F746_RUNTIME_PROBE
extern uint8_t __stack_probe_start__;
extern uint8_t __stack_probe_end__;
extern uint8_t __solver_start__;
extern uint8_t __solver_end__;
extern uint8_t __capture_start__;
extern uint8_t __capture_end__;
extern uint8_t __dma_buffer_start__;
extern uint8_t __dma_buffer_end__;

fc_runtime_probe_record_t g_fc_f746_runtime_probe
    __attribute__((aligned(32), used));
#endif

static volatile uint8_t g_uart_tx_active;
static volatile uint32_t g_uart_dropped;
#if FC_F746_FIXED_POINT
static uint8_t g_fixed_status_flags;
#endif

static void SystemClock_Config(void);
static void MX_DMA_Init(void);
static void MX_USART3_UART_Init(void);
#if FC_F746_PRIMARY_HANDSHAKE
static void wait_for_primary_start(void);
#endif
#if FC_F746_ENERGY_MARKER
static void MX_Energy_Marker_GPIO_Init(void);
#endif
#if FC_F746_CLOCK_REFERENCE
static void MX_Clock_Reference_GPIO_Init(void);
static void emit_clock_reference_pulse(void);
#endif
#if FC_F746_RUNTIME_PROBE
static void runtime_probe_begin(void);
static void runtime_probe_finish(void);
#endif
static void configure_floating_point(void);
static void cycle_counter_init(void);
static void clear_solver_storage(void);
static uint32_t solver_step_cycles(
    fc_f746_solver_t *solver,
    fc_f746_state_t *state,
    fc_f746_status_t *status);
static uint8_t sample_status_with_fixed_diagnostics(uint8_t status);
static void transmit_sample(
    const fc_f746_state_t *state,
    uint32_t sequence,
    uint32_t cycles,
    uint8_t status);
#if FC_F746_BUFFERED_CAPTURE_MODE
static void run_buffered_capture(
    fc_f746_solver_t *solver,
    fc_f746_state_t *state);
#endif
#if FC_F746_BENCHMARK_MODE
static void run_timing_benchmark(
    fc_f746_solver_t *solver,
    fc_f746_state_t *state);
static void transmit_timing_block(
    uint32_t first_index,
    const uint32_t cycles[FC_BENCHMARK_BLOCK_VALUES],
    uint8_t status);
#endif
static void halt_after_solver_error(
    const fc_f746_state_t *state,
    uint32_t sequence,
    uint32_t cycles);

int main(void)
{
#if FC_F746_FIXED_POINT
    fc_fixed_config_t config;
#else
    fc_config_t config;
#endif
    fc_f746_state_t state;
#if !FC_F746_BENCHMARK_MODE && !FC_F746_BUFFERED_CAPTURE_MODE
    uint32_t sequence = 0U;
    uint32_t decimation_counter = 0U;
#endif

    SCB_EnableICache();
    SCB_EnableDCache();

    HAL_Init();
    SystemClock_Config();
    configure_floating_point();
    cycle_counter_init();

#if FC_F746_RUNTIME_PROBE
    runtime_probe_begin();
#endif
#if FC_F746_ENERGY_MARKER
    MX_Energy_Marker_GPIO_Init();
#endif
#if FC_F746_CLOCK_REFERENCE
    MX_Clock_Reference_GPIO_Init();
#endif
    MX_DMA_Init();
    MX_USART3_UART_Init();
#if FC_F746_PRIMARY_HANDSHAKE
    wait_for_primary_start();
#endif

    /*
     * Después de configurar reloj y UART no se usa la base temporal HAL.
     * Se suprime su interrupción periódica y se conservan solo DMA/USART3.
     */
    HAL_SuspendTick();

#if FC_F746_CLOCK_REFERENCE
    emit_clock_reference_pulse();
#endif

    clear_solver_storage();
#if FC_F746_FIXED_POINT
    if (fc_fixed_config_from_manifest(
            (fc_fixed_system_t)FC_F746_SYSTEM,
            (fc_fixed_method_t)FC_F746_METHOD,
            &config) != FC_FIXED_OK) {
        Error_Handler();
    }
    if (fc_fixed_solver_init(
            &g_solver_storage.solver,
#if FC_F746_METHOD == 2
            NULL,
#else
            &g_solver_storage.workspace,
#endif
            &config) != FC_FIXED_OK) {
        Error_Handler();
    }
#else
    if (fc_config_from_manifest(
            (fc_system_t)FC_F746_SYSTEM,
            (fc_method_t)FC_F746_METHOD,
            &config) != FC_OK) {
        Error_Handler();
    }
    config.precomputed_tables = &FC_F746_PRECOMPUTED_TABLES;
    if (fc_solver_init(
            &g_solver_storage.solver,
#if FC_F746_METHOD == 2
            NULL,
#else
            &g_solver_storage.workspace,
#endif
            &config) != FC_OK) {
        Error_Handler();
    }
#endif

#if FC_F746_BENCHMARK_MODE
    run_timing_benchmark(&g_solver_storage.solver, &state);
#elif FC_F746_BUFFERED_CAPTURE_MODE
    run_buffered_capture(&g_solver_storage.solver, &state);
#else
    for (;;) {
        fc_f746_status_t status;
        const uint32_t cycles = solver_step_cycles(
            &g_solver_storage.solver,
            &state,
            &status);

        ++sequence;
        if (status != FC_F746_STATUS_OK) {
#if FC_F746_FIXED_POINT
            const fc_fixed_vec3_t *last_state =
                fc_fixed_solver_state(&g_solver_storage.solver);
#else
            const fc_vec3f_t *last_state =
                fc_solver_state(&g_solver_storage.solver);
#endif
            halt_after_solver_error(
                (last_state != NULL) ? last_state : &state,
                sequence,
                cycles);
        }

        ++decimation_counter;
        if (decimation_counter == FC_F746_OUTPUT_DECIMATION) {
            decimation_counter = 0U;
            transmit_sample(
                &state,
                sequence,
                cycles,
                FC_SAMPLE_STATUS_OK);
        }
    }
#endif
}

#if FC_F746_RUNTIME_PROBE
static uint32_t runtime_section_bytes(
    const uint8_t *start,
    const uint8_t *end)
{
    return (uint32_t)(
        (uintptr_t)(const void *)end -
        (uintptr_t)(const void *)start);
}

static void runtime_probe_begin(void)
{
    const fc_runtime_probe_config_t config = {
        FC_RUNTIME_CORE_F746_M7,
        FC_F746_CORE_CLOCK_HZ,
        SystemCoreClock,
        runtime_section_bytes(&__solver_start__, &__solver_end__),
        runtime_section_bytes(&__capture_start__, &__capture_end__),
        runtime_section_bytes(
            &__dma_buffer_start__,
            &__dma_buffer_end__),
        0U,
        0U,
    };

    if (!fc_runtime_probe_begin(
            &g_fc_f746_runtime_probe,
            &__stack_probe_start__,
            &__stack_probe_end__,
            (uintptr_t)__get_MSP(),
            256U,
            &config)) {
        Error_Handler();
    }
}

static void runtime_probe_finish(void)
{
    const int32_t stack_bytes = (int32_t)runtime_section_bytes(
        &__stack_probe_start__,
        &__stack_probe_end__);
    const int32_t record_bytes = (int32_t)(
        (sizeof(g_fc_f746_runtime_probe) + 31U) & ~31U);

    if (!fc_runtime_probe_finish(
            &g_fc_f746_runtime_probe,
            &__stack_probe_start__,
            &__stack_probe_end__)) {
        Error_Handler();
    }
    SCB_CleanDCache_by_Addr(
        (uint32_t *)(void *)&__stack_probe_start__,
        stack_bytes);
    SCB_CleanDCache_by_Addr(
        (uint32_t *)(void *)&g_fc_f746_runtime_probe,
        record_bytes);
    __DSB();
}
#endif

static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef oscillator = {0};
    RCC_ClkInitTypeDef clocks = {0};

    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

    oscillator.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    oscillator.HSEState = RCC_HSE_BYPASS;
    oscillator.PLL.PLLState = RCC_PLL_ON;
    oscillator.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    oscillator.PLL.PLLM = 8U;
    oscillator.PLL.PLLN = 432U;
    oscillator.PLL.PLLP = RCC_PLLP_DIV2;
    oscillator.PLL.PLLQ = 9U;
    if (HAL_RCC_OscConfig(&oscillator) != HAL_OK) {
        Error_Handler();
    }

    if (HAL_PWREx_EnableOverDrive() != HAL_OK) {
        Error_Handler();
    }

    clocks.ClockType =
        RCC_CLOCKTYPE_SYSCLK |
        RCC_CLOCKTYPE_HCLK |
        RCC_CLOCKTYPE_PCLK1 |
        RCC_CLOCKTYPE_PCLK2;
    clocks.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clocks.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clocks.APB1CLKDivider = RCC_HCLK_DIV4;
    clocks.APB2CLKDivider = RCC_HCLK_DIV2;
    if (HAL_RCC_ClockConfig(&clocks, FLASH_LATENCY_7) != HAL_OK) {
        Error_Handler();
    }

    SystemCoreClockUpdate();
    if (SystemCoreClock != FC_F746_CORE_CLOCK_HZ) {
        Error_Handler();
    }
}

static void MX_DMA_Init(void)
{
    __HAL_RCC_DMA1_CLK_ENABLE();

    HAL_NVIC_SetPriority(DMA1_Stream3_IRQn, 5U, 0U);
    HAL_NVIC_EnableIRQ(DMA1_Stream3_IRQn);
}

static void MX_USART3_UART_Init(void)
{
    huart3.Instance = USART3;
    huart3.Init.BaudRate = FC_F746_UART_BAUD;
    huart3.Init.WordLength = UART_WORDLENGTH_8B;
    huart3.Init.StopBits = UART_STOPBITS_1;
    huart3.Init.Parity = UART_PARITY_NONE;
#if FC_F746_PRIMARY_HANDSHAKE
    huart3.Init.Mode = UART_MODE_TX_RX;
#else
    huart3.Init.Mode = UART_MODE_TX;
#endif
    huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart3.Init.OverSampling = UART_OVERSAMPLING_16;
    huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;

    if (HAL_UART_Init(&huart3) != HAL_OK) {
        Error_Handler();
    }
}

#if FC_F746_PRIMARY_HANDSHAKE
static void wait_for_primary_start(void)
{
    static const uint8_t error_line[] = "ERR START\n";
    char line[FC_START_LINE_MAX];
    char ready[FC_READY_LINE_MAX];
    size_t length = 0U;
    uint8_t overflow = 0U;

    for (;;) {
        uint8_t byte;
        fc_start_command_t command;
        size_t ready_length;

        if (HAL_UART_Receive(
                &huart3,
                &byte,
                1U,
                HAL_MAX_DELAY) != HAL_OK) {
            Error_Handler();
        }
        if (byte != (uint8_t)'\n') {
            if (length < sizeof(line)) {
                line[length] = (char)byte;
                ++length;
            } else {
                overflow = 1U;
            }
            continue;
        }
        if ((overflow == 0U) &&
            fc_parse_start_command(line, length, &command) &&
            fc_start_command_matches(
                &command,
                FC_FRAME_TIMING_BLOCK,
                FC_BOARD_F746,
                (uint8_t)FC_F746_SYSTEM,
                (uint8_t)FC_F746_METHOD)) {
            ready_length = fc_format_ready_line(
                ready,
                sizeof(ready),
                command.request_id);
            if ((ready_length == 0U) ||
                (HAL_UART_Transmit(
                    &huart3,
                    (const uint8_t *)(const void *)ready,
                    (uint16_t)ready_length,
                    1000U) != HAL_OK)) {
                Error_Handler();
            }
            return;
        }
        if (HAL_UART_Transmit(
                &huart3,
                error_line,
                (uint16_t)(sizeof(error_line) - 1U),
                1000U) != HAL_OK) {
            Error_Handler();
        }
        length = 0U;
        overflow = 0U;
    }
}
#endif

#if FC_F746_ENERGY_MARKER
static void MX_Energy_Marker_GPIO_Init(void)
{
    __HAL_RCC_GPIOE_CLK_ENABLE();
    (void)RCC->AHB1ENR;
    FC_ENERGY_MARKER_GPIO->BSRR =
        (uint32_t)FC_ENERGY_MARKER_PIN << 16U;
    FC_ENERGY_MARKER_GPIO->OTYPER &= ~UINT32_C(1);
    FC_ENERGY_MARKER_GPIO->OSPEEDR &= ~UINT32_C(3);
    FC_ENERGY_MARKER_GPIO->PUPDR &= ~UINT32_C(3);
    FC_ENERGY_MARKER_GPIO->MODER =
        (FC_ENERGY_MARKER_GPIO->MODER & ~UINT32_C(3)) |
        UINT32_C(1);
    __DSB();
}
#endif

#if FC_F746_CLOCK_REFERENCE
static void MX_Clock_Reference_GPIO_Init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    (void)RCC->AHB1ENR;
    FC_CLOCK_REFERENCE_GPIO->BSRR =
        (uint32_t)FC_CLOCK_REFERENCE_PIN << 16U;
    FC_CLOCK_REFERENCE_GPIO->OTYPER &= ~UINT32_C(1);
    FC_CLOCK_REFERENCE_GPIO->OSPEEDR &= ~UINT32_C(3);
    FC_CLOCK_REFERENCE_GPIO->PUPDR &= ~UINT32_C(3);
    FC_CLOCK_REFERENCE_GPIO->MODER =
        (FC_CLOCK_REFERENCE_GPIO->MODER & ~UINT32_C(3)) |
        UINT32_C(1);
    __DSB();
}

static void emit_clock_reference_pulse(void)
{
    uint32_t start;

    FC_CLOCK_REFERENCE_GPIO->BSRR = FC_CLOCK_REFERENCE_PIN;
    __DSB();
    start = DWT->CYCCNT;
    while ((uint32_t)(DWT->CYCCNT - start) <
           FC_CLOCK_REFERENCE_CYCLES) {
        __NOP();
    }
    FC_CLOCK_REFERENCE_GPIO->BSRR =
        (uint32_t)FC_CLOCK_REFERENCE_PIN << 16U;
    __DSB();
#if FC_F746_ENERGY_MARKER
    /*
     * INA14/1 requires at least 100 idle samples at 500 Hz before PE0 rises.
     * Keep PA0 low for 300 ms so even the fastest solver profile satisfies
     * that baseline independently of its warm-up duration.
     */
    start = DWT->CYCCNT;
    while ((uint32_t)(DWT->CYCCNT - start) <
           FC_INA226_PRE_ENERGY_IDLE_CYCLES) {
        __NOP();
    }
#endif
}
#endif

static void configure_floating_point(void)
{
#if (__FPU_PRESENT == 1U) && (__FPU_USED == 1U)
    /*
     * Round to nearest, preserve subnormals and keep IEEE default NaN
     * propagation. Explicit fmaf() calls in the common kernel still map to
     * the single-rounding M7 FMA instruction.
     */
    const uint32_t non_ieee_mask =
        (3UL << 22U) | /* RMode */
        (1UL << 24U) | /* flush-to-zero */
        (1UL << 25U);  /* default-NaN */
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

static uint32_t solver_step_cycles(
    fc_f746_solver_t *solver,
    fc_f746_state_t *state,
    fc_f746_status_t *status)
{
    const uint32_t saved_primask = __get_PRIMASK();
    uint32_t start;
    uint32_t elapsed;

    /*
     * Communication and SysTick IRQ latency are excluded from the reported
     * kernel time. DMA hardware continues transmitting while IRQ delivery is
     * deferred for this single solver call.
     */
    __disable_irq();
    __DSB();
    __ISB();
    start = DWT->CYCCNT;
#if FC_F746_FIXED_POINT
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
#if FC_F746_FIXED_POINT
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

#if FC_F746_BUFFERED_CAPTURE_MODE
static void run_buffered_capture(
    fc_f746_solver_t *solver,
    fc_f746_state_t *state)
{
    uint32_t index;
    fc_f746_status_t solver_status = FC_F746_STATUS_OK;

    /*
     * UART remains idle while every consecutive solver state is copied to
     * SRAM1. Sequence is therefore model-step sequence, never reception time.
     */
    for (index = 0U;
         index < FC_F746_BUFFERED_CAPTURE_SAMPLES;
         ++index) {
        const uint32_t cycles =
            solver_step_cycles(solver, state, &solver_status);
        const uint32_t sequence = index + 1U;

        if (solver_status != FC_F746_STATUS_OK) {
            halt_after_solver_error(state, sequence, cycles);
        }
        g_buffered_capture[index].state = *state;
        g_buffered_capture[index].cycles = cycles;
    }

    /*
     * Drain only after the numerical window has closed. Waiting before each
     * DMA launch makes loss impossible without perturbing the saved states.
     */
    for (index = 0U;
         index < FC_F746_BUFFERED_CAPTURE_SAMPLES;
         ++index) {
        while (g_uart_tx_active != 0U) {
            __WFI();
        }
        transmit_sample(
            &g_buffered_capture[index].state,
            index + 1U,
            g_buffered_capture[index].cycles,
            FC_SAMPLE_STATUS_OK);
    }
    while (g_uart_tx_active != 0U) {
        __WFI();
    }
    for (;;) {
        __WFI();
    }
}
#endif

#if FC_F746_BENCHMARK_MODE
static void run_timing_benchmark(
    fc_f746_solver_t *solver,
    fc_f746_state_t *state)
{
    uint32_t solver_sequence = 0U;
    uint32_t index;
    fc_f746_status_t solver_status = FC_F746_STATUS_OK;
    uint8_t output_status;

    for (index = 0U; index < FC_BENCHMARK_WARMUP_STEPS; ++index) {
        const uint32_t cycles =
            solver_step_cycles(solver, state, &solver_status);
        ++solver_sequence;
        if (solver_status != FC_F746_STATUS_OK) {
            halt_after_solver_error(
                state,
                solver_sequence,
                cycles);
        }
    }

    /*
     * UART remains idle throughout this loop. Each DWT result is preserved
     * verbatim in SRAM1 and emitted only after all 10000 timed calls finish.
     * PE0/D34 brackets the retained 10000 calls plus any predeclared
     * supplemental calls needed to give the external INA226 enough samples.
     * Every call uses the same DWT-instrumented solver wrapper, but only the
     * first 10000 cycle counts are retained. UART remains outside the window.
     */
#if FC_F746_ENERGY_MARKER
    FC_ENERGY_MARKER_GPIO->BSRR = FC_ENERGY_MARKER_PIN;
#endif
    for (index = 0U; index < FC_BENCHMARK_TIMED_STEPS; ++index) {
        const uint32_t cycles =
            solver_step_cycles(solver, state, &solver_status);
        ++solver_sequence;
        if (solver_status != FC_F746_STATUS_OK) {
            halt_after_solver_error(
                state,
                solver_sequence,
                cycles);
        }
        g_benchmark_cycles[index] = cycles;
    }
#if FC_F746_ENERGY_MARKER
    for (index = FC_BENCHMARK_TIMED_STEPS;
         index < FC_BENCHMARK_ENERGY_STEPS;
         ++index) {
        (void)solver_step_cycles(solver, state, &solver_status);
        ++solver_sequence;
        if (solver_status != FC_F746_STATUS_OK) {
            halt_after_solver_error(
                state,
                solver_sequence,
                0U);
        }
    }
    FC_ENERGY_MARKER_GPIO->BSRR =
        (uint32_t)FC_ENERGY_MARKER_PIN << 16U;
#endif

    output_status =
        sample_status_with_fixed_diagnostics(FC_SAMPLE_STATUS_OK);
    for (index = 0U;
         index < FC_BENCHMARK_TIMED_STEPS;
         index += FC_BENCHMARK_BLOCK_VALUES) {
        transmit_timing_block(
            index,
            &g_benchmark_cycles[index],
            output_status);
    }
    while (g_uart_tx_active != 0U) {
        __WFI();
    }
#if FC_F746_RUNTIME_PROBE
    runtime_probe_finish();
#endif
    for (;;) {
        __WFI();
    }
}

static void transmit_timing_block(
    uint32_t first_index,
    const uint32_t cycles[FC_BENCHMARK_BLOCK_VALUES],
    uint8_t status)
{
    fc_sample_t sample = {0};

    while (g_uart_tx_active != 0U) {
        __WFI();
    }

    sample.sequence = first_index;
    sample.cycles = cycles[0];
    sample.state_words[0] = cycles[1];
    sample.state_words[1] = cycles[2];
    sample.state_words[2] = cycles[3];
    sample.system_id = (uint8_t)FC_F746_SYSTEM;
    sample.method_id = (uint8_t)FC_F746_METHOD;
    sample.status = status;
    sample.representation = FC_REPRESENTATION_TIMING_BLOCK;

    fc_make_state_frame(
        &g_uart_tx_buffer.frame,
        &sample,
        FC_BOARD_F746,
        g_uart_dropped);
    SCB_CleanDCache_by_Addr(
        (uint32_t *)(void *)&g_uart_tx_buffer,
        (int32_t)sizeof(g_uart_tx_buffer));
    __DSB();

    g_uart_tx_active = 1U;
    if (HAL_UART_Transmit_DMA(
            &huart3,
            (uint8_t *)(void *)&g_uart_tx_buffer.frame,
            (uint16_t)sizeof(g_uart_tx_buffer.frame)) != HAL_OK) {
        g_uart_tx_active = 0U;
        ++g_uart_dropped;
        Error_Handler();
    }
}
#endif

static void transmit_sample(
    const fc_f746_state_t *state,
    uint32_t sequence,
    uint32_t cycles,
    uint8_t status)
{
    fc_sample_t sample;

    if (g_uart_tx_active != 0U) {
        ++g_uart_dropped;
        return;
    }

    sample.sequence = sequence;
    sample.cycles = cycles;
#if FC_F746_FIXED_POINT
    sample.fixed_state[0] = state->v[0];
    sample.fixed_state[1] = state->v[1];
    sample.fixed_state[2] = state->v[2];
#else
    sample.state[0] = state->v[0];
    sample.state[1] = state->v[1];
    sample.state[2] = state->v[2];
#endif
    sample.system_id = (uint8_t)FC_F746_SYSTEM;
    sample.method_id = (uint8_t)FC_F746_METHOD;
    sample.status = sample_status_with_fixed_diagnostics(status);
    sample.representation =
#if FC_F746_FIXED_POINT
        FC_REPRESENTATION_FIXED_Q14;
#else
        FC_REPRESENTATION_FLOAT32;
#endif

    fc_make_state_frame(
        &g_uart_tx_buffer.frame,
        &sample,
        FC_BOARD_F746,
        g_uart_dropped);

    /*
     * The object begins on a 32-byte boundary and spans exactly two complete
     * cache lines, so cleaning cannot evict unrelated application data.
     */
    SCB_CleanDCache_by_Addr(
        (uint32_t *)(void *)&g_uart_tx_buffer,
        (int32_t)sizeof(g_uart_tx_buffer));
    __DSB();

    g_uart_tx_active = 1U;
    if (HAL_UART_Transmit_DMA(
            &huart3,
            (uint8_t *)(void *)&g_uart_tx_buffer.frame,
            (uint16_t)sizeof(g_uart_tx_buffer.frame)) != HAL_OK) {
        g_uart_tx_active = 0U;
        ++g_uart_dropped;
    }
}

static void halt_after_solver_error(
    const fc_f746_state_t *state,
    uint32_t sequence,
    uint32_t cycles)
{
    while (g_uart_tx_active != 0U) {
        __WFI();
    }

    transmit_sample(
        state,
        sequence,
        cycles,
        FC_SAMPLE_STATUS_NONFINITE);

    for (;;) {
        __WFI();
    }
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *uart)
{
    if (uart->Instance == USART3) {
        g_uart_tx_active = 0U;
    }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart)
{
    if (uart->Instance == USART3) {
        g_uart_tx_active = 0U;
        ++g_uart_dropped;
    }
}

void Error_Handler(void)
{
    __disable_irq();
    for (;;) {
        __NOP();
    }
}
