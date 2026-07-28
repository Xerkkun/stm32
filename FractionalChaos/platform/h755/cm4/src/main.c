#include "main.h"

#include "fc_protocol.h"
#include "h755_shared_memory.h"

#include <stdint.h>

#define FC_H755_HSEM_BOOT_ID       0U
#define FC_H755_UART_BAUD          921600U
#define FC_H755_DMA_BUFFER_BYTES   64U

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

UART_HandleTypeDef huart3;
DMA_HandleTypeDef hdma_usart3_tx;

static fc_h755_dma_buffer_t g_uart_tx_buffer
    __attribute__((section(".dma_buffer"), aligned(32), used));
static volatile uint8_t g_uart_tx_active;
static volatile uint32_t g_uart_errors;

static void wait_for_cm7_release(void);
static void MX_DMA_Init(void);
static void MX_USART3_UART_Init(void);
static void transmit_next_sample(void);

int main(void)
{
    HAL_Init();
    wait_for_cm7_release();
    SystemCoreClockUpdate();

    MX_DMA_Init();
    MX_USART3_UART_Init();
    HAL_SuspendTick();

    __DMB();
    if (g_fc_h755_queue.magic != FC_SHARED_QUEUE_MAGIC) {
        Error_Handler();
    }
    g_fc_h755_cm4_ready = FC_H755_CM4_READY_MAGIC;
    __DMB();
    __SEV();

    for (;;) {
        if (g_uart_tx_active == 0U) {
            transmit_next_sample();
        }
        if (g_uart_tx_active != 0U) {
            __WFI();
        } else {
            __WFE();
        }
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

static void transmit_next_sample(void)
{
    fc_sample_t sample;

    if (!fc_shared_queue_pop(&g_fc_h755_queue, &sample)) {
        return;
    }

    fc_make_state_frame(
        &g_uart_tx_buffer.frame,
        &sample,
        FC_BOARD_H755,
        fc_shared_queue_dropped(&g_fc_h755_queue) + g_uart_errors);

    g_uart_tx_active = 1U;
    if (HAL_UART_Transmit_DMA(
            &huart3,
            (uint8_t *)(void *)&g_uart_tx_buffer.frame,
            (uint16_t)sizeof(g_uart_tx_buffer.frame)) != HAL_OK) {
        g_uart_tx_active = 0U;
        ++g_uart_errors;
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
        ++g_uart_errors;
    }
}

void Error_Handler(void)
{
    __disable_irq();
    for (;;) {
        __NOP();
    }
}

