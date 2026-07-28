#ifndef FC_F746_MAIN_H
#define FC_F746_MAIN_H

#include "stm32f7xx_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

extern UART_HandleTypeDef huart3;
extern DMA_HandleTypeDef hdma_usart3_tx;

void Error_Handler(void);

#ifdef __cplusplus
}
#endif

#endif
