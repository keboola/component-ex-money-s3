import unittest

from keboola.component.exceptions import UserException

from component import Component, legislation_incompatible_members, max_incremental_value
from query_builder import Selection


class TestComponent(unittest.TestCase):
    def test_max_incremental_value(self):
        rows = [{"lastChangeDate": "2024-01-01"}, {"lastChangeDate": "2024-03-02"}, {"lastChangeDate": "2024-02-01"}]
        self.assertEqual(max_incremental_value(rows, "lastChangeDate"), "2024-03-02")

    def test_max_incremental_value_empty(self):
        self.assertIsNone(max_incremental_value([], "lastChangeDate"))

    def test_max_incremental_value_missing_field(self):
        self.assertIsNone(max_incremental_value([{"x": 1}], "lastChangeDate"))


class TestLegislationErrorParsing(unittest.TestCase):
    def test_extracts_member_from_legislation_error(self):
        errors = [
            {
                "message": "Cannot access member 'vatIdentificationNumberSk' on path "
                "'/issuedInvoices/items[0]/partnerAddress'. Member is available only for 'Sk' legislation"
            },
            {"message": "Cannot access member 'insured' on path '/x'. Member is available only for 'Sk' legislation"},
        ]
        self.assertEqual(legislation_incompatible_members(errors), {"vatIdentificationNumberSk", "insured"})

    def test_ignores_non_legislation_errors(self):
        errors = [
            {"message": "Unexpected Execution Error;"},
            {"message": "Cannot access member 'foo' on path '/x'. Some other reason"},
        ]
        self.assertEqual(legislation_incompatible_members(errors), set())


class _ProbeClient:
    """Fakes a single-page endpoint. The first page errors on any still-present
    legislation-incompatible field; once the query is clean it returns one item."""

    def __init__(self, endpoint, bad_members, item):
        self.endpoint = endpoint
        self.bad = set(bad_members)
        self.item = item
        self.first_page_calls = 0

    def execute_with_errors(self, query, variables=None, with_agenda=True):
        self.first_page_calls += 1
        present = [m for m in self.bad if m in query]
        if present:
            return {
                "errors": [
                    {
                        "message": f"Cannot access member '{m}' on path '/e/items[0]'. "
                        f"Member is available only for 'Sk' legislation"
                    }
                    for m in present
                ]
            }
        return {"data": {self.endpoint: {"pageInfo": {"hasNextPage": False}, "items": [self.item]}}}

    def execute(self, query, variables=None, with_agenda=True):  # pragma: no cover - single page
        raise AssertionError("no further pages expected")


class TestFetchRows(unittest.TestCase):
    def _component_with_client(self, client):
        comp = Component.__new__(Component)
        comp._client = client
        return comp

    def test_prunes_incompatible_fields_then_fetches(self):
        sel = Selection(
            endpoint="issuedInvoices",
            pk_field="guid",
            scalars=["guid", "documentNumber", "insured"],
            nested={"partnerAddress": ["identificationNumber", "vatIdentificationNumberSk"]},
            lists={},
        )
        client = _ProbeClient("issuedInvoices", {"insured", "vatIdentificationNumberSk"}, {"guid": "g1"})
        comp = self._component_with_client(client)
        result, rows = comp._fetch_rows(sel, where_clause=None, endpoint="issuedInvoices")
        self.assertEqual(result.scalars, ["guid", "documentNumber"])
        self.assertEqual(result.nested, {"partnerAddress": ["identificationNumber"]})
        self.assertEqual(rows, [{"guid": "g1"}])
        self.assertEqual(client.first_page_calls, 2)  # one failing + one clean

    def test_no_extra_request_when_compatible(self):
        sel = Selection(endpoint="x", pk_field="guid", scalars=["guid"], nested={}, lists={})
        client = _ProbeClient("x", set(), {"guid": "g1"})
        comp = self._component_with_client(client)
        result, rows = comp._fetch_rows(sel, where_clause=None, endpoint="x")
        self.assertEqual(result, sel)
        self.assertEqual(rows, [{"guid": "g1"}])
        self.assertEqual(client.first_page_calls, 1)  # happy path: a single request

    def test_non_legislation_error_is_raised(self):
        sel = Selection(endpoint="x", pk_field="guid", scalars=["guid"], nested={}, lists={})

        class _ErrClient:
            def execute_with_errors(self, query, variables=None, with_agenda=True):
                return {"errors": [{"message": "Unexpected Execution Error;"}]}

        comp = self._component_with_client(_ErrClient())
        with self.assertRaises(UserException):
            comp._fetch_rows(sel, where_clause=None, endpoint="x")


if __name__ == "__main__":
    unittest.main()
