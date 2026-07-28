#include "main.h"

#include "fc_protocol.h"
#include "h755_shared_memory.h"

#include <stdint.h>

#define FC_H755_HSEM_BOOT_ID       0U
#if FC_H755_SMOKE_DIAGNOSTICS
#define FC_H755_UART_BAUD          115200U
#else
#define FC_H755_UART_BAUD          921600U
#endif
#define FC_H755_DMA_BUFFER_BYTES   64U
#define FC_H755_CM7_SEV_PRIORITY   6U

#ifndef FC_H755_SMOKE_DIAGNOSTICS
#define FC_H755_SMOKE_DIAGNOSTICS 0
#endif

#if FC_H755_SMOKE_DIAGNOSTICS
#define FC_CM4_DIAG_WRITE(field, value) \
    (g_fc_h755_cm4_diagnostics.field = (uint32_t)(value))
#define FC_CM4_DIAG_INCREMENT(field) \
    (++g_fc_h755_cm4_diagnostics.field)
#else
#define FC_CM4_DIAG_WRITE(field, value) ((void)0)
#define FC_CM4_DIAG_INCREMENT(field) ((void)0)
#endif

typedef union {
    fc_wire_frame_t frame;
    uint8_t bytes[FC_H755_DMA_BUFFER_BYTES];
} fc_h755_dma_buffer_t;

_Static_assert(
    sizeof(fc_wire_frame_t) <= FC_H755_DMA_BUFFER_BYTES,
    "La trama debe caber en el búfer DMA");
_Static_assert(
    sizeof(fc_h755_dma_buffer_t) == FC_H755_DMA_BUFFER_BYTES,
    "El búfer DMA debe ocupar 64 bytes");
_Static_assert(
    CM7_SEV_IRQn == 64,
    "El vector intercore CM7_SEV debe conservar el IRQ 64 del STM32H755");
_Static_assert(
    FC_H755_CM7_SEV_PRIORITY > 5U,
    "CM7_SEV debe tener menor prioridad que DMA1 y USART3");

UART_HandleTypeDef huart3;
DMA_HandleTypeDef hdma_usart3_tx;

static fc_h755_dma_buffer_t g_uart_tx_buffer
    __attribute__((section(".dma_buffer"), aligned(32), used));
static volatile uint8_t g_uart_tx_active;
static volatile uint32_t g_uart_errors;

static void wait_for_cm7_release(void);
static void MX_DMA_Init(void);
static void MX_USART3_UART_Init(void);
static void MX_CM7_SEV_Init(void);
static void transmit_next_sample(void);

int main(void)
{
#if FC_H755_SMOKE_DIAGNOSTICS
    g_fc_h755_cm4_diagnostics.magic = FC_H755_CM4_DIAG_MAGIC;
    g_fc_h755_cm4_diagnostics.stage = 1U;
    g_fc_h755_cm4_diagnostics.loop_count = 0U;
    g_fc_h755_cm4_diagnostics.samples_popped = 0U;
    g_fc_h755_cm4_diagnostics.dma_started = 0U;
    g_fc_h755_cm4_diagnostics.tx_completed = 0U;
    g_fc_h755_cm4_diagnostics.uart_errors = 0U;
    g_fc_h755_cm4_diagnostics.last_hal_status = 0U;
    g_fc_h755_cm4_diagnostics.hardfault_count = 0U;
    g_fc_h755_cm4_diagnostics.cfsr = 0U;
    g_fc_h755_cm4_diagnostics.hfsr = 0U;
    g_fc_h755_cm4_diagnostics.mmfar = 0U;
    g_fc_h755_cm4_diagnostics.bfar = 0U;
#endif
    HAL_Init();
    FC_CM4_DIAG_WRITE(stage, 2U);
    wait_for_cm7_release();
    FC_CM4_DIAG_WRITE(stage, 3U);
    SystemCoreClockUpdate();

    MX_DMA_Init();
    FC_CM4_DIAG_WRITE(stage, 4U);
    MX_USART3_UART_Init();
    FC_CM4_DIAG_WRITE(stage, 5U);
    MX_CM7_SEV_Init();
    HAL_SuspendTick();

    __DMB();
    if (g_fc_h755_queue.magic != FC_SHARED_QUEUE_MAGIC) {
        Error_Handler();
    }
    g_fc_h755_cm4_ready = FC_H755_CM4_READY_MAGIC;
    __DMB();
    __SEV();

    for (;;) {
        FC_CM4_DIAG_INCREMENT(loop_count);
        if (g_uart_tx_active == 0U) {
            transmit_next_sample();
        }
        /*
         * WFI puede perder la terminación DMA/UART si la interrupción ocurre
         * entre la comprobación de g_uart_tx_active y la instrucción de
         * espera. WFE conserva el evento producido por CM7 o por los callbacks
         * aunque llegue antes de entrar en reposo.
         */
        __WFE();
    }
}

static void wait_for_cm7_release(void)
{
    __HAL_RCC_HSEM_CLK_ENABLE();
    HAL_HSEM_ActivateNotification(
        __HAL_HSEM_SEMID_TO_MASK(FC_H755_HSEM_BOOT_ID));
    HAL_PWREx_ClearPendingEvent();
    HAL_PWREx_EnterSTOPMode(
        PWR_MAINREGULATOR_ON,
        PWR_STOPENTRY_WFE,
        PWR_D2_DOMAIN);
    __HAL_HSEM_CLEAR_FLAG(
        __HAL_HSEM_SEMID_TO_MASK(FC_H755_HSEM_BOOT_ID));
}

static void MX_DMA_Init(void)
{
    __HAL_RCC_DMA1_CLK_ENABLE();
    HAL_NVIC_SetPriority(DMA1_Stream0_IRQn, 5U, 0U);
    HAL_NVIC_EnableIRQ(DMA1_Stream0_IRQn);
}

static void MX_USART3_UART_Init(void)
{
    huart3.Instance = USART3;
    huart3.Init.BaudRate = FC_H755_UART_BAUD;
    huart3.Init.WordLength = UART_WORDLENGTH_8B;
    huart3.Init.StopBits = UART_STOPBITS_1;
    huart3.Init.Parity = UART_PARITY_NONE;
    huart3.Init.Mode = UART_MODE_TX;
    huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart3.Init.OverSampling = UART_OVERSAMPLING_16;
    huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart3.Init.ClockPrescaler = UART_PRESCALER_DIV1;
    huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;

    if (HAL_UART_Init(&huart3) != HAL_OK) {
        Error_Handler();
    }
}

static void MX_CM7_SEV_Init(void)
{
    /*
     * En STM32H755, un SEV ejecutado por CM7 activa CM7_SEV_IRQn en CM4.
     * SystemInit habilita SEVONPEND: sólo una transición nueva a pending
     * produce un evento. Si el IRQ queda deshabilitado y pending, únicamente
     * el primer push despierta WFE. El handler vacío consume cada pending y
     * rearma así el doorbell intercore sin usar HSEM por muestra.
     *
     * CM7 espera g_fc_h755_cm4_ready, por lo que limpiar antes de publicar
     * ready no puede perder una muestra válida.
     */
    HAL_NVIC_ClearPendingIRQ(CM7_SEV_IRQn);
    HAL_NVIC_SetPriority(
        CM7_SEV_IRQn,
        FC_H755_CM7_SEV_PRIORITY,
        0U);
    HAL_NVIC_EnableIRQ(CM7_SEV_IRQn);
}

static void transmit_next_sample(void)
{
    fc_sample_t sample;

    if (!fc_shared_queue_pop(&g_fc_h755_queue, &sample)) {
        return;
    }
    FC_CM4_DIAG_INCREMENT(samples_popped);

    fc_make_state_frame(
        &g_uart_tx_buffer.frame,
        &sample,
        FC_BOARD_H755,
        fc_shared_queue_dropped(&g_fc_h755_queue) + g_uart_errors);

    g_uart_tx_active = 1U;
#if FC_H755_SMOKE_DIAGNOSTICS
    const HAL_StatusTypeDef hal_status = HAL_UART_Transmit(
        &huart3,
        (uint8_t *)(void *)&g_uart_tx_buffer.frame,
        (uint16_t)sizeof(g_uart_tx_buffer.frame),
        100U);
    FC_CM4_DIAG_WRITE(last_hal_status, hal_status);
    g_uart_tx_active = 0U;
    if (hal_status != HAL_OK) {
        ++g_uart_errors;
        FC_CM4_DIAG_INCREMENT(uart_errors);
    } else {
        FC_CM4_DIAG_INCREMENT(dma_started);
        FC_CM4_DIAG_INCREMENT(tx_completed);
    }
#else
    const HAL_StatusTypeDef hal_status = HAL_UART_Transmit_DMA(
        &huart3,
        (uint8_t *)(void *)&g_uart_tx_buffer.frame,
        (uint16_t)sizeof(g_uart_tx_buffer.frame));
    FC_CM4_DIAG_WRITE(last_hal_status, hal_status);
    if (hal_status != HAL_OK) {
        g_uart_tx_active = 0U;
        ++g_uart_errors;
        FC_CM4_DIAG_INCREMENT(uart_errors);
    } else {
        FC_CM4_DIAG_INCREMENT(dma_started);
    }
#endif
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *uart)
{
    if (uart->Instance == USART3) {
        g_uart_tx_active = 0U;
        FC_CM4_DIAG_INCREMENT(tx_completed);
        __SEV();
    }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart)
{
    if (uart->Instance == USART3) {
        g_uart_tx_active = 0U;
        ++g_uart_errors;
        FC_CM4_DIAG_INCREMENT(uart_errors);
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
