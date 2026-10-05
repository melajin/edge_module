"""Compile report V3 C policy and compare it with the bundled Python reference.

This checks policy inputs and states; signal math has a separate C synthetic
test in test_signal.c. Neither test measures a physical device.
"""
from __future__ import annotations

import ctypes as C
import gc
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
EDGE_MODULE = PROJECT.parents[2]
sys.path.insert(0, str(EDGE_MODULE / "src" / "report_reference"))
from aihub_training.v3_diagnostic import ImbalanceEvidence, InstantEvidence, V3Diagnostic  # noqa: E402


class Instant(C.Structure):
    _fields_ = [("valid", C.c_bool), ("flag", C.c_bool)]


class Imbalance(C.Structure):
    _fields_ = [("rpm_available", C.c_bool), ("baseline_available", C.c_bool),
                ("amplitude_present", C.c_bool), ("phase_present", C.c_bool),
                ("amplitude_ratio_1x", C.c_float), ("phase_concentration", C.c_float)]


class Input(C.Structure):
    _fields_ = [("bearing", Instant), ("misalignment", Instant),
                ("belt", Instant), ("imbalance", Imbalance)]


class Fault(C.Structure):
    _fields_ = [("status", C.c_int), ("votes_positive", C.c_uint8),
                ("votes_valid", C.c_uint8), ("instant_valid", C.c_bool),
                ("instant_flag", C.c_bool)]


class Result(C.Structure):
    _fields_ = [(name, Fault) for name in ("bearing", "misalignment", "belt", "imbalance")]
    _fields_.append(("imbalance_auto_confirmation_allowed", C.c_bool))


class History(C.Structure):
    _fields_ = [("bits", C.c_uint8 * 5), ("count", C.c_uint8),
                ("next", C.c_uint8), ("positive", C.c_uint8)]


class Engine(C.Structure):
    _fields_ = [(name, History) for name in ("bearing", "misalignment", "belt", "imbalance")]
    _fields_ += [("imbalance_amplitude_ratio", C.c_float),
                 ("imbalance_phase_concentration", C.c_float)]


STATUS = ["unavailable", "normal", "confirmed", "suspected", "inspection_required"]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="edge-alimi-v3-parity-") as tmp:
        libpath = Path(tmp) / ("v3.dll" if sys.platform == "win32" else "v3.so")
        subprocess.run([
            "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", "-shared",
            "-Isrc", "-o", str(libpath), str(PROJECT / "src" / "em_v3.c"), "-lm",
        ], cwd=PROJECT, check=True)
        lib = C.CDLL(str(libpath))
        lib.em_v3_init.argtypes = [C.POINTER(Engine), C.c_float, C.c_float]
        lib.em_v3_init.restype = C.c_bool
        lib.em_v3_reset.argtypes = [C.POINTER(Engine)]
        lib.em_v3_update.argtypes = [C.POINTER(Engine), C.POINTER(Input), C.POINTER(Result)]
        lib.em_v3_update.restype = C.c_bool
        c = Engine()
        p = V3Diagnostic()
        assert lib.em_v3_init(C.byref(c), 1.1, 0.0)
        for amp, phase in [(0, 0), (-1, 0), (math.nan, 0), (1, math.inf), (1, 1.1)]:
            assert not lib.em_v3_init(C.byref(Engine()), amp, phase)
        rng = random.Random(20260923)
        cases = [(True, True, False, 1.2, 0.8)] * 5
        cases += [(None, None, None, None, None)]
        cases += [(False, True, True, 1.1, 0.0)] * 6
        cases += [(True, False, True, 1.099, 0.8)] * 6
        cases += [(rng.choice([True, False, None]), rng.choice([True, False, None]),
                   rng.choice([True, False, None]), rng.choice([1.0, 1.2, None]),
                   rng.choice([0.0, 0.8, None])) for _ in range(200)]
        for index, (b, m, belt, amp, phase) in enumerate(cases):
            if index in (30, 130):
                lib.em_v3_reset(C.byref(c))
                p.reset_session("fan", "s1")
            ci = Input(Instant(b is not None, bool(b)), Instant(m is not None, bool(m)),
                       Instant(belt is not None, bool(belt)),
                       Imbalance(True, True, amp is not None, phase is not None,
                                 amp or 0.0, phase or 0.0))
            cr = Result()
            assert lib.em_v3_update(C.byref(c), C.byref(ci), C.byref(cr))
            pr = p.update(equipment_id="fan", session_id="s1",
                          bearing=InstantEvidence(b is not None, b),
                          misalignment=InstantEvidence(m is not None, m),
                          belt=InstantEvidence(belt is not None, belt),
                          imbalance=ImbalanceEvidence(True, True, amp, phase))
            for name in pr:
                cf, pf = getattr(cr, name), pr[name]
                assert (STATUS[cf.status], cf.votes_positive, cf.votes_valid) == (
                    pf.status, pf.votes_positive, pf.votes_valid), (index, name, cf, pf)
            assert not cr.imbalance_auto_confirmation_allowed
        before = [(getattr(c, name).count, getattr(c, name).positive)
                  for name in ("bearing", "misalignment", "belt", "imbalance")]
        invalid = Input()
        cr = Result()
        assert lib.em_v3_update(C.byref(c), C.byref(invalid), C.byref(cr))
        pr = p.update(equipment_id="fan", session_id="s1",
                      bearing=InstantEvidence(False), misalignment=InstantEvidence(False),
                      belt=InstantEvidence(False), imbalance=ImbalanceEvidence(False, False))
        for name in pr:
            assert STATUS[getattr(cr, name).status] == pr[name].status == "unavailable"
        assert before == [(getattr(c, name).count, getattr(c, name).positive)
                          for name in ("bearing", "misalignment", "belt", "imbalance")]
        before = (c.bearing.count, c.bearing.positive)
        for field, value in (("amplitude_ratio_1x", math.nan), ("amplitude_ratio_1x", math.inf),
                             ("amplitude_ratio_1x", -1), ("phase_concentration", math.nan),
                             ("phase_concentration", 1.01)):
            ci = Input(Instant(True, True), Instant(), Instant(), Imbalance(True, True, True, True, 1.2, 0.8))
            setattr(ci.imbalance, field, value)
            assert not lib.em_v3_update(C.byref(c), C.byref(ci), C.byref(Result()))
            assert (c.bearing.count, c.bearing.positive) == before
        # Windows keeps a loaded DLL locked until its native handle is released.
        if sys.platform == "win32":
            handle = lib._handle
            lib._handle = 0
            free_library = C.windll.kernel32.FreeLibrary
            free_library.argtypes = [C.c_void_p]
            free_library.restype = C.c_int
            assert free_library(C.c_void_p(handle))
        del lib
        gc.collect()
    print(f"PASS: {len(cases) + 1} Python/C aligned updates plus reset, partial-input and threshold checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
