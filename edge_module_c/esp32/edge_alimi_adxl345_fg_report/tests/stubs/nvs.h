#pragma once
#include "Preferences.h"
typedef int esp_err_t;
static const esp_err_t ESP_OK = 0, ESP_FAIL = -1;
static const esp_err_t ESP_ERR_NVS_NOT_FOUND = 0x1102;
static const esp_err_t ESP_ERR_NVS_TYPE_MISMATCH = 0x1103;
static const esp_err_t ESP_ERR_NVS_INVALID_LENGTH = 0x110c;
static const int NVS_READONLY = 0;
struct TestNvsHandle { std::string ns; };
typedef TestNvsHandle *nvs_handle_t;
inline esp_err_t nvs_open(const char *ns, int, nvs_handle_t *handle)
{
    if (Preferences::fail_open_read) return ESP_FAIL;
    const auto prefix = std::string(ns) + "/";
    bool found = false;
    for (const auto &entry : Preferences::data)
        if (entry.first.compare(0, prefix.size(), prefix) == 0) found = true;
    if (!found) return ESP_ERR_NVS_NOT_FOUND;
    *handle = new TestNvsHandle{ns}; return ESP_OK;
}
inline esp_err_t nvs_get_blob(nvs_handle_t handle, const char *key, void *target, size_t *size)
{
    const auto full = handle->ns + "/" + key;
    const auto &faults = target ? Preferences::read_errors : Preferences::query_errors;
    const auto fault = faults.find(full);
    if (fault != faults.end()) return fault->second;
    if (target && Preferences::fail_read) return ESP_FAIL;
    const auto entry = Preferences::data.find(full);
    if (entry == Preferences::data.end()) return ESP_ERR_NVS_NOT_FOUND;
    if (!target) { *size = entry->second.size(); return ESP_OK; }
    if (*size < entry->second.size()) return ESP_ERR_NVS_INVALID_LENGTH;
    std::memcpy(target, entry->second.data(), entry->second.size());
    *size = entry->second.size();
    if (Preferences::corrupt_readback && *size) static_cast<unsigned char *>(target)[0] ^= 1;
    return ESP_OK;
}
inline void nvs_close(nvs_handle_t handle) { delete handle; }
