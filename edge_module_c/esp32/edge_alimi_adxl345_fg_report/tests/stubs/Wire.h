#pragma once
#include <cstddef>
#include <cstdint>
struct TestWire {
    uint8_t reg = 0;
    bool wrote = false, fail = false;
    int remaining_samples = -1;
    void begin(int, int) {}
    void setClock(unsigned) {}
    void setTimeOut(unsigned) {}
    void beginTransmission(uint8_t) { wrote = false; }
    void write(uint8_t value) { if (!wrote) reg = value; wrote = true; }
    int endTransmission(bool = true) { return fail ? 1 : 0; }
    int requestFrom(int, int count, int) {
        if (reg == 0x32 && remaining_samples == 0) return 0;
        if (reg == 0x32 && remaining_samples > 0) --remaining_samples;
        return count;
    }
    int read() {
        if (reg == 0x30) { test_clock += 2500; return 0x80; }
        if (reg == 0x00) return 0xE5;
        if (reg == 0x2C) return 0x0C;
        if (reg == 0x31) return 0x09;
        return 0;
    }
};
extern TestWire Wire;
