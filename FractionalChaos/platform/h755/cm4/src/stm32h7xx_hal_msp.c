#include "main.h"

void HAL_MspInit(void)
{
    __HAL_RCC_SYSCFG_CLK_ENABLE();
}

void HAL_UART_MspInit(UART_HandleTypeDef *uart)
{
    GPIO_InitTypeDef gpio = {0};

    if (uart->Instance != USART3) {
        return;
    }

    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_USART3_CLK_ENABLE();

    /*
     * PD8 es TX hacia el VCP. PD9 se activa exclusivamente en la imagen
     * primaria con gate START/READY; los pilotos siguen siendo TX-only.
     */
#if FC_H755_PRIMARY_HANDSHAKE
    gpio.Pin = GPIO_PIN_8 | GPIO_PIN_9;
#else
    gpio.Pin = GPIO_PIN_8;
#endif
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Alternate = GPIO_AF7_USART3;
    HAL_GPIO_Init(GPIOD, &gpio);

    hdma_usart3_tx.Instance = DMA1_Stream0;
    hdma_usart3_tx.Init.Request = DMA_REQUEST_USART3_TX;
    hdma_usart3_tx.Init.Direction = DMA_MEMORY_TO_PERIPH;
    hdma_usart3_tx.Init.PeriphInc = DMA_PINC_DISABLE;
    hdma_usart3_tx.Init.MemInc = DMA_MINC_ENABLE;
    hdma_usart3_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    hdma_usart3_tx.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
    hdma_usart3_tx.Init.Mode = DMA_NORMAL;
    hdma_usart3_tx.Init.Priority = DMA_PRIORITY_HIGH;
    hdma_usart3_tx.Init.FIFOMode = DMA_FIFOMODE_DISABLE;
    if (HAL_DMA_Init(&hdma_usart3_tx) != HAL_OK) {
        Error_Handler();
    }

    __HAL_LINKDMA(uart, hdmatx, hdma_usart3_tx);

    HAL_NVIC_SetPriority(USART3_IRQn, 5U, 0U);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
}
