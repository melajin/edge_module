/* ESP32 DevKitC V4 + ADXL345 + FG research adapter for em_v3.
 * No relay output or automatic machine control is present.
 * Confirm sensor supply, I2C levels and FG electrical interface separately.
 */
#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
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
static const unsigned LEARN_WINDOWS = 336;
static const float MIN_BASELINE_G = 0.001f;
static const float MAX_BASELINE_G = 4.0f;
static const uint32_t MAX_INTERWINDOW_GAP_US = 100000u;
/* 2048 edges covers a 512-sample window at the slowest accepted 380 Hz
 * sampling and fastest accepted 1 ms FG interval, including a prior edge. */
static const unsigned FG_RING_SIZE = V3_FG_RING_SIZE;
static const uint32_t PROFILE_MAGIC = 0x56334144u;
static const uint16_t PROFILE_SCHEMA = 1u;
static const char *PROFILE_NAMESPACE = "edgev3";
static const char *PROFILE_KEY = "profile";

struct __attribute__((packed)) DeviceProfile {
    uint32_t magic;
    uint16_t schema;
    uint8_t ppr;
    uint8_t baseline_valid;
    float baseline_g[3];
    uint32_t crc32;
};

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
static bool paused = false;
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
static char command_line[32];
static unsigned command_length = 0;
static const char *profile_state = "unregistered";
static const char *profile_reason = "not_loaded";

static uint32_t profile_crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
    }
    return ~crc;
}

static bool profile_baseline_valid(const DeviceProfile &profile)
{
    if (profile.baseline_valid != 1u || profile.ppr == 0u || profile.ppr > 16u)
        return false;
    for (unsigned axis = 0; axis < 3; ++axis) {
        const float value = profile.baseline_g[axis];
        if (!isfinite(value) || value < MIN_BASELINE_G || value > MAX_BASELINE_G)
            return false;
    }
    return true;
}

static DeviceProfile make_profile(unsigned profile_ppr, bool has_baseline,
                                  const float values[3])
{
    DeviceProfile profile = {};
    profile.magic = PROFILE_MAGIC;
    profile.schema = PROFILE_SCHEMA;
    profile.ppr = (uint8_t)profile_ppr;
    profile.baseline_valid = has_baseline ? 1u : 0u;
    if (has_baseline)
        for (unsigned axis = 0; axis < 3; ++axis) profile.baseline_g[axis] = values[axis];
    profile.crc32 = profile_crc32((const uint8_t *)&profile,
                                  offsetof(DeviceProfile, crc32));
    return profile;
}

static bool save_profile(const DeviceProfile &profile, const char **failure)
{
    Preferences preferences;
    if (!preferences.begin(PROFILE_NAMESPACE, false)) {
        *failure = "nvs_open_write";
        return false;
    }
    const size_t written = preferences.putBytes(PROFILE_KEY, &profile, sizeof(profile));
    DeviceProfile verify = {};
    const size_t read = written == sizeof(profile)
        ? preferences.getBytes(PROFILE_KEY, &verify, sizeof(verify)) : 0u;
    preferences.end();
    if (written != sizeof(profile) || read != sizeof(profile) ||
        memcmp(&verify, &profile, sizeof(profile)) != 0) {
        *failure = "nvs_write";
        return false;
    }
    return true;
}

static bool persist_profile(unsigned profile_ppr, bool has_baseline,
                            const float values[3], const char **failure)
{
    if (profile_ppr > 16u || (has_baseline && profile_ppr == 0u)) {
        *failure = "profile_values";
        return false;
    }
    const DeviceProfile profile = make_profile(profile_ppr, has_baseline, values);
    if (has_baseline && !profile_baseline_valid(profile)) {
        *failure = "baseline_range";
        return false;
    }
    if (!save_profile(profile, failure)) return false;
    return true;
}

static void load_profile()
{
    baseline_ready = false;
    learning = false;
    ppr = 0;
    memset(baseline_g, 0, sizeof(baseline_g));
    Preferences preferences;
    if (!preferences.begin(PROFILE_NAMESPACE, true)) {
        profile_reason = "nvs_open_read";
        profile_state = "unregistered";
        return;
    }
    const size_t length = preferences.getBytesLength(PROFILE_KEY);
    if (length == 0u) {
        preferences.end();
        profile_reason = "not_found";
        profile_state = "unregistered";
        return;
    }
    if (length != sizeof(DeviceProfile)) {
        preferences.end();
        profile_reason = "profile_size";
        profile_state = "invalid";
        return;
    }
    DeviceProfile stored = {};
    const size_t read = preferences.getBytes(PROFILE_KEY, &stored, sizeof(stored));
    preferences.end();
    if (read != sizeof(stored)) {
        profile_reason = "nvs_read";
        profile_state = "invalid";
        return;
    }
    if (stored.magic != PROFILE_MAGIC) {
        profile_reason = "profile_magic";
        profile_state = "invalid";
        return;
    }
    if (stored.schema != PROFILE_SCHEMA) {
        profile_reason = "schema_mismatch";
        profile_state = "invalid";
        return;
    }
    if (stored.crc32 != profile_crc32((const uint8_t *)&stored,
                                      offsetof(DeviceProfile, crc32))) {
        profile_reason = "profile_crc";
        profile_state = "invalid";
        return;
    }
    if (stored.ppr > 16u) {
        profile_reason = "ppr_range";
        profile_state = "invalid";
        return;
    }
    if (stored.baseline_valid == 1u) {
        if (!profile_baseline_valid(stored)) {
            profile_reason = "baseline_range";
            profile_state = "invalid";
            return;
        }
        memcpy(baseline_g, stored.baseline_g, sizeof(baseline_g));
        baseline_ready = true;
    } else if (stored.baseline_valid != 0u) {
        profile_reason = "baseline_flag";
        profile_state = "invalid";
        return;
    } else {
        for (unsigned axis = 0; axis < 3; ++axis) {
            if (stored.baseline_g[axis] != 0.0f) {
                profile_reason = "unexpected_baseline_data";
                profile_state = "invalid";
                return;
            }
        }
    }
    ppr = stored.ppr;
    profile_state = baseline_ready ? "baseline_ready" : (ppr ? "learn_required" : "unregistered");
    profile_reason = baseline_ready ? "loaded" : (ppr ? "baseline_missing" : "ppr_unset");
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

static bool adxl_next_sample(v3_sample_t *sample, const char **reason)
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
    const char *failure = "unknown";
    if (!persist_profile(ppr, true, candidate, &failure)) {
        learning = false;
        learn_count = 0;
        profile_state = "invalid";
        profile_reason = failure;
        reset_temporal();
        return;
    }
    memcpy(baseline_g, candidate, sizeof(baseline_g));
    baseline_ready = true;
    learning = false;
    profile_state = "baseline_ready";
    profile_reason = "learned_and_saved";
    reset_temporal();
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
    Serial.printf(",\"axis\":%d,\"ratio_1x\":", axis);
    if (evidence_valid) Serial.printf("%.5f", ratio);
    else Serial.print("null");
    Serial.print(",\"phase_concentration\":");
    if (evidence_valid) Serial.printf("%.5f", phase_concentration);
    else Serial.print("null");
    Serial.printf(",\"bearing\":\"%s\",\"misalignment\":\"%s\","
                  "\"belt\":\"%s\",\"imbalance\":\"%s\","
                  "\"imbalance_votes\":%u,\"auto_confirm\":false}\n",
                  em_v3_status_name(policy_result.bearing.status),
                  em_v3_status_name(policy_result.misalignment.status),
                  em_v3_status_name(policy_result.belt.status),
                  em_v3_status_name(policy_result.imbalance.status),
                  policy_result.imbalance.votes_positive);
}

static void process_command(const char *line)
{
    if (strcmp(line, "learn") == 0) {
        if (ppr == 0u) {
            Serial.println("{\"command\":\"learn\",\"error\":\"set_ppr_first\"}");
            return;
        }
        paused = false;
        learning = true;
        learn_count = 0;
        profile_state = "learning_in_progress";
        profile_reason = "learning_in_progress";
        reset_temporal();
        Serial.printf("{\"command\":\"learn\",\"state\":\"started\",\"windows\":%u,\"ppr\":%u}\n",
                      LEARN_WINDOWS, ppr);
    } else if (strcmp(line, "clear") == 0) {
        const float unused[3] = {0, 0, 0};
        const char *failure = "unknown";
        if (!persist_profile(0u, false, unused, &failure)) {
            profile_state = "invalid";
            profile_reason = failure;
            baseline_ready = false;
            learning = false;
            learn_count = 0;
            reset_temporal();
            Serial.printf("{\"command\":\"clear\",\"error\":\"%s\"}\n", failure);
            return;
        }
        ppr = 0u;
        baseline_ready = false;
        learning = false;
        learn_count = 0;
        memset(baseline_g, 0, sizeof(baseline_g));
        profile_state = "unregistered";
        profile_reason = "cleared";
        reset_temporal();
        Serial.println("{\"command\":\"clear\",\"state\":\"done\",\"nvs\":\"verified\"}");
    } else if (strcmp(line, "stop") == 0) {
        paused = true;
        abort_learning();
        reset_temporal();
        Serial.println("{\"command\":\"stop\",\"state\":\"paused\"}");
    } else if (strcmp(line, "start") == 0) {
        paused = false;
        reset_temporal();
        Serial.println("{\"command\":\"start\",\"state\":\"sampling\"}");
    } else if (strncmp(line, "ppr ", 4) == 0) {
        char *end = nullptr;
        const unsigned long parsed = strtoul(line + 4, &end, 10);
        if (end == line + 4 || *end != '\0' || parsed < 1 || parsed > 16) {
            Serial.println("{\"command\":\"ppr\",\"error\":\"use_1_to_16\"}");
            return;
        }
        const unsigned new_ppr = (unsigned)parsed;
        const float unused[3] = {0, 0, 0};
        const char *failure = "unknown";
        if (!persist_profile(new_ppr, false, unused, &failure)) {
            Serial.printf("{\"command\":\"ppr\",\"error\":\"%s\",\"ppr\":%u}\n",
                          failure, ppr);
            return;
        }
        ppr = new_ppr;
        baseline_ready = false; /* 1x frequency changed, so baseline is invalid. */
        memset(baseline_g, 0, sizeof(baseline_g));
        learning = false;
        learn_count = 0;
        profile_state = "learn_required";
        profile_reason = "ppr_changed_baseline_invalidated";
        reset_temporal();
        Serial.printf("{\"command\":\"ppr\",\"value\":%u,\"baseline_ready\":false,\"nvs\":\"verified\"}\n", ppr);
    } else if (line[0]) {
        Serial.println("{\"error\":\"commands: ppr N | learn | clear | stop | start\"}");
    }
}

static void read_commands()
{
    while (Serial.available() > 0) {
        const char c = (char)Serial.read();
        if (c == '\r' || c == '\n') {
            command_line[command_length] = '\0';
            process_command(command_line);
            command_length = 0;
        } else if (command_length + 1 < sizeof(command_line)) {
            command_line[command_length++] = c;
        } else command_length = 0;
    }
}

void setup()
{
    Serial.begin(SERIAL_BAUD);
    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(400000);
    Wire.setTimeOut(5);
    pinMode(FG_PIN, INPUT); /* External FG interface must be verified. */
    attachInterrupt(digitalPinToInterrupt(FG_PIN), fg_falling_isr, FALLING);
    em_v3_init(&policy, 1.1f, 0.0f); /* Existing V3 policy threshold. */
    em_v3_unavailable(&policy_result);
    load_profile();
    sensor_ready = adxl_begin();
    Serial.printf("{\"boot\":\"adxl345_v3\",\"ppr\":%u,\"profile_state\":\"%s\",\"profile_reason\":\"%s\",\"baseline_ready\":%s,\"baseline_1x_g\":[%.5f,%.5f,%.5f],\"sensor_ready\":%s,\"commands\":\"ppr N | learn | clear | stop | start\"}\n",
                  ppr, profile_state, profile_reason,
                  baseline_ready ? "true" : "false",
                  baseline_g[0], baseline_g[1], baseline_g[2],
                  sensor_ready ? "true" : "false");
}

void loop()
{
    read_commands();
    if (paused) {
        delay(20);
        return;
    }
    if (!sensor_ready) {
        abort_learning();
        reset_temporal();
        sensor_ready = adxl_begin();
        if (!sensor_ready) {
            Serial.println("{\"reason\":\"adxl_init\",\"imbalance\":\"unavailable\"}");
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
        print_result("adxl_i2c", 0, 0, &unavailable, false, 0, 0, -1);
        return;
    }
    unsigned captured = 0;
    portENTER_CRITICAL(&fg_mux);
    const uint64_t fg_count_at_start = fg_count;
    portEXIT_CRITICAL(&fg_mux);
    for (; captured < V3_SAMPLE_COUNT; ++captured) {
        if (!adxl_next_sample(&samples[captured], &reason)) {
            sensor_ready = false;
            break;
        }
    }
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

    if (learning) {
        update_learning(&signal);
        print_result(baseline_ready ? "baseline_learned" : "learning",
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
