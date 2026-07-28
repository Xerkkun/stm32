#include "main.h"

#include "fc_protocol.h"
#include "fractional_chaos.h"

#include <stddef.h>
#include <stdint.h>

#ifndef FC_F746_SYSTEM
#define FC_F746_SYSTEM FC_SYSTEM_LORENZ
#endif

#ifndef FC_F746_METHOD
#define FC_F746_METHOD FC_METHOD_EFORK3
#endif

#ifndef FC_F746_PRECOMPUTED_TABLES
#error "FC_F746_PRECOMPUTED_TABLES must select the linked frozen table"
#endif

#ifndef FC_F746_OUTPUT_DECIMATION
#define FC_F746_OUTPUT_DECIMATION 16U
#endif

#define FC_F746_CORE_CLOCK_HZ       216000000U
#define FC_F746_UART_BAUD           921600U
#define FC_F746_CACHE_LINE_BYTES    32U
#define FC_F746_DMA_BUFFER_BYTES    (2U * FC_F746_CACHE_LINE_BYTES)
#define FC_F746_DTCM_BYTES          (64U * 1024U)

_Static_assert(
    (FC_F746_SYSTEM >= FC_SYSTEM_LORENZ) &&
    (FC_F746_SYSTEM <= FC_SYSTEM_CHEN),
    "FC_F746_SYSTEM must select one of the three manifests");
_Static_assert(
    (FC_F746_METHOD == FC_METHOD_EFORK3) ||
    (FC_F746_METHOD == FC_METHOD_GL_CAPUTO),
    "FC_F746_METHOD must select EFORK3 or GL_CAPUTO");
_Static_assert(
    FC_F746_OUTPUT_DECIMATION > 0U,
    "FC_F746_OUTPUT_DECIMATION must be greater than zero");
_Static_assert(
    sizeof(fc_wire_frame_t) <= FC_F746_DMA_BUFFER_BYTES,
    "The DMA storage must contain one complete wire frame");

typedef struct {
    fc_solver_t solver;
    fc_workspace_t workspace;
} fc_f746_solver_storage_t;

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

UART_HandleTypeDef huart3;
DMA_HandleTypeDef hdma_usart3_tx;

static fc_f746_solver_storage_t g_solver_storage
    __attribute__((section(".solver"), aligned(32)));
static fc_f746_dma_buffer_t g_uart_tx_buffer
    __attribute__((section(".dma_buffer"), aligned(32)));

static volatile uint8_t g_uart_tx_active;
static volatile uint32_t g_uart_dropped;

static void SystemClock_Config(void);
static void MX_DMA_Init(void);
static void MX_USART3_UART_Init(void);
static void configure_floating_point(void);
static void cycle_counter_init(void);
static void clear_solver_storage(void);
static uint32_t solver_step_cycles(
    fc_solver_t *solver,
    fc_vec3f_t *state,
    fc_status_t *status);
static void transmit_sample(
    const fc_vec3f_t *state,
    uint32_t sequence,
    uint32_t cycles,
    uint8_t status);
static void halt_after_solver_error(
    const fc_vec3f_t *state,
    uint32_t sequence,
    uint32_t cycles);

int main(void)
{
    fc_config_t config;
    fc_vec3f_t state;
    uint32_t sequence = 0U;
    uint32_t decimation_counter = 0U;

    SCB_EnableICache();
    SCB_EnableDCache();

    HAL_Init();
    SystemClock_Config();
    configure_floating_point();
    cycle_counter_init();

    MX_DMA_Init();
    MX_USART3_UART_Init();

    /*
     * Después de configurar reloj y UART no se usa la base temporal HAL.
     * Se suprime su interrupción periódica y se conservan solo DMA/USART3.
     */
    HAL_SuspendTick();

    clear_solver_storage();
    if (fc_config_from_manifest(
            (fc_system_t)FC_F746_SYSTEM,
            (fc_method_t)FC_F746_METHOD,
            &config) != FC_OK) {
        Error_Handler();
    }
    config.precomputed_tables = &FC_F746_PRECOMPUTED_TABLES;
    if (fc_solver_init(
            &g_solver_storage.solver,
            &g_solver_storage.workspace,
            &config) != FC_OK) {
        Error_Handler();
    }

    for (;;) {
        fc_status_t status;
        const uint32_t cycles = solver_step_cycles(
            &g_solver_storage.solver,
            &state,
            &status);

        ++sequence;
        if (status != FC_OK) {
            const fc_vec3f_t *last_state =
                fc_solver_state(&g_solver_storage.solver);
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
}

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
    huart3.Init.Mode = UART_MODE_TX;
    huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart3.Init.OverSampling = UART_OVERSAMPLING_16;
    huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;

    if (HAL_UART_Init(&huart3) != HAL_OK) {
        Error_Handler();
    }
}

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
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    __DSB();
    __ISB();
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
    fc_solver_t *solver,
    fc_vec3f_t *state,
    fc_status_t *status)
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
    *status = fc_solver_step(solver, state);
    elapsed = DWT->CYCCNT - start;
    __DSB();
    __ISB();
    if (saved_primask == 0U) {
        __enable_irq();
    }

    return elapsed;
}

static void transmit_sample(
    const fc_vec3f_t *state,
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
    sample.state[0] = state->v[0];
    sample.state[1] = state->v[1];
    sample.state[2] = state->v[2];
    sample.system_id = (uint8_t)FC_F746_SYSTEM;
    sample.method_id = (uint8_t)FC_F746_METHOD;
    sample.status = status;
    sample.reserved = 0U;

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
    const fc_vec3f_t *state,
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
