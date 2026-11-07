/* Host-only arithmetic and actual main.cpp serialization checks. Stubs do not
 * simulate sensor physics, UART timing, NVS durability or ESP32 scheduling. */
#include <cassert>
#include <cmath>
#include <iostream>
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

static AcquisitionQuality window(uint64_t start, unsigned n = V3_SAMPLE_COUNT,
                                  unsigned step = 2500, float value = 0.1f)
{
    AcquisitionQuality q;
    for (unsigned i = 0; i < n; ++i) {
        v3_sample_t sample = {(uint32_t)(start + i * step), {value, 0, 0}};
        acquisition_record(q, start + i * step, sample);
    }
    return q;
}

static void check(AcquisitionHistory &h, AcquisitionQuality q, const char *reason)
{
    acquisition_finish(h, q);
    assert(strcmp(q.reason, reason) == 0);
    assert(q.valid == (strcmp(reason, "ok") == 0));
}

static void emit()
{
    std::cout << Serial.output;
    Serial.output.clear();
}

int main()
{
    AcquisitionHistory h;
    check(h, window(1000, 0), "no_samples");
    assert(!h.have_end);
    check(h, window(1000, 3), "partial_window");
    check(h, window(100000), "ok");
    check(h, window(2000000, V3_SAMPLE_COUNT, 0), "sample_time");
    check(h, window(2000000, V3_SAMPLE_COUNT, 3201), "sample_time");
    check(h, window(4000000, V3_SAMPLE_COUNT, 3000), "sample_rate");
    check(h, window(6000000, V3_SAMPLE_COUNT, 2500, 3.9f), "sensor_range");
    check(h, window(8000000, V3_SAMPLE_COUNT, 2500, NAN), "numeric_input");
    check(h, window(10000000, V3_SAMPLE_COUNT, 2500, INFINITY), "numeric_input");
    auto reversed = window(12000000);
    v3_sample_t s = {};
    acquisition_record(reversed, 1, s);
    assert(reversed.time_error);
    // The 64-bit observation clock crosses micros() wrap without aliasing.
    check(h, window(UINT32_MAX - 500000ULL), "ok");

    setup();
    assert(Serial.tx_buffer == 2048 && serial_ready);
    assert(paused);
    process_command("start");
    Serial.output.clear();
    Serial.call_cost_us = 37;
    const uint64_t calls = Serial.calls;
    // Real loop and real print_result: complete sensor data without FG/PPR.
    loop();
    assert(acquisition.valid && acquisition.seq == 0 && !acquisition.have_gap);
    assert(previous_emit_seq == 0 && previous_emit_call_us == (Serial.calls - calls) * 37);
    assert(test_heap_queries == 1); // one snapshot per result, never per sample
    assert(Serial.output.find("\"previous_emit\":null") != std::string::npos);
    assert(Serial.output.find("\"pre_emit_us\":1282500") != std::string::npos);
    const uint64_t first_call_us = previous_emit_call_us;
    emit();
    const uint64_t last_end = acquisition.end_us;
    process_command("stop");
    Serial.output.clear();
    test_clock += (1ULL << 32) + 10000000; // stop exceeds one micros wrap
    loop();
    assert(acquisition_history.next_seq == 1);
    process_command("start");
    Serial.output.clear();
    loop();
    assert(acquisition.valid && acquisition.seq == 1);
    assert(Serial.output.find("\"previous_emit\":{\"seq\":0,\"call_us\":" +
                             std::to_string(first_call_us) + "}") != std::string::npos);
    assert(Serial.output.find("\"pre_emit_us\":1282500") != std::string::npos); // excludes pause
    assert(acquisition.have_gap && acquisition.gap_us == acquisition.start_us - last_end);
    assert(acquisition.gap_us > UINT32_MAX);
    emit();
    Wire.remaining_samples = 4; // prepare consumes 1, then 3 samples succeed
    loop();
    assert(acquisition.count == 3 && !acquisition.valid && acquisition.seq == 2);
    emit();
    const uint64_t partial_end = acquisition.end_us;
    Wire.fail = true;
    loop(); // initialization failure has the full common JSON schema
    assert(acquisition.count == 0 && acquisition.seq == 3);
    assert(Serial.output.find("\"previous_emit\":{\"seq\":2") != std::string::npos);
    assert(acquisition_history.last_end_us == partial_end);
    emit();
    Wire.fail = false;
    Wire.remaining_samples = -1;
    loop();
    assert(acquisition.valid && acquisition.have_gap && acquisition.seq == 4);
    assert(acquisition.gap_us == acquisition.start_us - partial_end);
    assert(test_heap_queries == 5);
    emit();

    // Conservative serialization budget using largest uint64 decimals and
    // longer reachable profile/reason/status strings, not measured UART time.
    acquisition.seq = UINT64_MAX;
    acquisition.start_us = UINT64_MAX - 1277500;
    acquisition.end_us = UINT64_MAX;
    acquisition.gap_us = UINT64_MAX - 1277500;
    acquisition.have_gap = true;
    acquisition.valid = false;
    acquisition.reason = "partial_window"; // longest quality reason
    profile_state = "learning_in_progress";
    profile_reason = "ppr_changed_baseline_invalidated";
    ppr = 16; phase_count = 5; learn_count = 336;
    strcpy(profiles.state.context_id, "ABCDEFGHIJKL");
    baseline_ready = true; profiles.state.current.id = UINT32_MAX;
    v3_signal_result_t signal = {};
    signal.reason = V3_SIGNAL_OK;
    signal.sample_rate_hz = 420;
    signal.fg_hz = 1000;
    signal.rpm = 60000;
    for (float &a : signal.amplitude_1x_g) a = 15.6f;
    policy_result.bearing.status = EM_V3_INSPECTION_REQUIRED;
    policy_result.misalignment.status = EM_V3_INSPECTION_REQUIRED;
    policy_result.belt.status = EM_V3_INSPECTION_REQUIRED;
    policy_result.imbalance.status = EM_V3_INSPECTION_REQUIRED;
    previous_emit_seq = UINT64_MAX - 1;
    previous_emit_call_us = UINT64_MAX;
    runtime_start_us = 0;
    test_clock = UINT64_MAX;
    Serial.call_cost_us = 0;
    test_heap.total_free_bytes = UINT32_MAX;
    test_heap.minimum_free_bytes = UINT32_MAX;
    test_heap.largest_free_block = UINT32_MAX;
    print_result("interwindow_gap_reset", V3_SAMPLE_COUNT, FG_RING_SIZE, &signal,
                 true, 15600, 1, -1);
    emit();

    // Both TX configuration and actual UART driver readiness are required.
    const auto attempts = acquisition_history.next_seq;
    Serial.begun = false;
    Serial.fail_config = true;
    setup();
    assert(!serial_ready && paused);
    loop();
    assert(acquisition_history.next_seq == attempts);
    Serial.begun = false;
    Serial.fail_config = false;
    Serial.fail_driver = true;
    setup();
    assert(!serial_ready && paused);
    loop();
    assert(acquisition_history.next_seq == attempts);
}
