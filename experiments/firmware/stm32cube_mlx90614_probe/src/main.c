#include "main.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MLX90614_ADDR 0x5AU
#define MLX90614_RAM_TA 0x06U
#define MLX90614_RAM_TOBJ1 0x07U

#ifndef MLX_I2C_TIMING
#define MLX_I2C_TIMING 0x307075B1U
#endif

#ifndef MLX_UART_BAUD
#define MLX_UART_BAUD 2000000U
#endif

#ifndef MLX_SAMPLE_INTERVAL_MS
#define MLX_SAMPLE_INTERVAL_MS 100U
#endif

#define MLX_FLAG_READ_ERROR 0x01U
#define MLX_FLAG_PEC_PRESENT 0x02U

I2C_HandleTypeDef hi2c1;
UART_HandleTypeDef huart3;

static uint32_t sequence_number;
static uint32_t sample_interval_ms = MLX_SAMPLE_INTERVAL_MS;

static void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_I2C1_Init(void);
static void MX_USART3_UART_Init(void);
static void mlx_i2c_bus_recover(void);
static HAL_StatusTypeDef mlx_read_word(uint8_t command, uint16_t *word, uint8_t *pec);
static int32_t mlx_word_to_centideg(uint16_t word);
static void format_centideg(char *buffer, size_t buffer_size, int32_t centideg);
static void handle_uart_commands(void);
static void send_sample(void);
static void uart_write_text(const char *text);
static uint32_t elapsed_us_since(uint32_t start_cycles);
static void dwt_init(void);

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    dwt_init();
    MX_GPIO_Init();
    mlx_i2c_bus_recover();
    MX_I2C1_Init();
    MX_USART3_UART_Init();

    uart_write_text("\r\nMLX90614_BOOT,address=0x5A,bus=I2C1,pins=PB9/PB8\r\n");
    if (HAL_I2C_IsDeviceReady(&hi2c1, MLX90614_ADDR << 1, 3, 100U) != HAL_OK) {
        uart_write_text("MLX90614_ERROR_NOT_FOUND\r\n");
    } else {
        uart_write_text("MLX90614_STREAM_BEGIN\r\n");
    }

    uint32_t last_sample_ms = 0;
    while (1) {
        handle_uart_commands();
        const uint32_t now_ms = HAL_GetTick();
        if (sample_interval_ms == 0U || (uint32_t)(now_ms - last_sample_ms) >= sample_interval_ms) {
            last_sample_ms = now_ms;
            send_sample();
            HAL_GPIO_TogglePin(GPIOB, GPIO_PIN_0);
        }
    }
}

static void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};
    RCC_PeriphCLKInitTypeDef PeriphClkInitStruct = {0};

    HAL_PWREx_ConfigSupply(PWR_LDO_SUPPLY);
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE0);
    while (!__HAL_PWR_GET_FLAG(PWR_FLAG_VOSRDY)) {
    }

    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI48 | RCC_OSCILLATORTYPE_HSE;
    RCC_OscInitStruct.HSEState = RCC_HSE_BYPASS;
    RCC_OscInitStruct.HSI48State = RCC_HSI48_ON;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    RCC_OscInitStruct.PLL.PLLM = 1;
    RCC_OscInitStruct.PLL.PLLN = 120;
    RCC_OscInitStruct.PLL.PLLP = 2;
    RCC_OscInitStruct.PLL.PLLQ = 8;
    RCC_OscInitStruct.PLL.PLLR = 2;
    RCC_OscInitStruct.PLL.PLLRGE = RCC_PLL1VCIRANGE_3;
    RCC_OscInitStruct.PLL.PLLVCOSEL = RCC_PLL1VCOWIDE;
    RCC_OscInitStruct.PLL.PLLFRACN = 0;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) {
        Error_Handler();
    }

    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                  RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2 |
                                  RCC_CLOCKTYPE_D3PCLK1 | RCC_CLOCKTYPE_D1PCLK1;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.SYSCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_HCLK_DIV2;
    RCC_ClkInitStruct.APB3CLKDivider = RCC_APB3_DIV2;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_APB1_DIV2;
    RCC_ClkInitStruct.APB2CLKDivider = RCC_APB2_DIV2;
    RCC_ClkInitStruct.APB4CLKDivider = RCC_APB4_DIV2;
    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK) {
        Error_Handler();
    }

    PeriphClkInitStruct.PeriphClockSelection = RCC_PERIPHCLK_USART234578 | RCC_PERIPHCLK_I2C123;
    PeriphClkInitStruct.Usart234578ClockSelection = RCC_USART234578CLKSOURCE_D2PCLK1;
    PeriphClkInitStruct.I2c123ClockSelection = RCC_I2C123CLKSOURCE_D2PCLK1;
    if (HAL_RCCEx_PeriphCLKConfig(&PeriphClkInitStruct) != HAL_OK) {
        Error_Handler();
    }
}

static void MX_GPIO_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    __HAL_RCC_GPIOB_CLK_ENABLE();

    GPIO_InitStruct.Pin = GPIO_PIN_0;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_RESET);
}

static void MX_I2C1_Init(void)
{
    hi2c1.Instance = I2C1;
    hi2c1.Init.Timing = MLX_I2C_TIMING;
    hi2c1.Init.OwnAddress1 = 0;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.OwnAddress2 = 0;
    hi2c1.Init.OwnAddress2Masks = I2C_OA2_NOMASK;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    if (HAL_I2C_Init(&hi2c1) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_I2CEx_ConfigAnalogFilter(&hi2c1, I2C_ANALOGFILTER_ENABLE) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_I2CEx_ConfigDigitalFilter(&hi2c1, 0) != HAL_OK) {
        Error_Handler();
    }
}

static void MX_USART3_UART_Init(void)
{
    huart3.Instance = USART3;
    huart3.Init.BaudRate = MLX_UART_BAUD;
    huart3.Init.WordLength = UART_WORDLENGTH_8B;
    huart3.Init.StopBits = UART_STOPBITS_1;
    huart3.Init.Parity = UART_PARITY_NONE;
    huart3.Init.Mode = UART_MODE_TX_RX;
    huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart3.Init.OverSampling = UART_OVERSAMPLING_8;
    huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart3.Init.ClockPrescaler = UART_PRESCALER_DIV1;
    huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
    if (HAL_UART_Init(&huart3) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_UARTEx_SetTxFifoThreshold(&huart3, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_UARTEx_SetRxFifoThreshold(&huart3, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK) {
        Error_Handler();
    }
    if (HAL_UARTEx_DisableFifoMode(&huart3) != HAL_OK) {
        Error_Handler();
    }
}

void HAL_I2C_MspInit(I2C_HandleTypeDef *i2cHandle)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    if (i2cHandle->Instance != I2C1) {
        return;
    }

    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_I2C1_CLK_ENABLE();

    GPIO_InitStruct.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF4_I2C1;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

    HAL_NVIC_SetPriority(I2C1_EV_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(I2C1_EV_IRQn);
    HAL_NVIC_SetPriority(I2C1_ER_IRQn, 0, 0);
    HAL_NVIC_EnableIRQ(I2C1_ER_IRQn);
}

void HAL_UART_MspInit(UART_HandleTypeDef *uartHandle)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};

    if (uartHandle->Instance != USART3) {
        return;
    }

    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_USART3_CLK_ENABLE();

    GPIO_InitStruct.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF7_USART3;
    HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

    HAL_NVIC_SetPriority(USART3_IRQn, 1, 0);
    HAL_NVIC_EnableIRQ(USART3_IRQn);
}

static HAL_StatusTypeDef mlx_read_word(uint8_t command, uint16_t *word, uint8_t *pec)
{
    uint8_t raw[3] = {0};
    HAL_StatusTypeDef status = HAL_I2C_Mem_Read(
        &hi2c1,
        MLX90614_ADDR << 1,
        command,
        I2C_MEMADD_SIZE_8BIT,
        raw,
        sizeof(raw),
        50U);
    if (status == HAL_OK) {
        *word = ((uint16_t)raw[1] << 8) | raw[0];
        *pec = raw[2];
    }
    return status;
}

static int32_t mlx_word_to_centideg(uint16_t word)
{
    return ((int32_t)word * 2) - 27315;
}

static void format_centideg(char *buffer, size_t buffer_size, int32_t centideg)
{
    if (buffer == NULL || buffer_size == 0U) {
        return;
    }
    const char *sign = "";
    uint32_t magnitude = (uint32_t)centideg;
    if (centideg < 0) {
        sign = "-";
        magnitude = (uint32_t)(-centideg);
    }
    (void)snprintf(
        buffer,
        buffer_size,
        "%s%lu.%02lu",
        sign,
        (unsigned long)(magnitude / 100U),
        (unsigned long)(magnitude % 100U));
}

static void handle_uart_commands(void)
{
    uint8_t byte = 0;
    while (HAL_UART_Receive(&huart3, &byte, 1U, 0U) == HAL_OK) {
        switch (byte) {
        case '1':
            sample_interval_ms = 1000U;
            uart_write_text("MLX90614_INTERVAL_MS,1000\r\n");
            break;
        case '2':
            sample_interval_ms = 200U;
            uart_write_text("MLX90614_INTERVAL_MS,200\r\n");
            break;
        case '3':
            sample_interval_ms = 100U;
            uart_write_text("MLX90614_INTERVAL_MS,100\r\n");
            break;
        case '4':
            sample_interval_ms = 50U;
            uart_write_text("MLX90614_INTERVAL_MS,50\r\n");
            break;
        case '5':
            sample_interval_ms = 20U;
            uart_write_text("MLX90614_INTERVAL_MS,20\r\n");
            break;
        case '6':
            sample_interval_ms = 10U;
            uart_write_text("MLX90614_INTERVAL_MS,10\r\n");
            break;
        case '7':
            sample_interval_ms = 0U;
            uart_write_text("MLX90614_INTERVAL_MS,0\r\n");
            break;
        default:
            break;
        }
    }
}

static void send_sample(void)
{
    uint16_t raw_ta = 0;
    uint16_t raw_to = 0;
    uint8_t pec_ta = 0;
    uint8_t pec_to = 0;
    uint8_t flags = MLX_FLAG_PEC_PRESENT;
    const uint32_t start_cycles = DWT->CYCCNT;
    HAL_StatusTypeDef status_ta = mlx_read_word(MLX90614_RAM_TA, &raw_ta, &pec_ta);
    HAL_StatusTypeDef status_to = mlx_read_word(MLX90614_RAM_TOBJ1, &raw_to, &pec_to);
    const uint32_t read_us = elapsed_us_since(start_cycles);

    if (status_ta != HAL_OK || status_to != HAL_OK || (raw_ta & 0x8000U) || (raw_to & 0x8000U)) {
        flags |= MLX_FLAG_READ_ERROR;
    }

    char ta_text[16];
    char to_text[16];
    format_centideg(ta_text, sizeof(ta_text), mlx_word_to_centideg(raw_ta));
    format_centideg(to_text, sizeof(to_text), mlx_word_to_centideg(raw_to));
    char line[160];
    (void)snprintf(
        line,
        sizeof(line),
        "MLX90614,%lu,%lu,%lu,%s,%s,%u,%u,%u\r\n",
        (unsigned long)sequence_number++,
        (unsigned long)HAL_GetTick(),
        (unsigned long)read_us,
        ta_text,
        to_text,
        (unsigned int)raw_ta,
        (unsigned int)raw_to,
        (unsigned int)flags);
    uart_write_text(line);
}

static void mlx_i2c_bus_recover(void)
{
    GPIO_InitTypeDef GPIO_InitStruct = {0};
    __HAL_RCC_GPIOB_CLK_ENABLE();

    GPIO_InitStruct.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_OD;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8 | GPIO_PIN_9, GPIO_PIN_SET);
    HAL_Delay(2U);
    for (uint8_t i = 0; i < 9U; ++i) {
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_RESET);
        HAL_Delay(1U);
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_SET);
        HAL_Delay(1U);
    }
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_RESET);
    HAL_Delay(1U);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_SET);
    HAL_Delay(1U);
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, GPIO_PIN_SET);
    HAL_Delay(2U);
}

static void uart_write_text(const char *text)
{
    if (text == NULL) {
        return;
    }
    const size_t len = strlen(text);
    if (len == 0U) {
        return;
    }
    (void)HAL_UART_Transmit(&huart3, (uint8_t *)text, (uint16_t)len, 100U);
}

static uint32_t elapsed_us_since(uint32_t start_cycles)
{
    const uint32_t cycles = DWT->CYCCNT - start_cycles;
    return cycles / (HAL_RCC_GetHCLKFreq() / 1000000U);
}

static void dwt_init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

void Error_Handler(void)
{
    while (1) {
        if (huart3.Instance == USART3) {
            static const uint8_t error_text[] = "MLX90614_ERROR_HANDLER\r\n";
            (void)HAL_UART_Transmit(&huart3, (uint8_t *)error_text, (uint16_t)(sizeof(error_text) - 1U), 100U);
        }
        HAL_GPIO_TogglePin(GPIOB, GPIO_PIN_0);
        for (volatile uint32_t i = 0; i < 2400000U; ++i) {
        }
    }
}
