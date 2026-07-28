#ifndef FC_H755_CM4_MAIN_H
#define FC_H755_CM4_MAIN_H

#include "stm32h7xx_hal.h"

extern UART_HandleTypeDef huart3;
extern DMA_HandleTypeDef hdma_usart3_tx;

void Error_Handler(void);

#endif

