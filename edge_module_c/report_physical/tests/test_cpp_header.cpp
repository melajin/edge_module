#include "fault_evidence.h"
#include <type_traits>
static_assert(std::is_standard_layout<fec_baseline_t>::value, "baseline must have C-compatible layout");
int main() {
    fec_config_t config;
    fec_config_default(&config);
    return config.fg_pulses_per_rev == 2 && fec_config_hash(&config) != 0 ? 0 : 1;
}
