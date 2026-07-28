#ifndef STM32H7XX_HAL_CONF_H
#define STM32H7XX_HAL_CONF_H

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Se habilitan únicamente los módulos usados por el árbol de reloj, HSEM0 y
 * USART3-TX mediante DMA1. No se habilitan ETH, USB, I2C, DAC, TIM ni recepción
 * UART.
 */
#define HAL_MODULE_ENABLED
#define HAL_CORTEX_MODULE_ENABLED
#define HAL_DMA_MODULE_ENABLED
#define HAL_FLASH_MODULE_ENABLED
#define HAL_GPIO_MODULE_ENABLED
#define HAL_HSEM_MODULE_ENABLED
#define HAL_PWR_MODULE_ENABLED
#define HAL_RCC_MODULE_ENABLED
#define HAL_UART_MODULE_ENABLED

#ifndef HSE_VALUE
#define HSE_VALUE                    8000000UL
#endif
#ifndef HSE_STARTUP_TIMEOUT
#define HSE_STARTUP_TIMEOUT          100UL
#endif
#ifndef CSI_VALUE
#define CSI_VALUE                    4000000UL
#endif
#ifndef HSI_VALUE
#define HSI_VALUE                    64000000UL
#endif
#ifndef LSI_VALUE
#define LSI_VALUE                    32000UL
#endif
#ifndef LSE_VALUE
#define LSE_VALUE                    32768UL
#endif
#ifndef LSE_STARTUP_TIMEOUT
#define LSE_STARTUP_TIMEOUT          5000UL
#endif
#ifndef EXTERNAL_CLOCK_VALUE
#define EXTERNAL_CLOCK_VALUE         12288000UL
#endif

#define VDD_VALUE                    3300UL
#define TICK_INT_PRIORITY            15UL
#define USE_RTOS                     0U
#define USE_SD_TRANSCEIVER           0U

#define USE_HAL_DMA_REGISTER_CALLBACKS  0U
#define USE_HAL_HSEM_REGISTER_CALLBACKS 0U
#define USE_HAL_UART_REGISTER_CALLBACKS 0U

#ifdef HAL_CORTEX_MODULE_ENABLED
#include "stm32h7xx_hal_cortex.h"
#endif
#ifdef HAL_DMA_MODULE_ENABLED
#include "stm32h7xx_hal_dma.h"
#endif
#ifdef HAL_FLASH_MODULE_ENABLED
#include "stm32h7xx_hal_flash.h"
#endif
#ifdef HAL_GPIO_MODULE_ENABLED
#include "stm32h7xx_hal_gpio.h"
#endif
#ifdef HAL_HSEM_MODULE_ENABLED
#include "stm32h7xx_hal_hsem.h"
#endif
#ifdef HAL_PWR_MODULE_ENABLED
#include "stm32h7xx_hal_pwr.h"
#endif
#ifdef HAL_RCC_MODULE_ENABLED
#include "stm32h7xx_hal_rcc.h"
#endif
#ifdef HAL_UART_MODULE_ENABLED
#include "stm32h7xx_hal_uart.h"
#endif

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line);
#define assert_param(expr) \
    ((expr) ? (void)0U : assert_failed((uint8_t *)__FILE__, __LINE__))
#else
#define assert_param(expr) ((void)0U)
#endif

#ifdef __cplusplus
}
#endif

#endif
