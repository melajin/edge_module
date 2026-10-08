#pragma once
#include <cstddef>
struct Preferences {
    bool begin(const char *, bool) { return false; }
    size_t putBytes(const char *, const void *, size_t) { return 0; }
    size_t getBytes(const char *, void *, size_t) { return 0; }
    size_t getBytesLength(const char *) { return 0; }
    void end() {}
};
