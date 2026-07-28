#include "main.h"
#include "stm32f7xx_it.h"

void NMI_Handler(void)
{
    for (;;) {
        __NOP();
    }
}

void HardFault_Handler(void)
{
    for (;;) {
        __NOP();
    }
}

void MemManage_Handler(void)
{
    for (;;) {
        __NOP();
    }
}

void BusFault_Handler(void)
{
    for (;;) {
        __NOP();
    }
}

void UsageFault_Handler(void)
{
    for (;;) {
        __NOP();
    }
}

void SVC_Handler(void)
{
}

void DebugMon_Handler(void)
{
}

void PendSV_Handler(void)
{
}

void SysTick_Handler(void)
{
    HAL_IncTick();
}

void DMA1_Stream3_IRQHandler(void)
{
    HAL_DMA_IRQHandler(&hdma_usart3_tx);
}

void USART3_IRQHandler(void)
{
    HAL_UART_IRQHandler(&huart3);
}
