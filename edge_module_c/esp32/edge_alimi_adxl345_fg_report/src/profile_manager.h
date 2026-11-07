#pragma once
#include <stdint.h>
#include <stddef.h>

// Explicitly packed, versioned, little-endian ESP32 state. No pointer is stored.
struct __attribute__((packed)) BaselineProfile {
    uint32_t id;
    char context_id[13];
    uint8_t ppr;
    float amplitude_g[3];
    uint16_t learn_count;
    uint8_t origin; // 1 learned, 2 legacy_import
    char session[17];
    uint64_t seq;
};
struct __attribute__((packed)) ProfileStore {
    uint32_t magic;
    uint16_t version, size;
    uint32_t generation, next_id;
    char context_id[13];
    uint8_t ppr;
    BaselineProfile current, candidate, previous; // id=0 means absent
    uint32_t crc;
};
struct __attribute__((packed)) DeviceProfile {
    uint32_t magic;
    uint16_t schema;
    uint8_t ppr, baseline_valid;
    float baseline_g[3];
    uint32_t crc32;
};
uint32_t profile_crc32(const uint8_t *, size_t);
bool profile_context_valid(const char *);
class ProfileManager {
public:
    ProfileStore state = {};
    DeviceProfile legacy = {};
    bool hold = false, legacy_valid = false;
    const char *reason = "not_loaded", *legacy_reason = "not_loaded";
    void load();
    bool ready() const;
    bool select_context(const char *);
    bool select_ppr(unsigned);
    bool candidate(const float values[3], const char *session, uint64_t seq);
    bool approve(uint32_t);
    bool discard();
    bool rollback();
    bool import_legacy(const char *, const char *, uint64_t);
    bool clear();
    bool recover();
private:
    int active_slot = -1;
    uint32_t highest_generation = 0;
    bool commit(ProfileStore);
    bool write_slot(int, const ProfileStore &);
    bool permitted();
    bool add_candidate(ProfileStore, const float *, unsigned, uint8_t,
                       const char *, uint64_t);
};
