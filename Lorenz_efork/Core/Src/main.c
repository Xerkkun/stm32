/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : EFORK (orden fraccionario) para Lorenz en STM32F7
  * 					Salidas DAC1/DAC2 y envío UART asíncrono (IT).
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2025 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "string.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "math.h"
#include "stdio.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define ALPHA   0.92          // orden fraccionario (0<alpha<1)
#define DT      0.01          // paso de integración [s]
#define Lm      10.0           // ventana de memoria [s]

#define SIGMA   10.0
#define RHO     28.0
#define BETA    (8.0/3.0)

#define MAX_M   10         // máximo Lm/DT soportado
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
#if defined ( __ICCARM__ ) /*!< IAR Compiler */
#pragma location=0x2004c000
ETH_DMADescTypeDef  DMARxDscrTab[ETH_RX_DESC_CNT]; /* Ethernet Rx DMA Descriptors */
#pragma location=0x2004c0a0
ETH_DMADescTypeDef  DMATxDscrTab[ETH_TX_DESC_CNT]; /* Ethernet Tx DMA Descriptors */

#elif defined ( __CC_ARM )  /* MDK ARM Compiler */

__attribute__((at(0x2004c000))) ETH_DMADescTypeDef  DMARxDscrTab[ETH_RX_DESC_CNT]; /* Ethernet Rx DMA Descriptors */
__attribute__((at(0x2004c0a0))) ETH_DMADescTypeDef  DMATxDscrTab[ETH_TX_DESC_CNT]; /* Ethernet Tx DMA Descriptors */

#elif defined ( __GNUC__ ) /* GNU Compiler */

ETH_DMADescTypeDef DMARxDscrTab[ETH_RX_DESC_CNT] __attribute__((section(".RxDecripSection"))); /* Ethernet Rx DMA Descriptors */
ETH_DMADescTypeDef DMATxDscrTab[ETH_TX_DESC_CNT] __attribute__((section(".TxDecripSection")));   /* Ethernet Tx DMA Descriptors */
#endif

ETH_TxPacketConfig TxConfig;

DAC_HandleTypeDef hdac;
DMA_HandleTypeDef hdma_dac1;
DMA_HandleTypeDef hdma_dac2;

ETH_HandleTypeDef heth;

I2C_HandleTypeDef hi2c1;

TIM_HandleTypeDef htim2;

UART_HandleTypeDef huart3;
DMA_HandleTypeDef hdma_usart3_rx;
DMA_HandleTypeDef hdma_usart3_tx;

PCD_HandleTypeDef hpcd_USB_OTG_FS;

/* USER CODE BEGIN PV */
static int    m = 0;                       /* número de pasos de memoria */
static long double x_hist[MAX_M+1];       /* historial de x */
static long double y_hist[MAX_M+1];       /* historial de y */
static long double z_hist[MAX_M+1];       /* historial de z */
static long double mem_weights[MAX_M];    /* pesos precalculados */
static long double gamma_term;     /* tgamma(2-ALPHA) precalculada */

static long double h_alpha;            /* h^alpha */
static long double inv_h_gamma;        /* 1 / (h * Gamma(2-alpha)) */
static long double c2, c3;             /* abscisas de etapa EFORK */

static float mem_weights_c2[MAX_M];
static float mem_weights_c3[MAX_M];

static int idx_current = 0;                /* índice circular actual */
static int step_count  = 0;                /* contador de pasos */

static char tx_buffer[120];
static volatile uint8_t uart_busy = 0;     /* 0 = libre, 1 = ocupada */
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_ETH_Init(void);
static void MX_I2C1_Init(void);
static void MX_USART3_UART_Init(void);
static void MX_USB_OTG_FS_PCD_Init(void);
static void MX_DAC_Init(void);
static void MX_TIM2_Init(void);
/* USER CODE BEGIN PFP */
/* Calcula la memoria fraccional usando pesos precalculados */
static void memory_fractional_delta3(long double delta,
                                     long double *Mx, long double *My, long double *Mz);

/* Un paso de integración EFORK (RK3-α) para Lorenz */
static void efork_step(void);

/* RHS del sistema de Lorenz */
static void system_rhs(long double x, long double y, long double z,
                        long double *dx, long double *dy, long double *dz);

/* Mapea val en [minVal, maxVal] a [0..4095] para DAC de 12 bits */
static uint32_t float_to_dac12(long double val, long double minVal, long double maxVal);

/* Envía mensaje por UART sin bloquear */
static void enviar_mensaje(const char *msg);

/* Callback al terminar transmisión UART */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static inline int circ_idx(int base, int back, int N)
{
    int idx = base - back;
    if (idx < 0) idx += N;
    return idx;
}
/**
  * @brief  		: Evalúa la contribución fraccional usando pesos precalculados.
  * @note   		: Los pesos mem_weights[] se calculan al iniciar el programa.
**/

/* Suma de memoria para x,y,z con desplazamiento delta.
   Devuelve Mx,My,Mz = (1/(h*Gamma(2-alpha))) * sum_j Δ * w_j(delta) */
static void memory_fractional_delta3(long double delta,
                                     long double *Mx, long double *My, long double *Mz)
{
    const int N     = MAX_M + 1;
    const int Jmax  = (step_count < m) ? step_count : m; /* cuántos intervalos tengo realmente */
    long double sx = 0.0L, sy = 0.0L, sz = 0.0L;

    for (int j = 0; j < Jmax; ++j)
    {
        int ia = circ_idx(idx_current, j,   N); /* y_{n-j}   */
        int ib = circ_idx(idx_current, j+1, N); /* y_{n-j-1} */

        long double dx = x_hist[ia] - x_hist[ib];
        long double dy = y_hist[ia] - y_hist[ib];
        long double dz = z_hist[ia] - z_hist[ib];

        long double wj;
        if (delta == 0.0L) {
            wj = mem_weights[j];
        } else if (delta == c2) {
            wj = (long double)mem_weights_c2[j];
        } else if (delta == c3) {
            wj = (long double)mem_weights_c3[j];
        } else {
            long double t1 = ((long double)j + 1.0L + delta) * DT;
            long double t0 = ((long double)j        + delta) * DT;
            wj = powl(t1, 1.0L - ALPHA) - powl(t0, 1.0L - ALPHA);
        }

        sx += dx * wj;
        sy += dy * wj;
        sz += dz * wj;
    }

    /* Factor común 1/(h * Gamma(2-alpha)) */
    *Mx = inv_h_gamma * sx;
    *My = inv_h_gamma * sy;
    *Mz = inv_h_gamma * sz;
}

/**
  * @brief  Calcula un paso de Lorenz fraccionario usando Grünwald–Letnikov (memoria corta).
  *         x_{n+1} = x_{n} + dt^alpha * dx - (suma de estados pasados)
  * @note   Los arreglos x_hist, y_hist, z_hist contienen las muestras pasadas.
  *         idx_current marca la posición más reciente en el buffer circular.
  *         Recorremos j=1..m para sumar c_coef[j]* x_hist[idx_prev].
  *         Se usa la aproximación de memoria corta con pesos precalculados.
  */

static void system_rhs(long double x, long double y, long double z,
                        long double *dx, long double *dy, long double *dz)
{
     *dx = SIGMA * (y - x);
     *dy = RHO * x - y - x*z;
     *dz = x*y - BETA*z;
}

/**
   * @brief  Calcula un paso de Lorenz fraccionario usando Grünwald–Letnikov (memoria corta).
   *         x_{n+1} = x_{n} + dt^alpha * dx - (suma de estados pasados)
   * @note   Los arreglos x_hist, y_hist, z_hist contienen las muestras pasadas.
   *         idx_current marca la posición más reciente en el buffer circular.
   *         Recorremos j=1..m para sumar c_coef[j]* x_hist[idx_prev].
   *         Se usa la aproximación de memoria corta con pesos precalculados.
   */

static void efork_step(void)
{
    /* Coeficientes EFORK (una sola vez) */
    static int init = 0;
    static long double a21, a31, a32, w1, w2, w3;
    if (!init) {
        long double G1a = tgammal(1.0L + ALPHA);
        long double G2a = tgammal(1.0L + 2.0L*ALPHA);
        long double G3a = tgammal(1.0L + 3.0L*ALPHA);

        a21 = 1.0L / (2.0L * G1a * G1a);
        a31 = ( (G1a*G1a*G2a) + 2.0L*(G2a*G2a) - G3a )
              / ( 4.0L * G1a*G1a * (2.0L*G2a*G2a - G3a) );
        a32 = -( G2a ) / ( 4.0L * (2.0L*G2a*G2a - G3a) );

        w1 = ( 8.0L*powl(G1a,3)*powl(G2a,2) - 6.0L*powl(G1a,3)*G3a + (G2a*G3a) )
             / ( G1a*G2a*G3a );
        w2 = ( 2.0L*G1a*G1a*(4.0L*G2a*G2a - G3a) ) / ( G2a*G3a );
        w3 = -( 8.0L*G1a*G1a*(2.0L*G2a*G2a - G3a) ) / ( G2a*G3a );

        init = 1;
    }

    /* Estado actual y_n */
    long double xn = x_hist[idx_current];
    long double yn = y_hist[idx_current];
    long double zn = z_hist[idx_current];

    /* ----- K1 (delta = 0) ----- */
    long double M1x, M1y, M1z;
    memory_fractional_delta3(0.0L, &M1x, &M1y, &M1z);

    long double fx, fy, fz;
    system_rhs(xn, yn, zn, &fx, &fy, &fz);

    long double K1x = h_alpha * (fx - M1x);
    long double K1y = h_alpha * (fy - M1y);
    long double K1z = h_alpha * (fz - M1z);

    /* ----- K2 (delta = c2) ----- */
    long double x2 = xn + a21*K1x;
    long double y2 = yn + a21*K1y;
    long double z2 = zn + a21*K1z;

    long double M2x, M2y, M2z;
    memory_fractional_delta3(c2, &M2x, &M2y, &M2z);

    system_rhs(x2, y2, z2, &fx, &fy, &fz);
    long double K2x = h_alpha * (fx - M2x);
    long double K2y = h_alpha * (fy - M2y);
    long double K2z = h_alpha * (fz - M2z);

    /* ----- K3 (delta = c3) ----- */
    long double x3 = xn + a31*K1x + a32*K2x;
    long double y3 = yn + a31*K1y + a32*K2y;
    long double z3 = zn + a31*K1z + a32*K2z;

    long double M3x, M3y, M3z;
    memory_fractional_delta3(c3, &M3x, &M3y, &M3z);

    system_rhs(x3, y3, z3, &fx, &fy, &fz);
    long double K3x = h_alpha * (fx - M3x);
    long double K3y = h_alpha * (fy - M3y);
    long double K3z = h_alpha * (fz - M3z);

    /* Avance */
    long double xnp1 = xn + w1*K1x + w2*K2x + w3*K3x;
    long double ynp1 = yn + w1*K1y + w2*K2y + w3*K3y;
    long double znp1 = zn + w1*K1z + w2*K2z + w3*K3z;

    /* Push en buffer circular y contadores */
    idx_current = (idx_current + 1) % (MAX_M + 1);
    x_hist[idx_current] = xnp1;
    y_hist[idx_current] = ynp1;
    z_hist[idx_current] = znp1;
    step_count++;

    /* DAC + UART (debug) */
    HAL_DAC_SetValue(&hdac, DAC_CHANNEL_1, DAC_ALIGN_12B_R,
                     float_to_dac12(xnp1, -25.0L, 25.0L));
    HAL_DAC_SetValue(&hdac, DAC_CHANNEL_2, DAC_ALIGN_12B_R,
                     float_to_dac12(ynp1, -30.0L, 30.0L));

    char buf[96];
    snprintf(buf, sizeof(buf),
             "EFORK -> x=% .3Lf, y=% .3Lf, z=% .3Lf\r\n", xnp1, ynp1, znp1);
    enviar_mensaje(buf);
}


/**
 * @brief Mapea val en [minVal, maxVal] a [0, 4095] para DAC de 12 bits.
 */
static uint32_t float_to_dac12(long double val, long double minVal, long double maxVal)
{
    // Evita división por cero si maxVal == minVal
    long double rango = maxVal - minVal;
    if (fabsl(rango) < 1e-16) {
        rango = 1e-16;
    }

    // Mapeo lineal
    long double scale = (val - minVal) * (4095.0L / rango);

    // Saturación en [0, 4095]
    if (scale < 0.0L)    scale = 0.0L;
    if (scale > 4095.0L) scale = 4095.0L;

    return (uint32_t) scale;
}

/**
  * @brief Envía un mensaje por UART con interrupción (no bloqueante).
  *        Si la UART está ocupada, descarta (en un sistema real, podrías encolar).
  */
static void enviar_mensaje(const char *msg)
{
    if(uart_busy == 0)
    {
        uart_busy = 1;
        strncpy(tx_buffer, msg, sizeof(tx_buffer)-1);
        tx_buffer[sizeof(tx_buffer)-1] = '\0';
        HAL_UART_Transmit_IT(&huart3,
                             (uint8_t*)tx_buffer,
                             strlen(tx_buffer));
    }
    else
    {
        // UART ocupada: descartar o encolar
    }
}

/**
  * @brief Callback al terminar la transmisión por IT UART.
  */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if(huart == &huart3)
    {
        uart_busy = 0;
    }
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  *
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_ETH_Init();
  MX_I2C1_Init();
  MX_USART3_UART_Init();
  MX_USB_OTG_FS_PCD_Init();
  MX_DAC_Init();
  MX_TIM2_Init();
  /* USER CODE BEGIN 2 */
  /* temporización de referencias para DAC por TIM2 */
  HAL_TIM_Base_Start(&htim2);

  /* Calcula m = LM/DT y limita si excede MAX_M */
  m = (int)(Lm / DT);
  if (m > MAX_M) m = MAX_M;
  gamma_term = tgammal(2.0L - ALPHA);
  /* h^alpha y 1/(h*Gamma(2-alpha)) */
  h_alpha    = powl(DT, ALPHA);
  inv_h_gamma = 1.0L / (DT * gamma_term);

  /* Abscisas EFORK (3-etapas). Del apéndice del método:
     c2 = (1/(2*Gamma(1+alpha)))^(1/alpha)
     c3 = (1/(4*Gamma(1+alpha)))^(1/alpha) */
  long double G1a = tgammal(1.0L + ALPHA);
  c2 = powl(1.0L / (2.0L * G1a), 1.0L / ALPHA);
  c3 = powl(1.0L / (4.0L * G1a), 1.0L / ALPHA);

  for (int i = 0; i < m; i++) {
      long double t10 = ((long double)(i+1) + c2) * DT;
      long double t00 = ((long double)i       + c2) * DT;
      mem_weights_c2[i] = (float)(powl(t10, 1.0L - ALPHA) - powl(t00, 1.0L - ALPHA));

      long double t13 = ((long double)(i+1) + c3) * DT;
      long double t03 = ((long double)i       + c3) * DT;
      mem_weights_c3[i] = (float)(powl(t13, 1.0L - ALPHA) - powl(t03, 1.0L - ALPHA));
  }

  for (int i = 0; i < m; i++) {
	  long double t1 = (long double)(i + 1) * DT;
	  long double t0 = (long double)i * DT;
	  mem_weights[i] = powl(t1, 1.0L - ALPHA) - powl(t0, 1.0L - ALPHA);
  }

  /* Inicializa historial: tiempos negativos para memoria inicial */
  /* Inicializa historial de estados a cero */
  for (int i = 0; i <= m; i++) {
	  x_hist[i] = y_hist[i] = z_hist[i] = 0.0L;
  }
  /* Condiciones iniciales */
  x_hist[0] = 0.1L;
  y_hist[0] = 0.1L;
  z_hist[0] = 0.1L;
  idx_current = 0;
  step_count  = 0;

  /* Arranca DAC y timer para triggear DAC */
  HAL_DAC_Start(&hdac, DAC_CHANNEL_1);
  HAL_DAC_Start(&hdac, DAC_CHANNEL_2);
  HAL_TIM_Base_Start(&htim2);

  enviar_mensaje("\r\n*** EFORK Frac (Lorenz) + UART IT ***\r\n");


  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */
      efork_step();
      HAL_Delay(10);
    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure LSE Drive Capability
  */
  HAL_PWR_EnableBkUpAccess();

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE3);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
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
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief DAC Initialization Function
  * @param None
  * @retval None
  */
static void MX_DAC_Init(void)
{

  /* USER CODE BEGIN DAC_Init 0 */

  /* USER CODE END DAC_Init 0 */

  DAC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN DAC_Init 1 */

  /* USER CODE END DAC_Init 1 */

  /** DAC Initialization
  */
  hdac.Instance = DAC;
  if (HAL_DAC_Init(&hdac) != HAL_OK)
  {
    Error_Handler();
  }

  /** DAC channel OUT1 config
  */
  sConfig.DAC_Trigger = DAC_TRIGGER_T2_TRGO;
  sConfig.DAC_OutputBuffer = DAC_OUTPUTBUFFER_ENABLE;
  if (HAL_DAC_ConfigChannel(&hdac, &sConfig, DAC_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }

  /** DAC channel OUT2 config
  */
  if (HAL_DAC_ConfigChannel(&hdac, &sConfig, DAC_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN DAC_Init 2 */

  /* USER CODE END DAC_Init 2 */

}

/**
  * @brief ETH Initialization Function
  * @param None
  * @retval None
  */
static void MX_ETH_Init(void)
{

  /* USER CODE BEGIN ETH_Init 0 */

  /* USER CODE END ETH_Init 0 */

   static uint8_t MACAddr[6];

  /* USER CODE BEGIN ETH_Init 1 */

  /* USER CODE END ETH_Init 1 */
  heth.Instance = ETH;
  MACAddr[0] = 0x00;
  MACAddr[1] = 0x80;
  MACAddr[2] = 0xE1;
  MACAddr[3] = 0x00;
  MACAddr[4] = 0x00;
  MACAddr[5] = 0x00;
  heth.Init.MACAddr = &MACAddr[0];
  heth.Init.MediaInterface = HAL_ETH_RMII_MODE;
  heth.Init.TxDesc = DMATxDscrTab;
  heth.Init.RxDesc = DMARxDscrTab;
  heth.Init.RxBuffLen = 1524;

  /* USER CODE BEGIN MACADDRESS */

  /* USER CODE END MACADDRESS */

  if (HAL_ETH_Init(&heth) != HAL_OK)
  {
    Error_Handler();
  }

  memset(&TxConfig, 0 , sizeof(ETH_TxPacketConfig));
  TxConfig.Attributes = ETH_TX_PACKETS_FEATURES_CSUM | ETH_TX_PACKETS_FEATURES_CRCPAD;
  TxConfig.ChecksumCtrl = ETH_CHECKSUM_IPHDR_PAYLOAD_INSERT_PHDR_CALC;
  TxConfig.CRCPadCtrl = ETH_CRC_PAD_INSERT;
  /* USER CODE BEGIN ETH_Init 2 */

  /* USER CODE END ETH_Init 2 */

}

/**
  * @brief I2C1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_I2C1_Init(void)
{

  /* USER CODE BEGIN I2C1_Init 0 */

  /* USER CODE END I2C1_Init 0 */

  /* USER CODE BEGIN I2C1_Init 1 */

  /* USER CODE END I2C1_Init 1 */
  hi2c1.Instance = I2C1;
  hi2c1.Init.Timing = 0x00808CD2;
  hi2c1.Init.OwnAddress1 = 0;
  hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c1.Init.OwnAddress2 = 0;
  hi2c1.Init.OwnAddress2Masks = I2C_OA2_NOMASK;
  hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Analogue filter
  */
  if (HAL_I2CEx_ConfigAnalogFilter(&hi2c1, I2C_ANALOGFILTER_ENABLE) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Digital filter
  */
  if (HAL_I2CEx_ConfigDigitalFilter(&hi2c1, 0) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN I2C1_Init 2 */

  /* USER CODE END I2C1_Init 2 */

}

/**
  * @brief TIM2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM2_Init(void)
{

  /* USER CODE BEGIN TIM2_Init 0 */

  /* USER CODE END TIM2_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};

  /* USER CODE BEGIN TIM2_Init 1 */

  /* USER CODE END TIM2_Init 1 */
  htim2.Instance = TIM2;
  htim2.Init.Prescaler = 72-1;
  htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim2.Init.Period = 100;
  htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim2, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim2, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM2_Init 2 */

  /* USER CODE END TIM2_Init 2 */

}

/**
  * @brief USART3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART3_UART_Init(void)
{

  /* USER CODE BEGIN USART3_Init 0 */

  /* USER CODE END USART3_Init 0 */

  /* USER CODE BEGIN USART3_Init 1 */

  /* USER CODE END USART3_Init 1 */
  huart3.Instance = USART3;
  huart3.Init.BaudRate = 115200;
  huart3.Init.WordLength = UART_WORDLENGTH_8B;
  huart3.Init.StopBits = UART_STOPBITS_1;
  huart3.Init.Parity = UART_PARITY_NONE;
  huart3.Init.Mode = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart3) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART3_Init 2 */

  /* USER CODE END USART3_Init 2 */

}

/**
  * @brief USB_OTG_FS Initialization Function
  * @param None
  * @retval None
  */
static void MX_USB_OTG_FS_PCD_Init(void)
{

  /* USER CODE BEGIN USB_OTG_FS_Init 0 */

  /* USER CODE END USB_OTG_FS_Init 0 */

  /* USER CODE BEGIN USB_OTG_FS_Init 1 */

  /* USER CODE END USB_OTG_FS_Init 1 */
  hpcd_USB_OTG_FS.Instance = USB_OTG_FS;
  hpcd_USB_OTG_FS.Init.dev_endpoints = 6;
  hpcd_USB_OTG_FS.Init.speed = PCD_SPEED_FULL;
  hpcd_USB_OTG_FS.Init.dma_enable = DISABLE;
  hpcd_USB_OTG_FS.Init.phy_itface = PCD_PHY_EMBEDDED;
  hpcd_USB_OTG_FS.Init.Sof_enable = ENABLE;
  hpcd_USB_OTG_FS.Init.low_power_enable = DISABLE;
  hpcd_USB_OTG_FS.Init.lpm_enable = DISABLE;
  hpcd_USB_OTG_FS.Init.vbus_sensing_enable = ENABLE;
  hpcd_USB_OTG_FS.Init.use_dedicated_ep1 = DISABLE;
  if (HAL_PCD_Init(&hpcd_USB_OTG_FS) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USB_OTG_FS_Init 2 */

  /* USER CODE END USB_OTG_FS_Init 2 */

}

/**
  * Enable DMA controller clock
  */
static void MX_DMA_Init(void)
{

  /* DMA controller clock enable */
  __HAL_RCC_DMA1_CLK_ENABLE();

  /* DMA interrupt init */
  /* DMA1_Stream1_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA1_Stream1_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream1_IRQn);
  /* DMA1_Stream3_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA1_Stream3_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream3_IRQn);
  /* DMA1_Stream5_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA1_Stream5_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream5_IRQn);
  /* DMA1_Stream6_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA1_Stream6_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream6_IRQn);

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOB, LD1_Pin|LD3_Pin|LD2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(USB_PowerSwitchOn_GPIO_Port, USB_PowerSwitchOn_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin : USER_Btn_Pin */
  GPIO_InitStruct.Pin = USER_Btn_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(USER_Btn_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pins : LD1_Pin LD3_Pin LD2_Pin */
  GPIO_InitStruct.Pin = LD1_Pin|LD3_Pin|LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /*Configure GPIO pin : USB_PowerSwitchOn_Pin */
  GPIO_InitStruct.Pin = USB_PowerSwitchOn_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(USB_PowerSwitchOn_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : USB_OverCurrent_Pin */
  GPIO_InitStruct.Pin = USB_OverCurrent_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(USB_OverCurrent_GPIO_Port, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
