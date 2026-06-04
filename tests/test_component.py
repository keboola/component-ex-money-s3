import os
import tempfile
import unittest
from unittest.mock import MagicMock

from keboola.component.exceptions import UserException

from component import Component, legislation_incompatible_members, max_incremental_value
from query_builder import ChildSelection, Selection

_FULL_LOAD = type("C", (), {"is_incremental": False})()


def _fake_table(directory, filename):
    table = MagicMock()
    table.full_path = os.path.join(directory, filename)
    table.name = filename
    return table


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


class TestEmptyTableSkipping(unittest.TestCase):
    def test_parent_with_no_rows_writes_nothing(self):
        comp = Component.__new__(Component)
        comp.create_out_table_definition = MagicMock()
        comp.write_manifest = MagicMock()
        sel = Selection(endpoint="e", pk_field="guid", scalars=["guid"], nested={}, lists={})
        comp._write_parent("e", sel, [], _FULL_LOAD)
        comp.create_out_table_definition.assert_not_called()
        comp.write_manifest.assert_not_called()

    def test_parent_with_rows_writes_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            comp = Component.__new__(Component)
            table = _fake_table(d, "e.csv")
            comp.create_out_table_definition = MagicMock(return_value=table)
            comp.write_manifest = MagicMock()
            sel = Selection(endpoint="e", pk_field="guid", scalars=["guid"], nested={}, lists={})
            comp._write_parent("e", sel, [{"guid": "g1"}], _FULL_LOAD)
            comp.write_manifest.assert_called_once()
            self.assertTrue(os.path.exists(table.full_path))

    def test_empty_child_table_skipped_and_file_removed(self):
        with tempfile.TemporaryDirectory() as d:
            comp = Component.__new__(Component)
            comp.create_out_table_definition = MagicMock(side_effect=lambda fname, **kw: _fake_table(d, fname))
            comp.write_manifest = MagicMock()
            sel = Selection(
                endpoint="e",
                pk_field="guid",
                scalars=["guid"],
                nested={},
                lists={
                    "full": ChildSelection(["a"], pk_field=None),
                    "empty": ChildSelection(["b"], pk_field=None),
                },
            )
            rows = [{"guid": "g1", "full": [{"a": 1}], "empty": []}]
            comp._write_children("e", sel, rows, _FULL_LOAD)

            manifested = {c.args[0].name for c in comp.write_manifest.call_args_list}
            self.assertEqual(manifested, {"e_full.csv"})  # only the non-empty child
            self.assertTrue(os.path.exists(os.path.join(d, "e_full.csv")))
            self.assertFalse(os.path.exists(os.path.join(d, "e_empty.csv")))  # empty CSV removed


if __name__ == "__main__":
    unittest.main()
