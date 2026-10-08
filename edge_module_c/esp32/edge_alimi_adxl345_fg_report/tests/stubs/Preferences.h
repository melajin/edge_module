#pragma once
#include <cstddef>
#include <map>
#include <string>
#include <vector>
#include <cstring>
struct Preferences {
    static std::map<std::string, std::vector<unsigned char>> data;
    static std::map<std::string, int> query_errors, read_errors;
    static bool fail_open_read, fail_open_write, fail_write, corrupt_readback, fail_read;
    static unsigned writes, legacy_writes;
    std::string ns;
    bool readonly = true;
    bool begin(const char *name, bool read_only) {
        ns = name; readonly = read_only;
        return !(readonly ? fail_open_read : fail_open_write);
    }
    size_t putBytes(const char *key, const void *source, size_t size) {
        if (readonly) return 0;
        ++writes; if (ns == "edgev3") ++legacy_writes;
        const auto *p = static_cast<const unsigned char *>(source);
        data[ns + "/" + key] = std::vector<unsigned char>(p, p + (fail_write ? size / 2 : size));
        return fail_write ? size / 2 : size;
    }
    size_t getBytes(const char *key, void *target, size_t size) {
        if (fail_read) return 0;
        auto entry = data.find(ns + "/" + key);
        if (entry == data.end() || entry->second.size() > size) return 0;
        std::memcpy(target, entry->second.data(), entry->second.size());
        if (corrupt_readback && !readonly && size) static_cast<unsigned char *>(target)[0] ^= 1;
        return entry->second.size();
    }
    size_t getBytesLength(const char *key) {
        if (query_errors.count(ns + "/" + key)) return 0;
        auto entry = data.find(ns + "/" + key);
        return entry == data.end() ? 0 : entry->second.size();
    }
    void end() {}
    static void reset() {
        data.clear(); query_errors.clear(); read_errors.clear();
        fail_open_read = fail_open_write = fail_write = corrupt_readback = fail_read = false;
        writes = legacy_writes = 0;
    }
};
