#pragma once
#include <cstdint>
#include <cstddef>
#include <cstdio>
#include <string>
#define IRAM_ATTR
#define INPUT 0
#define FALLING 0
#define portMUX_INITIALIZER_UNLOCKED 0
typedef int portMUX_TYPE;
#define portENTER_CRITICAL(x) ((void)(x))
#define portEXIT_CRITICAL(x) ((void)(x))
#define portENTER_CRITICAL_ISR(x) ((void)(x))
#define portEXIT_CRITICAL_ISR(x) ((void)(x))
struct TestSerial {
    std::string output;
    size_t tx_buffer = 0;
    bool begun = false, fail_config = false, fail_driver = false;
    size_t setTxBufferSize(size_t bytes) {
        if (begun) std::abort();
        tx_buffer = fail_config ? 0 : bytes;
        return tx_buffer;
    }
    void begin(unsigned) { begun = true; }
    explicit operator bool() const { return begun && !fail_driver; }
    int available() { return 0; }
    int read() { return -1; }
    void print(const char *s) { output += s; }
    void println(const char *s) { output += s; output += '\n'; }
    template<typename... Args> void printf(const char *format, Args... args) {
        char buffer[4096];
        const int n = std::snprintf(buffer, sizeof(buffer), format, args...);
        if (n < 0 || n >= (int)sizeof(buffer)) std::abort();
        output.append(buffer, (size_t)n);
    }
};
extern TestSerial Serial;
extern uint64_t test_clock;
inline uint32_t micros() { return (uint32_t)test_clock; }
inline void delay(unsigned n) { test_clock += n * 1000u; }
inline void delayMicroseconds(unsigned n) { test_clock += n; }
inline void pinMode(int, int) {}
inline int digitalPinToInterrupt(int n) { return n; }
inline void attachInterrupt(int, void (*)(), int) {}
