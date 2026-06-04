from query_builder import ChildSelection
from schema import classify_fields, date_filter_field_names, detect_pk, resolve_selection

FIELDS = [
    {"name": "id", "type": {"kind": "NON_NULL", "ofType": {"kind": "SCALAR", "name": "Int"}}},
    {"name": "documentNumber", "type": {"kind": "SCALAR", "name": "String", "ofType": None}},
    {"name": "currency", "type": {"kind": "OBJECT", "name": "ICurrency", "ofType": None}},
    {"name": "items", "type": {"kind": "LIST", "ofType": {"kind": "OBJECT", "name": "IIssuedInvoiceItem"}}},
]


def test_classify_fields_splits_kinds():
    scalars, objects, lists = classify_fields(FIELDS)
    assert set(scalars) == {"id", "documentNumber"}
    assert objects == {"currency": "ICurrency"}
    assert lists == {"items": "IIssuedInvoiceItem"}


def test_detect_pk_prefers_guid_then_id():
    # guid wins over id (id is not unique in Money S3); id is the fallback.
    assert detect_pk(["documentNumber", "id", "guid"]) == "guid"
    assert detect_pk(["id", "documentNumber"]) == "id"
    assert detect_pk(["code", "name"]) is None


def test_date_filter_field_names():
    filter_fields = [
        {"name": "and", "type": {"kind": "LIST", "ofType": {"kind": "INPUT_OBJECT", "name": "X"}}},
        {"name": "lastChangeDate", "type": {"kind": "INPUT_OBJECT", "name": "DateOnlyFilterInput", "ofType": None}},
        {"name": "dateOfIssue", "type": {"kind": "INPUT_OBJECT", "name": "DateOperationFilterInput", "ofType": None}},
        {
            "name": "documentNumber",
            "type": {"kind": "INPUT_OBJECT", "name": "EqualNonEqualStringOperationFilterInput", "ofType": None},
        },
    ]
    assert date_filter_field_names(filter_fields) == ["lastChangeDate", "dateOfIssue"]


class _FakeClient:
    """Returns type_fields keyed by type name."""

    def __init__(self, fields_by_type):
        self._fields = fields_by_type

    def endpoint_meta(self, endpoint):
        return "IIssuedInvoice", "IIssuedInvoiceFilterInput"

    def type_fields(self, type_name):
        return self._fields[type_name]


def _invoice_fields():
    return {
        "IIssuedInvoice": [
            {"name": "id", "type": {"kind": "NON_NULL", "ofType": {"kind": "SCALAR", "name": "Int"}}},
            {"name": "documentNumber", "type": {"kind": "SCALAR", "name": "String", "ofType": None}},
            {"name": "lastChangeDate", "type": {"kind": "NON_NULL", "ofType": {"kind": "SCALAR", "name": "Date"}}},
            {"name": "currency", "type": {"kind": "OBJECT", "name": "ICurrency", "ofType": None}},
            {"name": "items", "type": {"kind": "LIST", "ofType": {"kind": "OBJECT", "name": "IIssuedInvoiceItem"}}},
        ],
        "ICurrency": [{"name": "code", "type": {"kind": "SCALAR", "name": "String", "ofType": None}}],
        "IIssuedInvoiceItem": [
            {"name": "sequence", "type": {"kind": "NON_NULL", "ofType": {"kind": "SCALAR", "name": "Int"}}},
            {"name": "description", "type": {"kind": "SCALAR", "name": "String", "ofType": None}},
        ],
    }


def test_resolve_selection_full():
    client = _FakeClient(_invoice_fields())
    sel = resolve_selection(
        client, "issuedInvoices", ["documentNumber", "currency.code", "items"], incremental_field=None, override_pk=None
    )
    assert sel.pk_field == "id"
    assert "id" in sel.scalars and "documentNumber" in sel.scalars  # pk force-included
    assert sel.nested == {"currency": ["code"]}
    assert isinstance(sel.lists["items"], ChildSelection)
    assert sel.lists["items"].pk_field == "sequence"
    assert set(sel.lists["items"].scalars) == {"sequence", "description"}


def test_resolve_selection_forces_incremental_field():
    client = _FakeClient(_invoice_fields())
    sel = resolve_selection(
        client, "issuedInvoices", ["documentNumber"], incremental_field="lastChangeDate", override_pk=None
    )
    assert "lastChangeDate" in sel.scalars


def test_resolve_selection_override_pk():
    client = _FakeClient(_invoice_fields())
    sel = resolve_selection(
        client, "issuedInvoices", ["documentNumber"], incremental_field=None, override_pk=["documentNumber"]
    )
    assert sel.pk_field == "documentNumber"
