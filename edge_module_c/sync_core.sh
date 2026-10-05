#!/bin/sh
# Legacy V2 sync: core/ → esp32/edge_alimi/; preserve the historical shared-core path.
# The report-default ADXL345 + FG build owns its independent src/ directory.
cp core/*.c core/*.h esp32/edge_alimi/
echo "synced legacy V2 core: $(ls core/*.c core/*.h | wc -l) files"
