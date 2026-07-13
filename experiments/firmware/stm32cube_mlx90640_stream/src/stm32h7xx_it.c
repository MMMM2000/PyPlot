#include "main.h"

extern I2C_HandleTypeDef hi2c1;
extern UART_HandleTypeDef huart3;

void NMI_Handler(void)
{
}

void HardFault_Handler(void)
{
    Error_Handler();
}

void MemManage_Handler(void)
{
    Error_Handler();
}

void BusFault_Handler(void)
{
    Error_Handler();
}

void UsageFault_Handler(void)
{
    Error_Handler();
}

void SysTick_Handler(void)
{
    HAL_IncTick();
}

void I2C1_EV_IRQHandler(void)
{
    HAL_I2C_EV_IRQHandler(&hi2c1);
}

void I2C1_ER_IRQHandler(void)
{
    HAL_I2C_ER_IRQHandler(&hi2c1);
}

void I2C2_EV_IRQHandler(void)
{
    HAL_I2C_EV_IRQHandler(&hi2c1);
}

void I2C2_ER_IRQHandler(void)
{
    HAL_I2C_ER_IRQHandler(&hi2c1);
}

void USART3_IRQHandler(void)
{
    HAL_UART_IRQHandler(&huart3);
}
