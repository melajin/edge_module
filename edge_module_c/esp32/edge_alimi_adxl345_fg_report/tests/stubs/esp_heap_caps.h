#pragma once
#include <cstddef>
#include <cassert>
#define MALLOC_CAP_8BIT 4
struct multi_heap_info_t {
    size_t total_free_bytes = 240000;
    size_t minimum_free_bytes = 220000;
    size_t largest_free_block = 120000;
};
extern multi_heap_info_t test_heap;
extern unsigned test_heap_queries;
inline void heap_caps_get_info(multi_heap_info_t *info, unsigned caps) {
    assert(caps == MALLOC_CAP_8BIT);
    ++test_heap_queries;
    *info = test_heap;
}
