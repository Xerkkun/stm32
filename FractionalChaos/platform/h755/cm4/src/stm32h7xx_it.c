#include "main.h"
#include "h755_shared_memory.h"
#include "stm32h7xx_it.h"

#ifndef FC_H755_SMOKE_DIAGNOSTICS
#define FC_H755_SMOKE_DIAGNOSTICS 0
#endif

void HardFault_Handler(void)
{
#if FC_H755_SMOKE_DIAGNOSTICS
    g_fc_h755_cm4_diagnostics.hardfault_count++;
    g_fc_h755_cm4_diagnostics.cfsr = SCB->CFSR;
    g_fc_h755_cm4_diagnostics.hfsr = SCB->HFSR;
    g_fc_h755_cm4_diagnostics.mmfar = SCB->MMFAR;
    g_fc_h755_cm4_diagnostics.bfar = SCB->BFAR;
    g_fc_h755_cm4_diagnostics.stage = 0xDEAD0004U;
    __DMB();
#endif
    __disable_irq();
    for (;;) {
        __NOP();
    }
}

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

void CM7_SEV_IRQHandler(void)
{
    /*
     * La entrada a la excepción consume el estado pending de CM7_SEV_IRQn.
     * No existe una bandera periférica adicional que deba reconocerse aquí.
     * Dejar que el handler retorne permite que el siguiente SEV del CM7 vuelva
     * a producir una transición a pending y, por tanto, despierte otro WFE.
     */
}

void SysTick_Handler(void)
{
    HAL_IncTick();
}
