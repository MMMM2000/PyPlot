#include <Adafruit_MLX90640.h>
#include <Wire.h>

Adafruit_MLX90640 mlx;
float frame[32 * 24];

constexpr uint32_t SERIAL_BAUD = 921600;
#ifndef MLX_I2C_CLOCK
#define MLX_I2C_CLOCK 400000
#endif
#ifndef MLX_MODE
#define MLX_MODE MLX90640_CHESS
#endif
#ifndef MLX_RESOLUTION
#define MLX_RESOLUTION MLX90640_ADC_18BIT
#endif
#ifndef MLX_REFRESH_RATE
#define MLX_REFRESH_RATE MLX90640_4_HZ
#endif

constexpr uint8_t FRAME_WIDTH = 32;
constexpr uint8_t FRAME_HEIGHT = 24;
constexpr uint16_t FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT;
constexpr uint16_t HEADER_SIZE = 18;
constexpr uint16_t PACKET_SIZE = HEADER_SIZE + FRAME_PIXELS * 2 + 2;
constexpr uint8_t VERSION = 1;

uint8_t packet[PACKET_SIZE];
uint32_t sequenceNumber = 0;

int16_t centiDegrees(float value) {
  if (value > 327.67f) return 32767;
  if (value < -327.68f) return -32768;
  return static_cast<int16_t>(lroundf(value * 100.0f));
}

void putU8(uint16_t &offset, uint8_t value) {
  packet[offset++] = value;
}

void putU16(uint16_t &offset, uint16_t value) {
  packet[offset++] = static_cast<uint8_t>(value & 0xFF);
  packet[offset++] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

void putI16(uint16_t &offset, int16_t value) {
  putU16(offset, static_cast<uint16_t>(value));
}

void putU32(uint16_t &offset, uint32_t value) {
  packet[offset++] = static_cast<uint8_t>(value & 0xFF);
  packet[offset++] = static_cast<uint8_t>((value >> 8) & 0xFF);
  packet[offset++] = static_cast<uint8_t>((value >> 16) & 0xFF);
  packet[offset++] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

void sendFrame() {
  uint16_t offset = 0;
  putU8(offset, 'M');
  putU8(offset, 'L');
  putU8(offset, 'X');
  putU8(offset, '4');
  putU8(offset, VERSION);
  putU8(offset, FRAME_WIDTH);
  putU8(offset, FRAME_HEIGHT);
  putU8(offset, 0);
  putU32(offset, sequenceNumber++);
  putU32(offset, millis());
  putI16(offset, centiDegrees(mlx.getTa(false)));

  for (uint16_t i = 0; i < FRAME_PIXELS; ++i) {
    putI16(offset, centiDegrees(frame[i]));
  }

  uint16_t checksum = 0;
  for (uint16_t i = 0; i < offset; ++i) {
    checksum = static_cast<uint16_t>(checksum + packet[i]);
  }
  putU16(offset, checksum);

  Serial.write(packet, offset);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);
  Serial.println("MLX90640_FAST_BOOT");

  Wire.setSDA(PB9);  // Nucleo D14, I2C1 SDA
  Wire.setSCL(PB8);  // Nucleo D15, I2C1 SCL
  Wire.begin();
  Wire.setClock(MLX_I2C_CLOCK);

  if (!mlx.begin(MLX90640_I2CADDR_DEFAULT, &Wire)) {
    Serial.println("MLX90640_FAST_ERROR_NOT_FOUND");
    while (true) {
      delay(1000);
    }
  }
  Serial.println("MLX90640_FAST_STREAM_BEGIN");

  mlx.setMode(MLX_MODE);
  mlx.setResolution(MLX_RESOLUTION);
  mlx.setRefreshRate(MLX_REFRESH_RATE);
}

void loop() {
  if (mlx.getFrame(frame) == 0) {
    sendFrame();
  }
}
