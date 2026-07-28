/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Código completo para simular el sistema de Lorenz
  *                   usando RK4, salida en DAC y transmisión UART.
  ******************************************************************************
  * @attention
  *
  * Copyright (c)
  * Este ejemplo se distribuye “tal cual”, sin garantías de ningún tipo.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "stdio.h"
#include "math.h"

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
// Parámetros del sistema Lorenz
#define SIGMA  10.0f
#define RHO    28.0f
#define BETA   (8.0f/3.0f)
#define DT     0.001f   // Paso de integración

/* USER CODE BEGIN PD */
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */
/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
DAC_HandleTypeDef hdac;
UART_HandleTypeDef huart3;
TIM_HandleTypeDef htim2;

/* Variables globales del sistema Lorenz */
float x = 1.0f, y = 1.0f, z = 1.0f;

/* USER CODE BEGIN PV */
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DAC_Init(void);
static void MX_TIM2_Init(void);
static void MX_USART3_UART_Init(void);

/* USER CODE BEGIN PFP */
void lorenzRK4(void);
/* USER CODE END PFP */

/* USER CODE BEGIN 0 */
void lorenzRK4(void)
{
    float k1x, k1y, k1z;
    float k2x, k2y, k2z;
    float k3x, k3y, k3z;
    float k4x, k4y, k4z;
    float x_temp, y_temp, z_temp;

    // k1
    k1x = DT * (SIGMA * (y - x));
    k1y = DT * (x * (RHO - z) - y);
    k1z = DT * (x * y - BETA * z);

    // k2
    x_temp = x + k1x / 2.0f;
    y_temp = y + k1y / 2.0f;
    z_temp = z + k1z / 2.0f;
    k2x = DT * (SIGMA * (y_temp - x_temp));
    k2y = DT * (x_temp * (RHO - z_temp) - y_temp);
    k2z = DT * (x_temp * y_temp - BETA * z_temp);

    // k3
    x_temp = x + k2x / 2.0f;
    y_temp = y + k2y / 2.0f;
    z_temp = z + k2z / 2.0f;
    k3x = DT * (SIGMA * (y_temp - x_temp));
    k3y = DT * (x_temp * (RHO - z_temp) - y_temp);
    k3z = DT * (x_temp * y_temp - BETA * z_temp);

    // k4
    x_temp = x + k3x;
    y_temp = y + k3y;
    z_temp = z + k3z;
    k4x = DT * (SIGMA * (y_temp - x_temp));
    k4y = DT * (x_temp * (RHO - z_temp) - y_temp);
    k4z = DT * (x_temp * y_temp - BETA * z_temp);

    // Actualizar las variables
    x += (k1x + 2.0f*k2x + 2.0f*k3x + k4x) / 6.0f;
    y += (k1y + 2.0f*k2y + 2.0f*k3y + k4y) / 6.0f;
    z += (k1z + 2.0f*k2z + 2.0f*k3z + k4z) / 6.0f;
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{
  /* USER CODE BEGIN 1 */
  // Inicialización de variables del sistema Lorenz ya definida globalmente.
  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  HAL_Init();

  /* Configure the system clock */
  SystemClock_Config();

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DAC_Init();
  MX_TIM2_Init();
  MX_USART3_UART_Init();

  /* USER CODE BEGIN 2 */
  // Iniciar DAC (se pueden utilizar ambos canales para diferentes proyecciones)
  HAL_DAC_Start(&hdac, DAC_CHANNEL_1);
  HAL_DAC_Start(&hdac, DAC_CHANNEL_2);

  // Iniciar TIM2 en modo base para generar la interrupción periódica
  HAL_TIM_Base_Start_IT(&htim2);
  /* USER CODE END 2 */

  /* Infinite loop */
  while (1)
  {
    // El procesamiento se realiza en la interrupción del TIM2.
  }
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /* Configuración de osciladores */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_BYPASS;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = 4;
  RCC_OscInitStruct.PLL.PLLN = 72;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 3;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    while(1);
  }
  /* Configuración de buses */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK
                              | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    while(1);
  }
}

/**
  * @brief DAC Initialization Function
  * @param None
  * @retval None
  */
static void MX_DAC_Init(void)
{
  DAC_ChannelConfTypeDef sConfig = {0};

  hdac.Instance = DAC;
  if (HAL_DAC_Init(&hdac) != HAL_OK)
  {
    while(1);
  }
  /* Configuración del canal 1 */
  sConfig.DAC_Trigger = DAC_TRIGGER_T2_TRGO;  // En este ejemplo usamos TIM2 para gatillar, aunque la actualización se hace desde la interrupción
  sConfig.DAC_OutputBuffer = DAC_OUTPUTBUFFER_ENABLE;
  if (HAL_DAC_ConfigChannel(&hdac, &sConfig, DAC_CHANNEL_1) != HAL_OK)
  {
    while(1);
  }
  /* Configuración del canal 2 */
  if (HAL_DAC_ConfigChannel(&hdac, &sConfig, DAC_CHANNEL_2) != HAL_OK)
  {
    while(1);
  }
}

/**
  * @brief TIM2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM2_Init(void)
{
  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};

  htim2.Instance = TIM2;
  htim2.Init.Prescaler = 72 - 1;  // Suponiendo 72MHz, la cuenta se realiza a 1MHz
  htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim2.Init.Period = 100 - 1;    // Periodo de 100 µs → 10kHz (ajusta según la resolución que necesites)
  htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim2) != HAL_OK)
  {
    while(1);
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim2, &sClockSourceConfig) != HAL_OK)
  {
    while(1);
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim2, &sMasterConfig) != HAL_OK)
  {
    while(1);
  }
}

/**
  * @brief USART3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART3_UART_Init(void)
{
  huart3.Instance = USART3;
  huart3.Init.BaudRate = 115200;
  huart3.Init.WordLength = UART_WORDLENGTH_8B;
  huart3.Init.StopBits = UART_STOPBITS_1;
  huart3.Init.Parity = UART_PARITY_NONE;
  huart3.Init.Mode = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart3) != HAL_OK)
  {
    while(1);
  }
}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  /* Habilitar reloj para los puertos GPIO usados */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  /* Puedes agregar la configuración de otros pines según requiera tu placa */
}

/* USER CODE BEGIN 4 */
/**
  * @brief Callback de interrupción del Timer
  * @param htim: puntero a la estructura del timer
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if(htim->Instance == TIM2)
  {
    // Actualizar la integración del sistema de Lorenz
    lorenzRK4();

    // Escalar x e y a rango 0-4095 para salida DAC
    // Se utiliza un offset y escala aproximados; ajústalos según el rango esperado
    uint32_t dac_val_x = (uint32_t)(((x + 30.0f) / 60.0f) * 4095);
    uint32_t dac_val_y = (uint32_t)(((y + 30.0f) / 60.0f) * 4095);

    HAL_DAC_SetValue(&hdac, DAC_CHANNEL_1, DAC_ALIGN_12B_R, dac_val_x);
    HAL_DAC_SetValue(&hdac, DAC_CHANNEL_2, DAC_ALIGN_12B_R, dac_val_y);

    // Preparar cadena para enviar por UART: x, y, z
    char buffer[50];
    int len = snprintf(buffer, sizeof(buffer), "%.3f,%.3f,%.3f\r\n", x, y, z);
    HAL_UART_Transmit(&huart3, (uint8_t*)buffer, len, 10);
  }
}
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  __disable_irq();
  while (1)
  {
    // En caso de error, puede implementarse una acción indicativa
  }
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  // Puedes implementar una salida o reinicio
}
#endif /* USE_FULL_ASSERT */
