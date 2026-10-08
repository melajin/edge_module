/* ESP32 DevKitC V4 + ADXL345 + FG research adapter for em_v3.
 * No relay output or automatic machine control is present.
 * Confirm sensor supply, I2C levels and FG electrical interface separately.
 */
#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <esp_heap_caps.h>
#include "acquisition_quality.h"
#include "profile_manager.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

extern "C" {
#include "em_v3.h"
#include "v3_signal.h"
}

static const int SDA_PIN = 21, SCL_PIN = 22, FG_PIN = 25;
static const uint8_t ADXL_ADDR = 0x53; /* ALT ADDRESS low: verify actual board. */
static const uint32_t SERIAL_BAUD = 115200;
static const size_t SERIAL_TX_BUFFER_BYTES = 2048;
static bool serial_ready = false;
static const unsigned LEARN_WINDOWS = 336;
static const float MIN_BASELINE_G = 0.001f;
static const uint32_t MAX_INTERWINDOW_GAP_US = 100000u;
/* 2048 edges covers a 512-sample window at the slowest accepted 380 Hz
 * sampling and fastest accepted 1 ms FG interval, including a prior edge. */
static const unsigned FG_RING_SIZE = V3_FG_RING_SIZE;
static ProfileManager profiles;

static const uint8_t REG_DEVID = 0x00, REG_BW_RATE = 0x2C;
static const uint8_t REG_POWER_CTL = 0x2D, REG_INT_ENABLE = 0x2E;
static const uint8_t REG_INT_SOURCE = 0x30, REG_DATA_FORMAT = 0x31;
static const uint8_t REG_DATAX0 = 0x32, REG_FIFO_CTL = 0x38;
static const uint8_t DATA_READY = 0x80, OVERRUN = 0x01;

static v3_sample_t samples[V3_SAMPLE_COUNT];
static v3_fg_edge_t fg_snapshot[FG_RING_SIZE];
static volatile v3_fg_edge_t fg_ring[FG_RING_SIZE];
static volatile uint64_t fg_count = 0;
static volatile uint32_t fg_last_us = 0;
static portMUX_TYPE fg_mux = portMUX_INITIALIZER_UNLOCKED;

static em_v3_t policy;
static em_v3_result_t policy_result;
static unsigned ppr = 0; /* Explicit `ppr N` command required; 2 is only a test hypothesis. */
static bool sensor_ready = false, baseline_ready = false, learning = false;
static bool paused = true;
static float baseline_g[3] = {0, 0, 0};
static float learn_amplitude[3][LEARN_WINDOWS];
static unsigned learn_count = 0;
static float phase_history[3][EM_V3_WINDOW];
static unsigned phase_count = 0, phase_next = 0;
static bool have_last_sample = false;
static uint32_t last_sample_us = 0;
static bool have_last_fg = false;
static v3_fg_edge_t last_fg_edge;
static float last_fg_period_us = 0.0f;
static bool interwindow_overrun = false;
// Observation bookkeeping deliberately survives reset_temporal().
static AcquisitionHistory acquisition_history;
static AcquisitionQuality acquisition;
// Survives command/diagnostic resets; duration refers only to result printing calls.
static uint64_t runtime_start_us = 0, previous_emit_seq = 0, previous_emit_call_us = 0;
static bool have_previous_emit = false;
static char acquisition_session[17];
static char command_line[64];
static bool command_overlong = false;
static unsigned command_length = 0;
static const char *profile_state = "unregistered";
static const char *profile_reason = "not_loaded";

static void sync_profile()
{
    ppr = profiles.state.ppr;
    baseline_ready = profiles.ready();
    memcpy(baseline_g, profiles.state.current.amplitude_g, sizeof(baseline_g));
    if (profiles.hold) paused = true;
    profile_state = profiles.hold ? "storage_hold" : learning ? "learning_in_progress" :
        profiles.state.candidate.id ? "candidate_pending" : baseline_ready ? "baseline_ready" :
        profiles.state.context_id[0] && ppr ? "learn_required" : "unregistered";
    profile_reason = profiles.reason;
}

static void print_baseline(const BaselineProfile &b)
{
    if (!b.id) { Serial.print("null"); return; }
    Serial.printf("{\"id\":%u,\"context_id\":\"%s\",\"ppr\":%u,"
                  "\"amplitude_g\":[%.5f,%.5f,%.5f],\"learn_count\":%u,"
                  "\"origin\":\"%s\",\"session\":\"%s\",\"seq\":%llu}",
                  (unsigned)b.id, b.context_id, (unsigned)b.ppr,
                  b.amplitude_g[0], b.amplitude_g[1], b.amplitude_g[2],
                  (unsigned)b.learn_count, b.origin == 1 ? "learned" : "legacy_import",
                  b.session, (unsigned long long)b.seq);
}

static void print_profile(const char *command, bool ok, const char *reason)
{
    sync_profile();
    Serial.printf("{\"event\":\"profile\",\"v\":1,\"command\":\"%s\",\"ok\":%s,"
                  "\"reason\":\"%s\",\"firmware\":\"postreport_v2\",\"profile\":{"
                  "\"selected\":{\"context_id\":", command, ok ? "true" : "false", reason);
    if (profiles.state.context_id[0]) Serial.printf("\"%s\"", profiles.state.context_id);
    else Serial.print("null");
    Serial.printf(",\"ppr\":%u},\"current\":", ppr);
    print_baseline(profiles.state.current);
    Serial.print(",\"candidate\":"); print_baseline(profiles.state.candidate);
    Serial.print(",\"previous\":"); print_baseline(profiles.state.previous);
    Serial.printf(",\"baseline_ready\":%s,\"learning\":%s,\"learn_count\":%u,\"paused\":%s,"
                  "\"storage\":{\"hold\":%s,\"reason\":\"%s\",\"generation\":%u},"
                  "\"legacy\":{\"valid\":%s,\"reason\":\"%s\"}}}\n",
                  baseline_ready ? "true" : "false", learning ? "true" : "false", learn_count,
                  paused ? "true" : "false", profiles.hold ? "true" : "false", profiles.reason,
                  (unsigned)profiles.state.generation, profiles.legacy_valid ? "true" : "false",
                  profiles.legacy_reason);
}

static void IRAM_ATTR fg_falling_isr()
{
    const uint32_t now = micros();
    portENTER_CRITICAL_ISR(&fg_mux);
    if (fg_count == 0 || (uint32_t)(now - fg_last_us) >= 500u) {
        const uint64_t ordinal = fg_count++;
        fg_ring[ordinal % FG_RING_SIZE].time_us = now;
        fg_ring[ordinal % FG_RING_SIZE].ordinal = ordinal;
        fg_last_us = now;
    }
    portEXIT_CRITICAL_ISR(&fg_mux);
}

static void reset_temporal()
{
    portENTER_CRITICAL(&fg_mux);
    fg_count = 0;
    fg_last_us = 0;
    portEXIT_CRITICAL(&fg_mux);
    em_v3_reset(&policy);
    em_v3_unavailable(&policy_result);
    phase_count = phase_next = 0;
    have_last_sample = false;
    have_last_fg = false;
}

static void abort_learning()
{
    if (learning) {
        learning = false;
        learn_count = 0;
        profile_state = baseline_ready ? "baseline_ready"
                                      : (ppr ? "learn_required" : "unregistered");
        profile_reason = "learning_interrupted";
    }
}

static bool adxl_write(uint8_t reg, uint8_t value)
{
    Wire.beginTransmission(ADXL_ADDR);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}

static bool adxl_read(uint8_t reg, uint8_t *dest, size_t count)
{
    Wire.beginTransmission(ADXL_ADDR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom((int)ADXL_ADDR, (int)count, (int)true) != (int)count)
        return false;
    for (size_t i = 0; i < count; ++i) dest[i] = (uint8_t)Wire.read();
    return true;
}

static bool adxl_begin()
{
    uint8_t value = 0;
    if (!adxl_read(REG_DEVID, &value, 1) || value != 0xE5) return false;
    /* BW_RATE 0x0C: 400 Hz ODR; FULL_RES=1, range=+/-4 g: ~256 LSB/g. */
    if (!adxl_write(REG_POWER_CTL, 0x00) ||
        !adxl_write(REG_BW_RATE, 0x0C) ||
        !adxl_write(REG_DATA_FORMAT, 0x09) ||
        !adxl_write(REG_FIFO_CTL, 0x00) || /* Bypass: overrun means unread axes replaced. */
        !adxl_write(REG_INT_ENABLE, DATA_READY) ||
        !adxl_write(REG_POWER_CTL, 0x08)) return false;
    if (!adxl_read(REG_BW_RATE, &value, 1) || value != 0x0C) return false;
    if (!adxl_read(REG_DATA_FORMAT, &value, 1) || value != 0x09) return false;
    if (!adxl_read(REG_FIFO_CTL, &value, 1) || value != 0x00) return false;
    return true;
}

static bool adxl_next_sample(v3_sample_t *sample, uint64_t *read_start_us, const char **reason)
{
    const uint32_t wait_start = micros();
    uint8_t source = 0;
    for (;;) {
        if (!adxl_read(REG_INT_SOURCE, &source, 1)) {
            *reason = "adxl_i2c";
            return false;
        }
        if (source & OVERRUN) {
            *reason = "adxl_overrun";
            return false;
        }
        if (source & DATA_READY) break;
        if ((uint32_t)(micros() - wait_start) > 5000u) {
            *reason = "adxl_data_timeout";
            return false;
        }
        delayMicroseconds(80);
    }
    sample->time_us = micros();
    uint8_t raw[6];
    *read_start_us = (uint64_t)esp_timer_get_time();
    if (!adxl_read(REG_DATAX0, raw, sizeof(raw))) {
        *reason = "adxl_i2c";
        return false;
    }
    for (unsigned axis = 0; axis < 3; ++axis) {
        const uint16_t bits = (uint16_t)raw[2 * axis] |
                              ((uint16_t)raw[2 * axis + 1] << 8);
        const int16_t signed_raw = (int16_t)bits;
        sample->g[axis] = signed_raw / 256.0f;
    }
    return true;
}

static bool adxl_prepare_window()
{
    /* Processing/JSON output takes place between discrete windows. Discard
     * one old register sample to clear the expected boundary overrun, then
     * require every sample of the new window to be fresh and overrun-free. */
    interwindow_overrun = false;
    uint8_t source = 0, discard[6];
    if (!adxl_read(REG_INT_SOURCE, &source, 1) ||
        !adxl_read(REG_DATAX0, discard, sizeof(discard))) return false;
    interwindow_overrun = (source & OVERRUN) != 0;
    return true;
}

static unsigned copy_fg_edges(uint64_t *end_count)
{
    portENTER_CRITICAL(&fg_mux);
    const uint64_t end = fg_count;
    *end_count = end;
    const uint64_t begin = end > FG_RING_SIZE ? end - FG_RING_SIZE : 0u;
    const unsigned n = (unsigned)(end - begin);
    for (unsigned i = 0; i < n; ++i) {
        const uint64_t ordinal = begin + i;
        fg_snapshot[i].time_us = fg_ring[ordinal % FG_RING_SIZE].time_us;
        fg_snapshot[i].ordinal = fg_ring[ordinal % FG_RING_SIZE].ordinal;
    }
    portEXIT_CRITICAL(&fg_mux);
    return n;
}

static float median_amplitude(float *values, unsigned count)
{
    for (unsigned i = 1; i < count; ++i) {
        const float item = values[i];
        unsigned j = i;
        while (j > 0 && values[j - 1] > item) {
            values[j] = values[j - 1];
            --j;
        }
        values[j] = item;
    }
    return (values[(count - 1) / 2] + values[count / 2]) * 0.5f;
}

static void update_learning(const v3_signal_result_t *signal)
{
    for (unsigned axis = 0; axis < 3; ++axis)
        learn_amplitude[axis][learn_count] = signal->amplitude_1x_g[axis];
    ++learn_count;
    if (learn_count < LEARN_WINDOWS) return;
    float candidate[3];
    for (unsigned axis = 0; axis < 3; ++axis) {
        candidate[axis] = median_amplitude(learn_amplitude[axis], LEARN_WINDOWS);
    }
    const bool saved = profiles.candidate(candidate, acquisition_session, acquisition.seq);
    learning = false;
    learn_count = 0;
    sync_profile();
    reset_temporal();
    // Detailed state event can exceed a measurement frame's UART wire budget.
    // The temporal reset prevents a following window bridging this event gap.
    print_profile("candidate", saved, saved ? "candidate_saved" : profiles.reason);
}

static void push_phases(const v3_signal_result_t *signal)
{
    for (unsigned axis = 0; axis < 3; ++axis)
        phase_history[axis][phase_next] = signal->phase_1x_rad[axis];
    if (phase_count < EM_V3_WINDOW) ++phase_count;
    phase_next = (phase_next + 1u) % EM_V3_WINDOW;
}

static float concentration(unsigned axis)
{
    float re = 0.0f, im = 0.0f;
    for (unsigned i = 0; i < EM_V3_WINDOW; ++i) {
        re += cosf(phase_history[axis][i]);
        im += sinf(phase_history[axis][i]);
    }
    return hypotf(re, im) / EM_V3_WINDOW;
}

static void print_result(const char *reason, unsigned captured, unsigned fg_edges,
                         const v3_signal_result_t *signal,
                         bool evidence_valid, float ratio,
                         float phase_concentration, int axis)
{
    const uint64_t emit_start_us = (uint64_t)esp_timer_get_time();
    const uint64_t pre_emit_us = emit_start_us - runtime_start_us;
    multi_heap_info_t heap = {};
    heap_caps_get_info(&heap, MALLOC_CAP_8BIT);
    const bool measured = captured == V3_SAMPLE_COUNT &&
                          signal->reason == V3_SIGNAL_OK &&
                          signal->sample_rate_hz > 0.0f;
    Serial.printf("{\"reason\":\"%s\",\"samples\":%u,\"fg_buffered_edges\":%u,\"ppr\":%u,"
                  "\"interwindow_overrun\":%s,"
                  "\"learning\":%s,\"paused\":%s,\"learn_count\":%u,\"baseline_ready\":%s,"
                  "\"profile_state\":\"%s\",\"profile_reason\":\"%s\","
                  "\"phase_windows\":%u,\"fs_hz\":",
                  reason, captured, fg_edges, ppr,
                  interwindow_overrun ? "true" : "false",
                  learning ? "true" : "false",
                  paused ? "true" : "false", learn_count,
                  baseline_ready ? "true" : "false", profile_state, profile_reason,
                  phase_count);
    if (measured) {
        Serial.printf("%.2f,\"fg_hz\":%.3f,\"rpm\":%.2f,"
                      "\"amp_1x_g\":[%.5f,%.5f,%.5f]",
                      signal->sample_rate_hz, signal->fg_hz, signal->rpm,
                      signal->amplitude_1x_g[0], signal->amplitude_1x_g[1],
                      signal->amplitude_1x_g[2]);
    } else Serial.print("null,\"fg_hz\":null,\"rpm\":null,\"amp_1x_g\":null");
    Serial.print(",\"context_id\":");
    if (profiles.state.context_id[0]) Serial.printf("\"%s\"", profiles.state.context_id);
    else Serial.print("null");
    Serial.print(",\"baseline_id\":");
    if (baseline_ready) Serial.printf("%u", (unsigned)profiles.state.current.id);
    else Serial.print("null");
    Serial.printf(",\"axis\":%d,\"ratio_1x\":", axis);
    if (evidence_valid) Serial.printf("%.5f", ratio);
    else Serial.print("null");
    Serial.print(",\"phase_concentration\":");
    if (evidence_valid) Serial.printf("%.5f", phase_concentration);
    else Serial.print("null");
    Serial.printf(",\"acquisition\":{\"v\":1,\"session\":\"%s\",\"seq\":%llu,\"start_us\":",
                  acquisition_session, (unsigned long long)acquisition.seq);
    if (acquisition.count) Serial.printf("%llu", (unsigned long long)acquisition.start_us);
    else Serial.print("null");
    Serial.print(",\"end_us\":");
    if (acquisition.count) Serial.printf("%llu", (unsigned long long)acquisition.end_us);
    else Serial.print("null");
    Serial.print(",\"gap_us\":");
    if (acquisition.have_gap) Serial.printf("%llu", (unsigned long long)acquisition.gap_us);
    else Serial.print("null");
    Serial.printf(",\"valid\":%s,\"reason\":\"%s\"}",
                  acquisition.valid ? "true" : "false", acquisition.reason);
    Serial.printf(",\"runtime\":{\"v\":1,\"pre_emit_us\":%llu,\"previous_emit\":",
                  (unsigned long long)pre_emit_us);
    if (have_previous_emit)
        Serial.printf("{\"seq\":%llu,\"call_us\":%llu}",
                      (unsigned long long)previous_emit_seq,
                      (unsigned long long)previous_emit_call_us);
    else Serial.print("null");
    Serial.printf(",\"heap\":{\"free_bytes\":%u,\"min_free_bytes\":%u,\"largest_free_bytes\":%u}}",
                  (unsigned)heap.total_free_bytes, (unsigned)heap.minimum_free_bytes,
                  (unsigned)heap.largest_free_block);
    Serial.printf(",\"bearing\":\"%s\",\"misalignment\":\"%s\","
                  "\"belt\":\"%s\",\"imbalance\":\"%s\","
                  "\"imbalance_votes\":%u,\"auto_confirm\":false}\n",
                  em_v3_status_name(policy_result.bearing.status),
                  em_v3_status_name(policy_result.misalignment.status),
                  em_v3_status_name(policy_result.belt.status),
                  em_v3_status_name(policy_result.imbalance.status),
                  policy_result.imbalance.votes_positive);
    // No flush: this is formatting/queueing-call time, not UART/PC completion.
    previous_emit_call_us = (uint64_t)esp_timer_get_time() - emit_start_us;
    previous_emit_seq = acquisition.seq;
    have_previous_emit = true;
}

static bool decimal_u32(const char *text, uint32_t *value)
{
    if (!text[0]) return false;
    uint32_t n = 0;
    for (const char *c = text; *c; ++c) {
        if (*c < '0' || *c > '9' || n > (UINT32_MAX - (unsigned)(*c - '0')) / 10u)
            return false;
        n = n * 10u + (unsigned)(*c - '0');
    }
    *value = n;
    return true;
}

static void process_command(const char *line)
{
    if (!line[0]) return;
    bool ok = false, changed = false;
    const char *command = "invalid", *reason = "unknown_command";
    uint32_t number = 0;
    if (strcmp(line, "profile") == 0) {
        // Explicit status output also resets temporal continuity: its full
        // state frame may take over 100 ms on 115200 8N1.
        abort_learning(); reset_temporal();
        print_profile("profile", true, "status"); return;
    } else if (strcmp(line, "stop") == 0) {
        command = "stop"; paused = true; changed = ok = true; reason = "paused";
    } else if (strcmp(line, "start") == 0) {
        command = "start";
        if (profiles.hold) reason = "storage_hold";
        else { paused = false; changed = ok = true; reason = "sampling"; }
    } else if (strcmp(line, "learn") == 0) {
        command = "learn";
        if (profiles.hold) reason = "storage_hold";
        else if (profiles.state.candidate.id) reason = "candidate_exists";
        else if (learning) reason = "learning_in_progress";
        else if (!profiles.state.context_id[0] || !ppr) reason = "selection_required";
        else {
            learning = true; paused = false; learn_count = 0;
            profiles.reason = "learning_in_progress"; reset_temporal();
            print_profile(command, true, "started"); return;
        }
    } else if (strncmp(line, "context ", 8) == 0) {
        command = "context"; ok = profiles.select_context(line + 8); changed = ok; reason = profiles.reason;
    } else if (strncmp(line, "ppr ", 4) == 0) {
        command = "ppr";
        if (!decimal_u32(line + 4, &number) || !number || number > 16) reason = "ppr_invalid";
        else { ok = profiles.select_ppr(number); changed = ok; reason = profiles.reason; }
    } else if (strncmp(line, "approve ", 8) == 0) {
        command = "approve";
        if (!decimal_u32(line + 8, &number) || !number) reason = "candidate_id_invalid";
        else { ok = profiles.approve(number); changed = ok; reason = profiles.reason; }
    } else if (strcmp(line, "discard") == 0) {
        command = "discard"; ok = profiles.discard(); changed = ok; reason = profiles.reason;
    } else if (strcmp(line, "rollback") == 0) {
        command = "rollback"; ok = profiles.rollback(); changed = ok; reason = profiles.reason;
    } else if (strncmp(line, "import ", 7) == 0) {
        command = "import"; ok = profiles.import_legacy(line + 7, acquisition_session,
        acquisition_history.next_seq); changed = ok; reason = profiles.reason;
    } else if (strcmp(line, "clear") == 0) {
        command = "clear"; ok = profiles.clear(); changed = ok; reason = profiles.reason;
    } else if (strcmp(line, "recover") == 0) {
        command = "recover"; ok = profiles.recover(); changed = ok; reason = profiles.reason;
        paused = true;
    }
    if (changed || profiles.hold) { abort_learning(); reset_temporal(); }
    // Every response is a potentially long frame; abort incomplete learning
    // and reset continuity even for a rejected command.
    if (!changed) { abort_learning(); reset_temporal(); }
    sync_profile();
    print_profile(command, ok, reason);
}

static void read_commands()
{
    while (Serial.available() > 0) {
        const char c = (char)Serial.read();
        if (c == '\r' || c == '\n') {
            if (command_overlong) {
                abort_learning(); reset_temporal();
                print_profile("invalid", false, "command_too_long");
            } else {
                command_line[command_length] = '\0'; process_command(command_line);
            }
            command_length = 0; command_overlong = false;
        } else if (!command_overlong) {
            if (c < 32 || c > 126 || command_length + 1 >= sizeof(command_line))
                command_overlong = true;
            else command_line[command_length++] = c;
        }
    }
}

void setup()
{
    // Queue a complete result without spending its whole wire time between
    // windows. Allocation/driver failure must not silently start acquisition.
    const bool tx_configured = Serial.setTxBufferSize(SERIAL_TX_BUFFER_BYTES) == SERIAL_TX_BUFFER_BYTES;
    Serial.begin(SERIAL_BAUD);
    serial_ready = tx_configured && (bool)Serial;
    if (!serial_ready) {
        paused = true;
        if (Serial) Serial.println("{\"boot\":\"adxl345_v3\",\"error\":\"serial_tx_unavailable\"}");
        return;
    }
    snprintf(acquisition_session, sizeof(acquisition_session), "%08lx%08lx",
             (unsigned long)esp_random(), (unsigned long)esp_random());
    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(400000);
    Wire.setTimeOut(5);
    pinMode(FG_PIN, INPUT); /* External FG interface must be verified. */
    attachInterrupt(digitalPinToInterrupt(FG_PIN), fg_falling_isr, FALLING);
    em_v3_init(&policy, 1.1f, 0.0f); /* Existing V3 policy threshold. */
    em_v3_unavailable(&policy_result);
    paused = true; learning = false; learn_count = 0;
    profiles.load(); sync_profile();
    sensor_ready = adxl_begin();
    print_profile("boot", !profiles.hold, profiles.reason);
}

void loop()
{
    if (!serial_ready) { delay(1000); return; }
    read_commands();
    if (paused) {
        delay(20);
        return;
    }
    acquisition = AcquisitionQuality{};
    runtime_start_us = (uint64_t)esp_timer_get_time();
    if (!sensor_ready) {
        abort_learning();
        reset_temporal();
        sensor_ready = adxl_begin();
        if (!sensor_ready) {
            interwindow_overrun = false;
            acquisition_finish(acquisition_history, acquisition);
            v3_signal_result_t unavailable = {};
            print_result("adxl_init", 0, 0, &unavailable, false, 0, 0, -1);
            delay(1000);
            return;
        }
    }

    const char *reason = "ok";
    if (!adxl_prepare_window()) {
        sensor_ready = false;
        abort_learning();
        reset_temporal();
        v3_signal_result_t unavailable = {};
        acquisition_finish(acquisition_history, acquisition);
        print_result("adxl_i2c", 0, 0, &unavailable, false, 0, 0, -1);
        return;
    }
    unsigned captured = 0;
    portENTER_CRITICAL(&fg_mux);
    const uint64_t fg_count_at_start = fg_count;
    portEXIT_CRITICAL(&fg_mux);
    for (; captured < V3_SAMPLE_COUNT; ++captured) {
        uint64_t read_start_us = 0;
        if (!adxl_next_sample(&samples[captured], &read_start_us, &reason)) {
            sensor_ready = false;
            break;
        }
        acquisition_record(acquisition, read_start_us, samples[captured]);
    }
    acquisition_finish(acquisition_history, acquisition);
    v3_signal_result_t signal = {};
    if (captured != V3_SAMPLE_COUNT) {
        abort_learning();
        reset_temporal();
        print_result(reason, captured, 0, &signal, false, 0, 0, -1);
        return;
    }
    if (have_last_sample &&
        (uint32_t)(samples[0].time_us - last_sample_us) > MAX_INTERWINDOW_GAP_US) {
        abort_learning();
        reset_temporal();
        print_result("interwindow_gap_reset", captured, 0, &signal,
                     false, 0, 0, -1);
        return;
    }
    last_sample_us = samples[V3_SAMPLE_COUNT - 1].time_us;
    have_last_sample = true;

    uint64_t fg_count_at_end = 0;
    const unsigned fg_n = copy_fg_edges(&fg_count_at_end);
    if (have_last_fg &&
        !v3_fg_gap_contiguous(fg_snapshot, fg_n, last_fg_edge,
                              last_fg_period_us, samples[0].time_us)) {
        reason = "fg_gap_reset";
        abort_learning();
        reset_temporal();
        print_result(reason, captured, fg_n, &signal, false, 0, 0, -1);
        return;
    }
    if (!v3_analyze_1x(samples, captured, fg_snapshot, fg_n, ppr,
                       fg_count_at_start, fg_count_at_end, &signal)) {
        reason = v3_signal_reason_name(signal.reason);
        abort_learning();
        reset_temporal();
        print_result(reason, captured, fg_n, &signal, false, 0, 0, -1);
        return;
    }
    last_fg_edge = signal.last_fg_edge;
    last_fg_period_us = signal.fg_period_us;
    have_last_fg = true;

    if (!acquisition.valid) {
        abort_learning(); reset_temporal();
        print_result(acquisition.reason, captured, fg_n, &signal, false, 0, 0, -1);
        return;
    }

    if (learning) {
        update_learning(&signal);
        print_result(learning ? "learning" : profiles.state.candidate.id ? "candidate_pending" : "learning_aborted",
                     captured, fg_n, &signal, false, 0, 0, -1);
        return;
    }

    em_v3_input_t evidence = {};
    float ratio = 0.0f, phase_c = 0.0f;
    int selected_axis = -1;
    bool evidence_valid = false;
    if (baseline_ready) {
        push_phases(&signal);
        if (phase_count == EM_V3_WINDOW) {
            for (unsigned axis = 0; axis < 3; ++axis) {
                if (baseline_g[axis] < MIN_BASELINE_G) continue;
                const float candidate = signal.amplitude_1x_g[axis] / baseline_g[axis];
                if (candidate > ratio) {
                    ratio = candidate;
                    selected_axis = (int)axis;
                }
            }
            if (selected_axis >= 0) {
                phase_c = concentration((unsigned)selected_axis);
                evidence.imbalance.rpm_available = true;
                evidence.imbalance.baseline_available = true;
                evidence.imbalance.amplitude_present = true;
                evidence.imbalance.phase_present = true;
                evidence.imbalance.amplitude_ratio_1x = ratio;
                evidence.imbalance.phase_concentration = phase_c;
                evidence_valid = true;
            }
        }
    }
    if (!em_v3_update(&policy, &evidence, &policy_result)) {
        reset_temporal();
        reason = "v3_numeric_input";
    } else if (strcmp(reason, "ok") == 0 && !baseline_ready)
        reason = "baseline_missing";
    else if (strcmp(reason, "ok") == 0 && !evidence_valid)
        reason = "phase_history_warmup";
    print_result(reason, captured, fg_n, &signal, evidence_valid, ratio, phase_c,
                 selected_axis);
}
