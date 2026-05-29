#define private public
#include <Adafruit_MLX90640.h>
#undef private
#include <Wire.h>

Adafruit_MLX90640 mlx;

constexpr uint32_t SERIAL_BAUD = 921600;
#ifndef MLX_I2C_CLOCK
#define MLX_I2C_CLOCK 1000000
#endif
#ifndef MLX_REFRESH_RATE
#define MLX_REFRESH_RATE MLX90640_16_HZ
#endif
#ifndef MLX_RESOLUTION
#define MLX_RESOLUTION MLX90640_ADC_16BIT
#endif
#ifndef MLX_READ_CHUNK_WORDS
#define MLX_READ_CHUNK_WORDS 32
#endif
constexpr uint32_t I2C_CLOCK = MLX_I2C_CLOCK;
constexpr uint8_t FRAME_WIDTH = 32;
constexpr uint8_t FRAME_HEIGHT = 24;
constexpr uint16_t FRAME_PIXELS = FRAME_WIDTH * FRAME_HEIGHT;
constexpr uint16_t HEADER_SIZE = 18;
constexpr uint8_t VERSION = 2;

uint16_t rawFrame[834];
float thermalFrame[FRAME_PIXELS];
uint8_t packet[HEADER_SIZE + FRAME_PIXELS * 2 + 2];
uint32_t sequenceNumber = 0;
uint32_t readOk = 0;
uint32_t readErrors = 0;

int fastReadWords(uint16_t startAddress, uint16_t wordCount, uint16_t *data) {
  while (wordCount > 0) {
    const uint16_t wordsThisRead = min<uint16_t>(wordCount, MLX_READ_CHUNK_WORDS);
    const uint8_t bytesThisRead = static_cast<uint8_t>(wordsThisRead * 2);
    const uint8_t read = Wire.requestFrom(
        static_cast<uint8_t>(MLX90640_I2CADDR_DEFAULT),
        bytesThisRead,
        static_cast<uint32_t>(startAddress),
        static_cast<uint8_t>(2),
        static_cast<uint8_t>(true));
    if (read != bytesThisRead) {
      return -1;
    }
    for (uint16_t i = 0; i < wordsThisRead; ++i) {
      const uint8_t high = Wire.read();
      const uint8_t low = Wire.read();
      data[i] = static_cast<uint16_t>((high << 8) | low);
    }
    data += wordsThisRead;
    startAddress += wordsThisRead;
    wordCount -= wordsThisRead;
  }
  return 0;
}

int fastWriteWord(uint16_t writeAddress, uint16_t data) {
  uint8_t payload[4] = {
      static_cast<uint8_t>(writeAddress >> 8),
      static_cast<uint8_t>(writeAddress & 0xFF),
      static_cast<uint8_t>(data >> 8),
      static_cast<uint8_t>(data & 0xFF),
  };
  if (i2c_master_write(&Wire._i2c, MLX90640_I2CADDR_DEFAULT << 1, payload, sizeof(payload)) != I2C_OK) {
    return -1;
  }

  uint16_t dataCheck = 0;
  if (fastReadWords(writeAddress, 1, &dataCheck) != 0) {
    return -1;
  }
  return dataCheck == data ? 0 : -2;
}

int fastWriteWordNoVerify(uint16_t writeAddress, uint16_t data) {
  uint8_t payload[4] = {
      static_cast<uint8_t>(writeAddress >> 8),
      static_cast<uint8_t>(writeAddress & 0xFF),
      static_cast<uint8_t>(data >> 8),
      static_cast<uint8_t>(data & 0xFF),
  };
  return i2c_master_write(&Wire._i2c, MLX90640_I2CADDR_DEFAULT << 1, payload, sizeof(payload)) == I2C_OK ? 0 : -1;
}

int fastGetFrameData(uint16_t *frameData) {
  uint16_t controlRegister1 = 0;
  uint16_t statusRegister = 0;
  uint16_t dataReady = 0;
  int error = 0;

  while (dataReady == 0) {
    error = fastReadWords(0x8000, 1, &statusRegister);
    if (error != 0) {
      return error;
    }
    dataReady = statusRegister & 0x0008;
  }

  error = fastWriteWordNoVerify(0x8000, 0x0030);
  if (error != 0) {
    return error;
  }

  error = fastReadWords(0x0400, 832, frameData);
  if (error != 0) {
    return error;
  }

  error = fastReadWords(0x8000, 1, &statusRegister);
  if (error != 0) {
    return error;
  }
  if ((statusRegister & 0x0008) != 0) {
    return -8;
  }

  error = fastReadWords(0x800D, 1, &controlRegister1);
  if (error != 0) {
    return error;
  }
  frameData[832] = controlRegister1;
  frameData[833] = statusRegister & 0x0001;
  return frameData[833];
}

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

void sendFrame(uint8_t flags, float ambient) {
  uint16_t offset = 0;
  putU8(offset, 'M');
  putU8(offset, 'L');
  putU8(offset, 'X');
  putU8(offset, '4');
  putU8(offset, VERSION);
  putU8(offset, FRAME_WIDTH);
  putU8(offset, FRAME_HEIGHT);
  putU8(offset, flags);
  putU32(offset, sequenceNumber++);
  putU32(offset, millis());
  putI16(offset, centiDegrees(ambient));

  for (uint16_t i = 0; i < FRAME_PIXELS; ++i) {
    putI16(offset, centiDegrees(thermalFrame[i]));
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
  Serial.println("MLX90640_SUBPAGE_BOOT");

  Wire.setSDA(PB9);
  Wire.setSCL(PB8);
  Wire.begin();
  Wire.setClock(I2C_CLOCK);

  if (!mlx.begin(MLX90640_I2CADDR_DEFAULT, &Wire)) {
    Serial.println("MLX90640_SUBPAGE_ERROR_NOT_FOUND");
    while (true) {
      delay(1000);
    }
  }

  mlx.setMode(MLX90640_CHESS);
  mlx.setResolution(MLX_RESOLUTION);
  mlx.setRefreshRate(MLX_REFRESH_RATE);
  Serial.println("MLX90640_SUBPAGE_STREAM_BEGIN");
}

void loop() {
  const int status = fastGetFrameData(rawFrame);
  if (status < 0) {
    ++readErrors;
    if ((readErrors % 100) == 1) {
      Serial.print("MLX90640_SUBPAGE_READ_ERROR,");
      Serial.println(status);
    }
    return;
  }

  ++readOk;
  const int subpage = mlx.MLX90640_GetSubPageNumber(rawFrame);
  const float ta = mlx.MLX90640_GetTa(rawFrame, &mlx._params);
  const float tr = ta - OPENAIR_TA_SHIFT;
  mlx.MLX90640_CalculateTo(rawFrame, &mlx._params, 0.95f, tr, thermalFrame);

  uint8_t flags = static_cast<uint8_t>(subpage & 0x01);
  if (subpage == 1) {
    flags |= 0x80;
  }
  sendFrame(flags, ta);
}
