#include "profile_manager.h"
#include <Preferences.h>
#include <nvs.h>
#include <string.h>
#include <math.h>
#include <limits.h>

static const uint32_t STORE_MAGIC = 0x32505645u;
static const char *STORE_NS = "edgev3p2";
static const char *KEYS[2] = {"slot_a", "slot_b"};

uint32_t profile_crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned b = 0; b < 8; ++b)
            crc = (crc >> 1) ^ (0xedb88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return ~crc;
}
bool profile_context_valid(const char *text)
{
    const size_t n = strlen(text);
    if (!n || n > 12) return false;
    for (size_t i = 0; i < n; ++i) {
        const char c = text[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-')) return false;
    }
    return true;
}
static bool amplitudes_valid(const float *v)
{
    for (unsigned i = 0; i < 3; ++i)
        if (!isfinite(v[i]) || v[i] < 0.001f || v[i] > 4.0f) return false;
    return true;
}
static bool baseline_valid(const BaselineProfile &b)
{
    if (!b.id) {
        const BaselineProfile empty = {};
        return memcmp(&b, &empty, sizeof(b)) == 0;
    }
    if (b.context_id[12] != 0 || !profile_context_valid(b.context_id) ||
        !b.ppr || b.ppr > 16 || b.session[16] != 0) return false;
    for (unsigned i = 0; i < 16; ++i)
        if (!((b.session[i] >= '0' && b.session[i] <= '9') ||
              (b.session[i] >= 'a' && b.session[i] <= 'f'))) return false;
    float values[3];
    memcpy(values, b.amplitude_g, sizeof(values));
    return amplitudes_valid(values) && ((b.origin == 1 && b.learn_count == 336) ||
                                      (b.origin == 2 && b.learn_count == 0));
}
static const char *validate(const ProfileStore &s)
{
    if (s.magic != STORE_MAGIC) return "store_magic";
    if (s.version != 1) return "store_version";
    if (s.size != sizeof(s)) return "store_size";
    if (s.crc != profile_crc32((const uint8_t *)&s, offsetof(ProfileStore, crc)))
        return "store_crc";
    if (!s.generation || !s.next_id || s.context_id[12] != 0 ||
        (s.context_id[0] && !profile_context_valid(s.context_id)) || s.ppr > 16)
        return "store_values";
    if (!baseline_valid(s.current) || !baseline_valid(s.candidate) ||
        !baseline_valid(s.previous)) return "store_baseline";
    const uint32_t ids[3] = {s.current.id, s.candidate.id, s.previous.id};
    for (unsigned i = 0; i < 3; ++i) {
        if (ids[i] >= s.next_id) return "store_ids";
        for (unsigned j = i + 1; j < 3; ++j)
            if (ids[i] && ids[i] == ids[j]) return "store_ids";
    }
    return nullptr;
}
static void seal(ProfileStore &s, uint32_t generation)
{
    s.magic = STORE_MAGIC; s.version = 1; s.size = sizeof(s);
    s.generation = generation;
    s.crc = profile_crc32((const uint8_t *)&s, offsetof(ProfileStore, crc));
}
// Preferences::getBytesLength collapses NOT_FOUND, type errors and read
// failures to zero. Only the direct API's NOT_FOUND authorizes empty state.
static esp_err_t read_blob(nvs_handle_t handle, const char *key, void *dest,
                          size_t expected, const char **reason,
                          const char *query_reason, const char *size_reason,
                          const char *read_reason)
{
    size_t size = 0;
    esp_err_t status = nvs_get_blob(handle, key, nullptr, &size);
    if (status == ESP_ERR_NVS_NOT_FOUND) return status;
    if (status != ESP_OK) { *reason = query_reason; return status; }
    if (size != expected) { *reason = size_reason; return ESP_ERR_NVS_INVALID_LENGTH; }
    status = nvs_get_blob(handle, key, dest, &size);
    if (status != ESP_OK || size != expected) {
        *reason = read_reason;
        // A key disappearing after its successful size query is a failed
        // read, never an independently confirmed absence.
        return status == ESP_OK || status == ESP_ERR_NVS_NOT_FOUND ? ESP_FAIL : status;
    }
    return ESP_OK;
}
static void load_legacy(ProfileManager &m)
{
    m.legacy_valid = false;
    nvs_handle_t handle;
    const esp_err_t opened = nvs_open("edgev3", NVS_READONLY, &handle);
    if (opened == ESP_ERR_NVS_NOT_FOUND) { m.legacy_reason = "legacy_missing"; return; }
    if (opened != ESP_OK) { m.legacy_reason = "legacy_open"; return; }
    DeviceProfile b = {};
    const esp_err_t read = read_blob(handle, "profile", &b, sizeof(b), &m.legacy_reason,
                                    "legacy_query", "legacy_size", "legacy_read");
    nvs_close(handle);
    if (read == ESP_ERR_NVS_NOT_FOUND) { m.legacy_reason = "legacy_missing"; return; }
    if (read != ESP_OK) return;
    if (b.magic != 0x56334144u || b.schema != 1 || b.baseline_valid != 1 ||
        !b.ppr || b.ppr > 16 || b.crc32 != profile_crc32((const uint8_t *)&b,
        offsetof(DeviceProfile, crc32))) { m.legacy_reason = "legacy_invalid"; return; }
    float values[3]; memcpy(values, b.baseline_g, sizeof(values));
    if (!amplitudes_valid(values)) { m.legacy_reason = "legacy_range"; return; }
    m.legacy = b; m.legacy_valid = true; m.legacy_reason = "legacy_valid";
}
void ProfileManager::load()
{
    state = ProfileStore{}; state.next_id = 1;
    hold = false; active_slot = -1; highest_generation = 0;
    load_legacy(*this);
    nvs_handle_t handle;
    const esp_err_t opened = nvs_open(STORE_NS, NVS_READONLY, &handle);
    if (opened == ESP_ERR_NVS_NOT_FOUND) { reason = "not_found"; return; }
    if (opened != ESP_OK) { hold = true; reason = "nvs_open_read"; return; }
    ProfileStore slots[2] = {};
    bool valid[2] = {false, false}, present[2] = {false, false};
    const char *errors[2] = {nullptr, nullptr};
    for (int i = 0; i < 2; ++i) {
        const esp_err_t read = read_blob(handle, KEYS[i], &slots[i], sizeof(ProfileStore),
                                        &errors[i], "nvs_query", "store_size", "nvs_read");
        present[i] = read != ESP_ERR_NVS_NOT_FOUND;
        if (!present[i]) continue;
        if (read == ESP_OK) errors[i] = validate(slots[i]);
        valid[i] = errors[i] == nullptr;
        if (valid[i] && slots[i].generation > highest_generation)
            highest_generation = slots[i].generation;
    }
    nvs_close(handle);
    if (valid[0]) active_slot = 0;
    if (valid[1] && (active_slot < 0 || slots[1].generation > slots[0].generation)) active_slot = 1;
    if (active_slot >= 0) state = slots[active_slot];
    if ((present[0] && !valid[0]) || (present[1] && !valid[1])) {
        hold = true; reason = errors[0] ? errors[0] : errors[1]; return;
    }
    if (valid[0] && valid[1] && slots[0].generation == slots[1].generation &&
        memcmp(&slots[0], &slots[1], sizeof(ProfileStore))) {
        hold = true; reason = "generation_conflict"; return;
    }
    reason = active_slot < 0 ? "not_found" : "loaded";
}
bool ProfileManager::ready() const
{
    return !hold && state.current.id && state.ppr == state.current.ppr &&
           strcmp(state.context_id, state.current.context_id) == 0;
}
bool ProfileManager::permitted()
{
    if (hold) { reason = "storage_hold"; return false; }
    return true;
}
bool ProfileManager::write_slot(int slot, const ProfileStore &s)
{
    Preferences nvs;
    if (!nvs.begin(STORE_NS, false)) { reason = "nvs_open_write"; return false; }
    const size_t count = nvs.putBytes(KEYS[slot], &s, sizeof(s));
    nvs.end();
    ProfileStore readback = {};
    nvs_handle_t handle;
    if (nvs_open(STORE_NS, NVS_READONLY, &handle) != ESP_OK) {
        reason = "nvs_write_uncertain"; return false;
    }
    const char *failure = nullptr;
    const esp_err_t read = read_blob(handle, KEYS[slot], &readback, sizeof(readback), &failure,
                                    "nvs_query", "store_size", "nvs_read");
    nvs_close(handle);
    if (count != sizeof(s) || read != ESP_OK ||
        validate(readback) || memcmp(&s, &readback, sizeof(s))) {
        reason = "nvs_write_uncertain"; return false;
    }
    return true;
}
bool ProfileManager::commit(ProfileStore proposed)
{
    if (!permitted()) return false;
    if (highest_generation == UINT32_MAX) { reason = "generation_exhausted"; return false; }
    seal(proposed, highest_generation + 1);
    if (validate(proposed)) { reason = "store_values"; return false; }
    const int target = active_slot == 0 ? 1 : 0;
    ++highest_generation; // a failed readback may still have persisted this generation
    if (!write_slot(target, proposed)) { hold = true; return false; }
    state = proposed; active_slot = target; reason = "saved"; return true;
}
bool ProfileManager::select_context(const char *context)
{
    if (!profile_context_valid(context)) { reason = "context_invalid"; return false; }
    ProfileStore s = state; memset(s.context_id, 0, sizeof(s.context_id));
    strcpy(s.context_id, context); return commit(s);
}
bool ProfileManager::select_ppr(unsigned ppr)
{
    if (!ppr || ppr > 16) { reason = "ppr_invalid"; return false; }
    ProfileStore s = state; s.ppr = (uint8_t)ppr; return commit(s);
}
bool ProfileManager::add_candidate(ProfileStore s, const float *values, unsigned count,
                                  uint8_t origin, const char *session, uint64_t seq)
{
    if (s.candidate.id) { reason = "candidate_exists"; return false; }
    if (!s.context_id[0] || !s.ppr) { reason = "selection_required"; return false; }
    if (!amplitudes_valid(values)) { reason = "baseline_range"; return false; }
    if (s.next_id == UINT32_MAX) { reason = "id_exhausted"; return false; }
    BaselineProfile b = {}; b.id = s.next_id++;
    strcpy(b.context_id, s.context_id); b.ppr = s.ppr;
    memcpy(b.amplitude_g, values, sizeof(b.amplitude_g));
    b.learn_count = (uint16_t)count; b.origin = origin;
    if (strlen(session) != 16) { reason = "session_invalid"; return false; }
    memcpy(b.session, session, 16); b.seq = seq; s.candidate = b;
    return commit(s);
}
bool ProfileManager::candidate(const float *values, const char *session, uint64_t seq)
{ return add_candidate(state, values, 336, 1, session, seq); }
static bool matches(const ProfileStore &s, const BaselineProfile &b)
{ return b.id && b.ppr == s.ppr && strcmp(b.context_id, s.context_id) == 0; }
bool ProfileManager::approve(uint32_t id)
{
    if (!state.candidate.id || state.candidate.id != id) { reason = "candidate_id_mismatch"; return false; }
    if (!matches(state, state.candidate)) { reason = "condition_mismatch"; return false; }
    ProfileStore s = state; s.previous = s.current; s.current = s.candidate;
    s.candidate = BaselineProfile{}; return commit(s);
}
bool ProfileManager::discard()
{
    if (!state.candidate.id) { reason = "candidate_missing"; return false; }
    ProfileStore s = state; s.candidate = BaselineProfile{}; return commit(s);
}
bool ProfileManager::rollback()
{
    if (!state.previous.id) { reason = "previous_missing"; return false; }
    if (!matches(state, state.previous)) { reason = "condition_mismatch"; return false; }
    ProfileStore s = state; s.current = state.previous; s.previous = state.current;
    return commit(s);
}
bool ProfileManager::import_legacy(const char *context, const char *session, uint64_t seq)
{
    if (!legacy_valid) { reason = "legacy_unavailable"; return false; }
    if (!profile_context_valid(context)) { reason = "context_invalid"; return false; }
    ProfileStore s = state; memset(s.context_id, 0, sizeof(s.context_id));
    strcpy(s.context_id, context); s.ppr = legacy.ppr;
    float values[3]; memcpy(values, legacy.baseline_g, sizeof(values));
    return add_candidate(s, values, 0, 2, session, seq);
}
bool ProfileManager::clear()
{
    ProfileStore s = state; s.context_id[0] = 0;
    memset(s.context_id, 0, sizeof(s.context_id)); s.ppr = 0;
    s.current = s.candidate = s.previous = BaselineProfile{};
    return commit(s); // next_id deliberately never reused
}
bool ProfileManager::recover()
{
    if (!hold) { reason = "recovery_not_required"; return false; }
    if (active_slot < 0) {
        // Retry the direct read; only explicitly confirmed NOT_FOUND permits
        // empty state. Never create or overwrite data to resolve read errors.
        load();
        if (!hold) { reason = "recovered"; return true; }
        if (active_slot < 0) { reason = "recovery_no_valid_state"; return false; }
    }
    if (highest_generation == UINT32_MAX) { reason = "generation_exhausted"; return false; }
    ProfileStore proposed = state; seal(proposed, ++highest_generation);
    const int first = active_slot == 0 ? 1 : 0;
    if (!write_slot(first, proposed) || !write_slot(1-first, proposed)) return false;
    state = proposed; active_slot = first; hold = false; reason = "recovered"; return true;
}
