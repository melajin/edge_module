/* Host tests execute actual command, learning and storage code. NVS faults
 * are in-memory injections, not ESP32 power-loss or hardware validation. */
#include <cassert>
#include <iostream>
#include <nvs.h>
#include "../src/main.cpp"
TestSerial Serial;
TestWire Wire;
uint64_t test_clock = 1000000;
multi_heap_info_t test_heap;
unsigned test_heap_queries = 0;
std::map<std::string, std::vector<unsigned char>> Preferences::data;
std::map<std::string, int> Preferences::query_errors, Preferences::read_errors;
bool Preferences::fail_open_read = false, Preferences::fail_open_write = false;
bool Preferences::fail_write = false, Preferences::corrupt_readback = false, Preferences::fail_read = false;
unsigned Preferences::writes = 0, Preferences::legacy_writes = 0;
static const char *SESSION = "0123456789abcdef";
static const float VALUES[3] = {0.1f, 0.2f, 0.3f};

static void command(const char *s) { process_command(s); Serial.output.clear(); }
static ProfileStore blob(const char *key)
{
    ProfileStore s = {};
    const auto &v = Preferences::data.at(key);
    assert(v.size() == sizeof(s)); memcpy(&s, v.data(), sizeof(s)); return s;
}
static void store(const char *key, ProfileStore s, bool recalc = true)
{
    if (recalc) s.crc = profile_crc32((const uint8_t *)&s, offsetof(ProfileStore, crc));
    const auto *p = (const unsigned char *)&s;
    Preferences::data[key] = std::vector<unsigned char>(p, p + sizeof(s));
}
static void fresh()
{
    Preferences::reset(); profiles = ProfileManager{}; profiles.load();
    learning = false; learn_count = 0; paused = true;
    strcpy(acquisition_session, SESSION); sync_profile();
    command("context bench_1"); command("ppr 2");
    assert(!baseline_ready && profiles.state.ppr == 2);
}
static void approved()
{
    fresh(); assert(profiles.candidate(VALUES, SESSION, 4));
    command("approve 1"); assert(baseline_ready);
}
int main()
{
    fresh(); command("learn"); assert(learning && !paused);
    v3_signal_result_t signal = {};
    signal.amplitude_1x_g[0] = VALUES[0]; signal.amplitude_1x_g[1] = VALUES[1]; signal.amplitude_1x_g[2] = VALUES[2];
    for (unsigned i = 0; i < 335; ++i) {
        signal.amplitude_1x_g[1] = i < 168 ? 0.1f : 0.3f;
        update_learning(&signal);
    }
    assert(learning && learn_count == 335 && !profiles.state.candidate.id);
    update_learning(&signal);
    assert(!learning && learn_count == 0 && !baseline_ready && profiles.state.candidate.id == 1);
    assert(fabs(profiles.state.candidate.amplitude_g[1] - 0.2f) < 1e-6);
    assert(Serial.output.find("\"reason\":\"candidate_saved\"") != std::string::npos);
    command("learn"); assert(!learning && profiles.state.candidate.id == 1);
    command("approve 2"); assert(!baseline_ready && !profiles.state.current.id);
    command("context other"); command("approve 1"); assert(!baseline_ready);
    command("context bench_1"); command("ppr 3"); command("approve 1"); assert(!baseline_ready);
    command("ppr 2"); command("approve 1"); assert(baseline_ready && !profiles.state.candidate.id);
    command("learn"); update_learning(&signal); assert(profiles.state.current.id == 1);
    command("stop"); assert(!learning && learn_count == 0 && baseline_ready && paused);
    command("learn"); update_learning(&signal); abort_learning(); assert(!learning && baseline_ready);
    command("learn"); update_learning(&signal); command("context changed");
    assert(!learning && !baseline_ready && profiles.state.current.id == 1);
    command("context bench_1"); assert(baseline_ready);
    command("learn"); update_learning(&signal); command("profile"); assert(!learning && !learn_count);
    assert(profiles.candidate(VALUES, SESSION, 7));
    ProfileManager reboot; reboot.load();
    assert(reboot.ready() && reboot.state.current.id == 1 && reboot.state.candidate.id == 2);
    command("discard"); assert(baseline_ready && profiles.state.current.id == 1);
    assert(profiles.candidate(VALUES, SESSION, 9)); command("approve 3");
    assert(profiles.state.current.id == 3 && profiles.state.previous.id == 1);
    command("context other"); command("rollback"); assert(profiles.state.current.id == 3);
    command("context bench_1"); command("rollback");
    assert(profiles.state.current.id == 1 && profiles.state.previous.id == 3);
    command("rollback"); assert(profiles.state.current.id == 3);
    const auto history = acquisition_history.next_seq;
    have_previous_emit = true; previous_emit_seq = 123; previous_emit_call_us = 456;
    command("context next"); assert(acquisition_history.next_seq == history && previous_emit_seq == 123 && have_previous_emit);
    // Long commands cannot be truncated into a valid suffix command.
    Serial.input = std::string(80, 'x') + "clear\n"; read_commands();
    assert(profiles.state.current.id == 3 && Serial.output.find("command_too_long") != std::string::npos);
    command("ppr +2"); assert(ppr == 2);
    command("ppr 42949672960"); assert(ppr == 2);
    command("context bad space"); assert(strcmp(profiles.state.context_id, "next") == 0);

    // A torn write/readback/open error preserves the whole old RAM state and
    // stops acquisition. Recovery keeps its IDs and data; no auto-approval.
    for (unsigned fault = 0; fault < 3; ++fault) {
        approved(); const ProfileStore old = profiles.state;
        Preferences::fail_write = fault == 0;
        Preferences::corrupt_readback = fault == 1;
        Preferences::fail_open_write = fault == 2;
        command("clear"); assert(profiles.hold && paused && !baseline_ready);
        assert(memcmp(&old, &profiles.state, sizeof(old)) == 0);
        command("start"); assert(paused);
        Preferences::fail_write = Preferences::corrupt_readback = Preferences::fail_open_write = false;
        command("recover"); assert(!profiles.hold && paused && baseline_ready && profiles.state.current.id == 1);
        ProfileManager verified; verified.load(); assert(!verified.hold && verified.ready());
    }
    // Independently reject CRC/size/version/truncated read and preserve the
    // other valid slot. Equal generations with distinct valid bytes hold.
    for (unsigned fault = 0; fault < 4; ++fault) {
        approved(); command("ppr 3");
        auto a = blob("edgev3p2/slot_a"); auto b = blob("edgev3p2/slot_b");
        const char *newest = a.generation > b.generation ? "edgev3p2/slot_a" : "edgev3p2/slot_b";
        if (fault == 0) Preferences::data[newest][0] ^= 1;
        if (fault == 1) Preferences::data[newest].resize(3);
        if (fault == 2) { auto s = blob(newest); s.version = 99; store(newest, s); }
        if (fault == 3) { auto s = blob(newest); s.crc ^= 1; store(newest, s, false); }
        ProfileManager broken; broken.load();
        assert(broken.hold && broken.state.current.id == 1 && !broken.ready());
        assert(broken.recover()); ProfileManager fixed; fixed.load(); assert(!fixed.hold);
    }
    approved();
    auto conflicting = profiles.state; conflicting.ppr = 3;
    store("edgev3p2/slot_a", profiles.state); store("edgev3p2/slot_b", conflicting);
    ProfileManager conflict; conflict.load(); assert(conflict.hold && strcmp(conflict.reason, "generation_conflict") == 0);
    assert(conflict.recover());
    approved(); Preferences::fail_read = true;
    ProfileManager unread; unread.load(); assert(unread.hold);
    assert(!unread.recover()); Preferences::fail_read = false;
    assert(unread.recover() && unread.ready()); // reread required, no empty overwrite
    // Preferences::getBytesLength would return zero for these errors. Direct
    // NVS status must distinguish one/both slot failures from actual absence.
    for (unsigned stage = 0; stage < 2; ++stage) {
        for (unsigned failure = 0; failure < 2; ++failure) {
            for (unsigned affected = 0; affected < 3; ++affected) {
                approved();
                const auto persisted = Preferences::data;
                auto &faults = stage ? Preferences::read_errors : Preferences::query_errors;
                const int error = failure ? ESP_FAIL : ESP_ERR_NVS_TYPE_MISMATCH;
                if (affected != 1) faults["edgev3p2/slot_a"] = error;
                if (affected != 0) faults["edgev3p2/slot_b"] = error;
                profiles.load(); sync_profile();
                assert(profiles.hold && paused && !baseline_ready);
                assert(strcmp(profiles.reason, stage ? "nvs_read" : "nvs_query") == 0);
                const auto preserved = profiles.state;
                command("context must_not_save"); command("ppr 3"); command("start");
                assert(paused && profiles.hold && !baseline_ready);
                assert(memcmp(&preserved, &profiles.state, sizeof(preserved)) == 0);
                assert(Preferences::data == persisted);
                if (affected == 2) {
                    assert(!profiles.recover() && Preferences::data == persisted);
                } else {
                    const auto other = blob(affected == 0 ? "edgev3p2/slot_b" : "edgev3p2/slot_a");
                    assert(memcmp(&other, &profiles.state, sizeof(other)) == 0);
                }
                faults.clear(); assert(profiles.recover() && !profiles.hold);
            }
        }
    }
    approved(); const auto before_query_failure = profiles.state;
    Preferences::query_errors["edgev3p2/slot_a"] = ESP_ERR_NVS_TYPE_MISMATCH;
    command("clear");
    assert(profiles.hold && paused && memcmp(&before_query_failure, &profiles.state, sizeof(profiles.state)) == 0);
    Preferences::query_errors.clear(); command("recover"); assert(baseline_ready);
    // Namespace NOT_FOUND is the only safe first-use empty state. A present
    // namespace with two missing keys is also empty; genuine open errors hold.
    Preferences::reset(); ProfileManager missing; missing.load();
    assert(!missing.hold && strcmp(missing.reason, "not_found") == 0);
    Preferences::data["edgev3p2/unrelated"] = {1}; missing.load();
    assert(!missing.hold && strcmp(missing.reason, "not_found") == 0);
    // A key disappearing between size and payload reads remains an error.
    approved(); Preferences::read_errors["edgev3p2/slot_b"] = ESP_ERR_NVS_NOT_FOUND;
    ProfileManager disappearing; disappearing.load();
    assert(disappearing.hold && strcmp(disappearing.reason, "nvs_read") == 0);
    fresh(); Preferences::fail_open_read = true;
    ProfileManager opening; opening.load(); assert(opening.hold);
    assert(!opening.recover()); Preferences::fail_open_read = false;
    assert(opening.recover() && opening.state.ppr == 2);
    Preferences::reset(); Preferences::fail_open_read = true;
    ProfileManager virgin; virgin.load(); assert(virgin.hold);
    Preferences::fail_open_read = false;
    assert(virgin.recover() && !virgin.hold && !virgin.state.generation && !virgin.state.ppr);
    Preferences::data["edgev3p2/slot_a"] = {1,2,3};
    ProfileManager invalid; invalid.load(); assert(invalid.hold && !invalid.recover());
    assert(Preferences::data["edgev3p2/slot_a"].size() == 3);

    // Legacy data is read-only, only import creates a pending candidate.
    Preferences::reset(); DeviceProfile legacy = {};
    legacy.magic = 0x56334144u; legacy.schema = 1; legacy.ppr = 4; legacy.baseline_valid = 1;
    memcpy(legacy.baseline_g, VALUES, sizeof(VALUES));
    legacy.crc32 = profile_crc32((const uint8_t *)&legacy, offsetof(DeviceProfile, crc32));
    const auto *lp = (const unsigned char *)&legacy;
    Preferences::data["edgev3/profile"] = std::vector<unsigned char>(lp, lp + sizeof(legacy));
    const auto legacy_bytes = Preferences::data["edgev3/profile"];
    Preferences::query_errors["edgev3/profile"] = ESP_ERR_NVS_TYPE_MISMATCH;
    profiles.load(); assert(!profiles.legacy_valid && strcmp(profiles.legacy_reason, "legacy_query") == 0);
    assert(!profiles.import_legacy("legacy_A", SESSION, 0));
    assert(Preferences::data["edgev3/profile"] == legacy_bytes);
    Preferences::query_errors.clear();
    Preferences::read_errors["edgev3/profile"] = ESP_ERR_NVS_NOT_FOUND;
    profiles.load(); assert(!profiles.legacy_valid && strcmp(profiles.legacy_reason, "legacy_read") == 0);
    Preferences::read_errors.clear();
    profiles.load(); sync_profile(); assert(profiles.legacy_valid && !profiles.state.current.id);
    command("import legacy_A"); assert(!baseline_ready && profiles.state.candidate.origin == 2 && !profiles.state.candidate.learn_count);
    command("approve 1"); assert(baseline_ready && ppr == 4);
    command("clear"); assert(!profiles.state.current.id && ppr == 0);
    assert(Preferences::legacy_writes == 0 && Preferences::data["edgev3/profile"] == legacy_bytes);
    command("import legacy_A"); assert(profiles.state.candidate.id == 2); // cleared IDs not reused
    command("approve 1"); assert(!baseline_ready); command("approve 2"); assert(baseline_ready);
    command("learn"); update_learning(&signal);
    profiles.load(); learning = false; learn_count = 0; paused = true; sync_profile();
    assert(baseline_ready && !profiles.state.candidate.id && paused); // incomplete reboot learning is RAM-only
    auto exhausted = profiles.state; exhausted.generation = UINT32_MAX;
    store("edgev3p2/slot_a", exhausted); store("edgev3p2/slot_b", exhausted);
    profiles.load(); const ProfileStore old = profiles.state;
    assert(!profiles.select_context("changed") && strcmp(profiles.reason, "generation_exhausted") == 0);
    assert(memcmp(&old, &profiles.state, sizeof(old)) == 0);
    approved(); profiles.state.next_id = UINT32_MAX;
    assert(!profiles.candidate(VALUES, SESSION, 0) && strcmp(profiles.reason, "id_exhausted") == 0);
    // Actual detail events remain valid JSON with all three provenance slots.
    approved(); assert(profiles.candidate(VALUES, SESSION, UINT64_MAX));
    Serial.output.clear(); print_baseline(profiles.state.candidate);
    assert(Serial.output.find("18446744073709551615") != std::string::npos);
    assert(profiles.discard());
    assert(profiles.candidate(VALUES, SESSION, 5));
    assert(profiles.approve(3)); assert(profiles.candidate(VALUES, SESSION, 7));
    Serial.output.clear(); sync_profile(); print_profile("profile", true, "status");
    std::cout << Serial.output;
    std::cout << "PASS profile lifecycle, 335/336, command parsing, A/B injected faults, recovery, legacy read-only, overflow\n";
}
