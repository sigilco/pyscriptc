#include "mix.h"

/* Bit-at-a-time ISO-HDLC CRC-32. Slow on purpose, so a loop inside this
   .so is real native work rather than a handful of instructions. */

int64_t mix_crc32(const char *data, int64_t n) {
    uint32_t crc = 0xFFFFFFFFu;
    int64_t i;
    int bit;
    for (i = 0; i < n; i++) {
        crc ^= (unsigned char)data[i];
        for (bit = 0; bit < 8; bit++) {
            uint32_t mask = -(crc & 1u);
            crc = (crc >> 1) ^ (0xEDB88320u & mask);
        }
    }
    return (int64_t)(crc ^ 0xFFFFFFFFu);
}

int64_t mix_crc32_n(const char *data, int64_t n, int64_t times) {
    int64_t acc = 0;
    int64_t i;
    for (i = 0; i < times; i++) {
        acc += mix_crc32(data, n);
    }
    return acc;
}
