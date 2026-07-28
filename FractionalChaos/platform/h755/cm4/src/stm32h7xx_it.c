#include "main.h"
#include "stm32h7xx_it.h"

void DMA1_Stream0_IRQHandler(void)
{
    HAL_DMA_IRQHandler(&hdma_usart3_tx);
}

void USART3_IRQHandler(void)
{
    /*
     * La interrupción TC completa el estado HAL después de que DMA transfiere
     * el último byte al TDR; sin ella sólo se enviaría la primera trama.
     */
    HAL_UART_IRQHandler(&huart3);
}

void SysTick_Handler(void)
{
    HAL_IncTick();
}
