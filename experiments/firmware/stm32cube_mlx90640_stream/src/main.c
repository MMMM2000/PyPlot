#include "main.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MLX90640_ADDR 0x33U
#define MLX90640_STATUS_REG 0x8000U
#define MLX90640_CONTROL_REG 0x800DU
#define MLX90640_FRAME_RAM 0x0400U
#define MLX90640_EEPROM 0x2400U
#define MLX90640_DEVICE_ID1 0x2407U
#define MLX90640_FRAME_WORDS 832U
#define MLX90640_PIXEL_WORDS 768U
#define MLX90640_AUX_WORDS 64U
#define MLX90640_ROW_WORDS 32U
#define MLX90640_ROWS 24U
#define MLX90640_READ_CHUNK_WORDS 120U
#define MLX90640_REFRESH_16_HZ 5U
#define MLX90640_ADC_17BIT 1U
#define MLX90640_STATUS_DATA_READY 0x0008U
#define MLX90640_STATUS_CLEAR 0x0030U
#define MLX90640_MODE_CHESS 0x1000U

#ifndef MLX_I2C_TIMING
#define MLX_I2C_TIMING 0x20D01132U
#endif

#ifndef MLX_UART_BAUD
#define MLX_UART_BAUD 2000000U
#endif

#ifndef MLX_REFRESH_RATE
#define MLX_REFRESH_RATE MLX90640_REFRESH_16_HZ
#endif

#ifndef MLX_ADC_RESOLUTION
#define MLX_ADC_RESOLUTION MLX90640_ADC_17BIT
#endif

#define RAW_PACKET_MAGIC_0 'M'
#define RAW_PACKET_MAGIC_1 'L'
#define RAW_PACKET_MAGIC_2 'X'
#define RAW_PACKET_MAGIC_3 'R'
#define EEPROM_PACKET_MAGIC_3 'E'
#define RAW_PACKET_VERSION 1U
#define RAW_HEADER_BYTES 28U
#define RAW_PAYLOAD_BYTES (MLX90640_FRAME_WORDS * 2U)
#define RAW_COMPACT_WORDS ((MLX90640_PIXEL_WORDS / 2U) + MLX90640_AUX_WORDS)
#define RAW_COMPACT_PAYLOAD_BYTES (RAW_COMPACT_WORDS * 2U)
#define RAW_PACKET_BYTES (RAW_HEADER_BYTES + RAW_PAYLOAD_BYTES + 2U)
#define RAW_FLAG_SUBPAGE_1 0x01U
#define RAW_FLAG_COMPACT 0x40U
#define RAW_FLAG_OVERRUN 0x80U

I2C_HandleTypeDef hi2c1;
UART_HandleTypeDef huart3;

static uint8_t frame_payload[RAW_PAYLOAD_BYTES];
static uint8_t packet[RAW_PACKET_BYTES];
static uint32_t sequence_number;

static void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_I2C1_Init(void);
static void MX_USART3_UART_Init(void);
static HAL_StatusTypeDef mlx_read_bytes(uint16_t start_address, uint16_t byte_count, uint8_t *data);
static HAL_StatusTypeDef mlx_read_words(uint16_t start_address, uint16_t word_count, uint8_t *data);
static HAL_StatusTypeDef mlx_read_word(uint16_t address, uint16_t *value);
static HAL_StatusTypeDef mlx_write_word(uint16_t address, uint16_t value);
static bool mlx_configure(void);
static bool mlx_set_refresh_rate(uint8_t refresh_rate);
static HAL_StatusTypeDef mlx_read_interleaved_subpage(uint8_t subpage);
static bool mlx_read_eeprom(void);
static bool mlx_read_frame(uint16_t *status, uint16_t *control, uint32_t *read_us);
static void handle_uart_commands(void);
static void send_raw_packet(uint16_t status, uint16_t control, uint32_t read_us);
static void send_eeprom_packet(uint32_t read_us);
static void uart_write(const void *data, uint16_t size);
static void uart_write_text(const char *text);
static void put_u8(uint16_t *offset, uint8_t value);
static void put_u16(uint16_t *offset, uint16_t value);
static void put_u32(uint16_t *offset, uint32_t value);
static uint32_t elapsed_us_since(uint32_t start_cycles);
static void dwt_init(void);

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    dwt_init();
    MX_GPIO_Init();
    MX_I2C1_Init();
    MX_USART3_UART_Init();

    uart_write_text("\r\nMLX90640_CUBE_RAW_BOOT\r\n");

    if (!mlx_configure()) {
        uart_write_text("MLX90640_CUBE_ERROR_NOT_FOUND_OR_CONFIG_FAILED\r\n");
        Error_Handler();
    }
    if (!mlx_read_eeprom()) {
        uart_write_text("MLX90640_CUBE_ERROR_EEPROM_READ_FAILED\r\n");
        Error_Handler();
    }
    send_eeprom_packet(0);

    uart_write_text("MLX90640_CUBE_RAW_STREAM_BEGIN\r\n");

    while (1) {
        uint16_t status = 0;
        uint16_t control = 0;
        uint32_t read_us = 0;
        handle_uart_commands();
        if (mlx_read_frame(&status, &control, &read_us)) {
            send_raw_packet(status, control, read_us);
            if ((sequence_number % 64U) == 0U && mlx_read_eeprom()) {
                send_eeprom_packet(0);
            }
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

    HAL_I2CEx_EnableFastModePlus(I2C_FASTMODEPLUS_I2C1);

    GPIO_InitStruct.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
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

static HAL_StatusTypeDef mlx_read_bytes(uint16_t start_address, uint16_t byte_count, uint8_t *data)
{
    return HAL_I2C_Mem_Read(
        &hi2c1,
        MLX90640_ADDR << 1,
        start_address,
        I2C_MEMADD_SIZE_16BIT,
        data,
        byte_count,
        100U);
}

static HAL_StatusTypeDef mlx_read_words(uint16_t start_address, uint16_t word_count, uint8_t *data)
{
    uint16_t words_remaining = word_count;
    uint16_t offset_words = 0;

    while (words_remaining > 0U) {
        uint16_t chunk_words = words_remaining;
        if (chunk_words > MLX90640_READ_CHUNK_WORDS) {
            chunk_words = MLX90640_READ_CHUNK_WORDS;
        }
        HAL_StatusTypeDef status = mlx_read_bytes(
            (uint16_t)(start_address + offset_words),
            (uint16_t)(chunk_words * 2U),
            &data[offset_words * 2U]);
        if (status != HAL_OK) {
            return status;
        }
        offset_words = (uint16_t)(offset_words + chunk_words);
        words_remaining = (uint16_t)(words_remaining - chunk_words);
    }
    return HAL_OK;
}

static HAL_StatusTypeDef mlx_read_word(uint16_t address, uint16_t *value)
{
    uint8_t raw[2] = {0};
    HAL_StatusTypeDef status = mlx_read_bytes(address, sizeof(raw), raw);
    if (status == HAL_OK) {
        *value = ((uint16_t)raw[0] << 8) | raw[1];
    }
    return status;
}

static HAL_StatusTypeDef mlx_write_word(uint16_t address, uint16_t value)
{
    uint8_t raw[2] = {
        (uint8_t)(value >> 8),
        (uint8_t)(value & 0xFFU),
    };
    return HAL_I2C_Mem_Write(
        &hi2c1,
        MLX90640_ADDR << 1,
        address,
        I2C_MEMADD_SIZE_16BIT,
        raw,
        sizeof(raw),
        100U);
}

static bool mlx_configure(void)
{
    uint16_t device_id = 0;

    if (HAL_I2C_IsDeviceReady(&hi2c1, MLX90640_ADDR << 1, 3, 100U) != HAL_OK) {
        return false;
    }
    if (mlx_read_word(MLX90640_DEVICE_ID1, &device_id) != HAL_OK) {
        return false;
    }
    if (device_id == 0x0000U || device_id == 0xFFFFU) {
        return false;
    }
    if (!mlx_set_refresh_rate(MLX_REFRESH_RATE & 0x07U)) {
        return false;
    }
    if (mlx_write_word(MLX90640_STATUS_REG, MLX90640_STATUS_CLEAR) != HAL_OK) {
        return false;
    }
    return true;
}

static bool mlx_set_refresh_rate(uint8_t refresh_rate)
{
    uint16_t control = 0;
    if (mlx_read_word(MLX90640_CONTROL_REG, &control) != HAL_OK) {
        return false;
    }

    control &= (uint16_t)~((0x03U << 10) | (0x07U << 7) | MLX90640_MODE_CHESS);
    control |= (uint16_t)(((MLX_ADC_RESOLUTION & 0x03U) << 10) | ((refresh_rate & 0x07U) << 7));

    if (mlx_write_word(MLX90640_CONTROL_REG, control) != HAL_OK) {
        return false;
    }
    if (mlx_write_word(MLX90640_STATUS_REG, MLX90640_STATUS_CLEAR) != HAL_OK) {
        return false;
    }
    return true;
}

static void handle_uart_commands(void)
{
    uint8_t byte = 0;
    if (__HAL_UART_GET_FLAG(&huart3, UART_FLAG_ORE) != RESET) {
        __HAL_UART_CLEAR_OREFLAG(&huart3);
    }
    while (__HAL_UART_GET_FLAG(&huart3, UART_FLAG_RXNE) != RESET) {
        byte = (uint8_t)(huart3.Instance->RDR & 0xFFU);
        if (byte >= '5' && byte <= '7') {
            (void)mlx_set_refresh_rate((uint8_t)(byte - '0'));
            sequence_number = 0;
            if (mlx_read_eeprom()) {
                send_eeprom_packet(0);
            }
        }
    }
}

static bool mlx_read_eeprom(void)
{
    uint32_t start_cycles = DWT->CYCCNT;
    if (mlx_read_words(MLX90640_EEPROM, MLX90640_FRAME_WORDS, frame_payload) != HAL_OK) {
        return false;
    }
    (void)elapsed_us_since(start_cycles);
    return true;
}

static HAL_StatusTypeDef mlx_read_interleaved_subpage(uint8_t subpage)
{
    for (uint16_t row = subpage; row < MLX90640_ROWS; row = (uint16_t)(row + 2U)) {
        uint16_t row_offset = (uint16_t)(row * MLX90640_ROW_WORDS);
        HAL_StatusTypeDef status = mlx_read_words(
            (uint16_t)(MLX90640_FRAME_RAM + row_offset),
            MLX90640_ROW_WORDS,
            &frame_payload[row_offset * 2U]);
        if (status != HAL_OK) {
            return status;
        }
    }

    return mlx_read_words(
        (uint16_t)(MLX90640_FRAME_RAM + MLX90640_PIXEL_WORDS),
        MLX90640_AUX_WORDS,
        &frame_payload[MLX90640_PIXEL_WORDS * 2U]);
}

static bool mlx_read_frame(uint16_t *status, uint16_t *control, uint32_t *read_us)
{
    uint16_t local_status = 0;
    uint32_t start_cycles = 0;
    uint8_t subpage = 0;

    if (mlx_read_word(MLX90640_STATUS_REG, &local_status) != HAL_OK) {
        return false;
    }
    if ((local_status & MLX90640_STATUS_DATA_READY) == 0U) {
        return false;
    }
    subpage = (uint8_t)(local_status & 0x0001U);

    if (mlx_write_word(MLX90640_STATUS_REG, MLX90640_STATUS_CLEAR) != HAL_OK) {
        return false;
    }

    start_cycles = DWT->CYCCNT;
    if (mlx_read_interleaved_subpage(subpage) != HAL_OK) {
        return false;
    }
    *read_us = elapsed_us_since(start_cycles);

    if (mlx_read_word(MLX90640_STATUS_REG, status) != HAL_OK) {
        return false;
    }
    if (mlx_read_word(MLX90640_CONTROL_REG, control) != HAL_OK) {
        return false;
    }

    *status = (uint16_t)((*status & 0xFFFEU) | (local_status & 0x0001U));
    return true;
}

static void send_eeprom_packet(uint32_t read_us)
{
    uint16_t offset = 0;
    uint16_t checksum = 0;

    put_u8(&offset, RAW_PACKET_MAGIC_0);
    put_u8(&offset, RAW_PACKET_MAGIC_1);
    put_u8(&offset, RAW_PACKET_MAGIC_2);
    put_u8(&offset, EEPROM_PACKET_MAGIC_3);
    put_u8(&offset, RAW_PACKET_VERSION);
    put_u8(&offset, 0);
    put_u16(&offset, MLX90640_FRAME_WORDS);
    put_u32(&offset, 0);
    put_u32(&offset, HAL_GetTick());
    put_u32(&offset, read_us);
    put_u16(&offset, 0);
    put_u16(&offset, 0);
    put_u32(&offset, RAW_PAYLOAD_BYTES);

    for (uint16_t i = 0; i < RAW_PAYLOAD_BYTES; ++i) {
        packet[offset++] = frame_payload[i];
    }

    for (uint16_t i = 0; i < offset; ++i) {
        checksum = (uint16_t)(checksum + packet[i]);
    }
    put_u16(&offset, checksum);
    uart_write(packet, offset);
}

static void send_raw_packet(uint16_t status, uint16_t control, uint32_t read_us)
{
    uint16_t offset = 0;
    uint16_t checksum = 0;
    uint8_t flags = 0;
    uint8_t subpage = (uint8_t)(status & 0x0001U);

    if (subpage != 0U) {
        flags |= RAW_FLAG_SUBPAGE_1;
    }
    flags |= RAW_FLAG_COMPACT;
    if ((status & MLX90640_STATUS_DATA_READY) != 0U) {
        flags |= RAW_FLAG_OVERRUN;
    }

    put_u8(&offset, RAW_PACKET_MAGIC_0);
    put_u8(&offset, RAW_PACKET_MAGIC_1);
    put_u8(&offset, RAW_PACKET_MAGIC_2);
    put_u8(&offset, RAW_PACKET_MAGIC_3);
    put_u8(&offset, RAW_PACKET_VERSION);
    put_u8(&offset, flags);
    put_u16(&offset, RAW_COMPACT_WORDS);
    put_u32(&offset, sequence_number++);
    put_u32(&offset, HAL_GetTick());
    put_u32(&offset, read_us);
    put_u16(&offset, status);
    put_u16(&offset, control);
    put_u32(&offset, RAW_COMPACT_PAYLOAD_BYTES);

    for (uint16_t row = subpage; row < MLX90640_ROWS; row = (uint16_t)(row + 2U)) {
        uint16_t row_offset = (uint16_t)(row * MLX90640_ROW_WORDS * 2U);
        for (uint16_t i = 0; i < MLX90640_ROW_WORDS * 2U; ++i) {
            packet[offset++] = frame_payload[row_offset + i];
        }
    }
    for (uint16_t i = 0; i < MLX90640_AUX_WORDS * 2U; ++i) {
        packet[offset++] = frame_payload[(MLX90640_PIXEL_WORDS * 2U) + i];
    }

    for (uint16_t i = 0; i < offset; ++i) {
        checksum = (uint16_t)(checksum + packet[i]);
    }
    put_u16(&offset, checksum);
    uart_write(packet, offset);
}

static void uart_write(const void *data, uint16_t size)
{
    (void)HAL_UART_Transmit(&huart3, (uint8_t *)data, size, 100U);
}

static void uart_write_text(const char *text)
{
    const char *cursor = text;
    while (*cursor != '\0') {
        ++cursor;
    }
    uart_write(text, (uint16_t)(cursor - text));
}

static void put_u8(uint16_t *offset, uint8_t value)
{
    packet[(*offset)++] = value;
}

static void put_u16(uint16_t *offset, uint16_t value)
{
    packet[(*offset)++] = (uint8_t)(value & 0xFFU);
    packet[(*offset)++] = (uint8_t)(value >> 8);
}

static void put_u32(uint16_t *offset, uint32_t value)
{
    packet[(*offset)++] = (uint8_t)(value & 0xFFU);
    packet[(*offset)++] = (uint8_t)((value >> 8) & 0xFFU);
    packet[(*offset)++] = (uint8_t)((value >> 16) & 0xFFU);
    packet[(*offset)++] = (uint8_t)((value >> 24) & 0xFFU);
}

static void dwt_init(void)
{
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
}

static uint32_t elapsed_us_since(uint32_t start_cycles)
{
    return (DWT->CYCCNT - start_cycles) / (SystemCoreClock / 1000000U);
}

void Error_Handler(void)
{
    __disable_irq();
    while (1) {
        HAL_GPIO_TogglePin(GPIOB, GPIO_PIN_0);
        for (volatile uint32_t i = 0; i < 2400000U; ++i) {
        }
    }
}
