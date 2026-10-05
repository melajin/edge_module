"""Check the v2 dashboard feature contract and report-first public entry.

This is a deterministic source/asset check. It does not connect to a board,
start a web server, or prove browser rendering.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    "rms",
    "harmonic1_ratio",
    "harmonic2_ratio",
    "harmonic3_ratio",
    "high_freq_ratio",
]
LEGACY = re.compile(r"harmonic[123]_energy|high_freq_energy")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def html_keys(path: Path) -> list[str]:
    return re.findall(r'\{key:"([^"]+)"', read(path))


def embedded_dashboard(path: Path) -> str:
    text = read(path)
    match = re.search(
        r"static const uint8_t DASHBOARD_HTML_GZ\[\] PROGMEM = \{(.*?)\};",
        text,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"DASHBOARD_HTML_GZ array not found: {path}")
    values = [int(value) for value in re.findall(r"\d+", match.group(1))]
    return gzip.decompress(bytes(values)).decode("utf-8")


def main() -> int:
    c_config = ROOT / "core" / "em_config.c"
    source = ROOT / "viz" / "dashboard.html"
    report_entry = ROOT.parent / "docs" / "index.html"
    legacy_snapshot = ROOT.parent / "docs" / "legacy-v2-dashboard.html"
    header = ROOT / "esp32" / "edge_alimi" / "dashboard_html.h"

    config_text = read(c_config)
    config_match = re.search(
        r"EM_FEATURE_NAMES\[[^]]+\].*?=\s*\{(.*?)\};", config_text, re.DOTALL
    )
    if config_match is None:
        raise AssertionError(f"EM_FEATURE_NAMES array not found: {c_config}")
    c_names = re.findall(r'"([^"]+)"', config_match.group(1))
    if c_names != EXPECTED:
        raise AssertionError(f"C feature names differ: {c_names}")

    source_text = read(source)
    legacy_text = read(legacy_snapshot)
    report_text = read(report_entry)
    if html_keys(source) != EXPECTED:
        raise AssertionError(f"Dashboard source keys differ: {html_keys(source)}")
    if html_keys(legacy_snapshot) != EXPECTED:
        raise AssertionError(f"Legacy dashboard snapshot keys differ: {html_keys(legacy_snapshot)}")
    if LEGACY.search(source_text) or LEGACY.search(legacy_text):
        raise AssertionError("Legacy energy key remains in dashboard source/deploy")

    embedded = embedded_dashboard(header)
    if embedded.splitlines() != source_text.splitlines():
        raise AssertionError("Embedded dashboard differs from dashboard source")
    if LEGACY.search(embedded):
        raise AssertionError("Legacy energy key remains in embedded dashboard")

    chapters = [
        "01-system.md",
        "02-physical-evidence.md",
        "03-ml-model.md",
        "04-fault-interpretation.md",
        "05-fg-hardware.md",
        "06-results.md",
        "07-effects-and-limits.md",
        "08-sources.md",
    ]
    missing_chapters = [name for name in chapters if name not in report_text]
    if missing_chapters:
        raise AssertionError(f"Report entry is missing chapter links: {missing_chapters}")
    if "legacy-v2-dashboard.html" not in report_text:
        raise AssertionError("Report entry does not link to the preserved V2 dashboard")

    print("v2 dashboard contract PASS")
    print("  names:", ", ".join(EXPECTED))
    print("  C config, dashboard source, preserved V2 snapshot and embedded asset agree")
    print("  report entry links all eight detail chapters and the preserved V2 snapshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
