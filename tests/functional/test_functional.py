"""
Functional datadir test for the Money S3 GraphQL extractor.

Records real HTTP interactions (token fetch + GraphQL introspection + data page) into a
VCR cassette (tests/functional/cassettes/issued_invoices.yaml) with all secrets scrubbed,
then replays offline (record_mode=none) so pytest passes with no network and no real
credentials.

VCR matching strategy:
  - All requests: method + scheme + host + port + path + query + body
  - This uniquely identifies each interaction because:
      * The token POST: distinct path (/connect/token) and body (form data)
      * Each GraphQL POST: distinct body (different query / variables)
  - Authorization and AgendaId headers are NOT part of matching
    (they were stripped from the cassette and would differ from placeholder values).
"""

import sys
import unittest
from pathlib import Path

import vcr
from keboola.datadirtest import TestDataDir

FUNCTIONAL_DIR = Path(__file__).parent
CASSETTES_DIR = FUNCTIONAL_DIR / "cassettes"
COMPONENT_SCRIPT = (FUNCTIONAL_DIR.parent.parent / "src" / "component.py").as_posix()

# Ensure src/ is on the path (datadirtest uses runpy.run_path, but vcr is set up
# here in the test process, which intercepts requests made by the component).
sys.path.insert(0, str(FUNCTIONAL_DIR.parent.parent / "src"))


def _make_vcr():
    """Return a configured VCR instance for offline replay."""
    return vcr.VCR(
        cassette_library_dir=str(CASSETTES_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path", "query", "body"],
    )


class VCRTestDataDir(TestDataDir):
    """TestDataDir subclass that wraps the component run inside a VCR cassette."""

    def __init__(self, *args, vcr_cassette: str, **kwargs):
        self._vcr_cassette = vcr_cassette
        super().__init__(*args, **kwargs)

    def run_component(self):
        """Run the component with VCR active so HTTP is intercepted."""
        my_vcr = _make_vcr()
        with my_vcr.use_cassette(self._vcr_cassette):
            super().run_component()


class TestFunctional(unittest.TestCase):
    """Discovers and runs all functional datadir tests with VCR replay."""

    def test_issued_invoices(self):
        test = VCRTestDataDir(
            data_dir=str(FUNCTIONAL_DIR / "issued_invoices"),
            component_script=COMPONENT_SCRIPT,
            vcr_cassette="issued_invoices.yaml",
        )
        result = unittest.TestResult()
        test.run(result)
        if result.errors:
            raise AssertionError(f"Functional test 'issued_invoices' raised an error:\n{result.errors[0][1]}")
        if result.failures:
            raise AssertionError(f"Functional test 'issued_invoices' failed:\n{result.failures[0][1]}")


if __name__ == "__main__":
    unittest.main()
