"""Functional datadir tests for the Money S3 GraphQL extractor.

Each subdirectory of this folder that contains `source/data/config.json` is one endpoint
fixture, replayed offline against the VCR cassette `cassettes/<endpoint>.yaml`. The cassettes
were recorded against the live test instance with all secrets scrubbed (see
`record_cassettes.py`) and replay with `record_mode="none"`, so the suite needs no network
and no real credentials.

VCR matching: method + scheme + host + port + path + query + body (auth/agenda headers are
not matched — they were scrubbed). `allow_playback_repeats=True` because a run re-issues some
introspection queries (e.g. the same type lookup) more than once.
"""

import sys
import unittest
from pathlib import Path

import pytest
import vcr
from keboola.datadirtest import TestDataDir

FUNCTIONAL_DIR = Path(__file__).parent
CASSETTES_DIR = FUNCTIONAL_DIR / "cassettes"
COMPONENT_SCRIPT = (FUNCTIONAL_DIR.parent.parent / "src" / "component.py").as_posix()

sys.path.insert(0, str(FUNCTIONAL_DIR.parent.parent / "src"))


def _endpoints() -> list[str]:
    return sorted(p.parent.parent.parent.name for p in FUNCTIONAL_DIR.glob("*/source/data/config.json"))


def _make_vcr() -> vcr.VCR:
    return vcr.VCR(
        cassette_library_dir=str(CASSETTES_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path", "query", "body"],
    )


class VCRTestDataDir(TestDataDir):
    """TestDataDir subclass that runs the component inside a VCR cassette."""

    def __init__(self, *args, vcr_cassette: str, **kwargs):
        self._vcr_cassette = vcr_cassette
        super().__init__(*args, **kwargs)

    def run_component(self):
        with _make_vcr().use_cassette(self._vcr_cassette, allow_playback_repeats=True):
            super().run_component()


@pytest.mark.parametrize("endpoint", _endpoints())
def test_endpoint(endpoint):
    test = VCRTestDataDir(
        data_dir=str(FUNCTIONAL_DIR / endpoint),
        component_script=COMPONENT_SCRIPT,
        vcr_cassette=f"{endpoint}.yaml",
    )
    result = unittest.TestResult()
    test.run(result)
    if result.errors:
        raise AssertionError(f"Functional test '{endpoint}' raised an error:\n{result.errors[0][1]}")
    if result.failures:
        raise AssertionError(f"Functional test '{endpoint}' failed:\n{result.failures[0][1]}")
