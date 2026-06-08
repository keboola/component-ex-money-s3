import pytest
from keboola.component.exceptions import UserException

from query_builder import ChildSelection, Selection
from schema import all_field_paths, classify_fields, date_filter_field_names, detect_pk, prune_fields, resolve_selection

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
    # name-only fallback: guid wins over id (id is not unique in Money S3); id is the fallback.
    assert detect_pk(["documentNumber", "id", "guid"]) == "guid"
    assert detect_pk(["id", "documentNumber"]) == "id"
    assert detect_pk(["code", "name"]) is None


def test_detect_pk_prefers_uuid_typed_field_by_metadata():
    types = {"documentNumber": "String", "rowKey": "UUID", "id": "Int"}
    # a UUID-typed scalar is chosen even when it is not named "guid"
    assert detect_pk(["documentNumber", "rowKey", "id"], types) == "rowKey"


def test_detect_pk_guid_wins_among_multiple_uuid_fields():
    types = {"otherUuid": "UUID", "guid": "UUID", "id": "Int"}
    assert detect_pk(["otherUuid", "guid", "id"], types) == "guid"


def test_detect_pk_falls_back_to_id_when_no_uuid():
    types = {"documentNumber": "String", "id": "Int"}
    assert detect_pk(["documentNumber", "id"], types) == "id"


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
    assert sel.pk_fields == ["id"]
    assert "id" in sel.scalars and "documentNumber" in sel.scalars  # pk force-included
    assert sel.nested == {"currency": ["code"]}
    assert isinstance(sel.lists["items"], ChildSelection)
    assert sel.lists["items"].pk_field == "sequence"
    assert set(sel.lists["items"].scalars) == {"sequence", "description"}


def test_resolve_selection_detects_uuid_pk():
    fields = {
        "IIssuedInvoice": [
            {"name": "guid", "type": {"kind": "NON_NULL", "ofType": {"kind": "SCALAR", "name": "UUID"}}},
            {"name": "id", "type": {"kind": "NON_NULL", "ofType": {"kind": "SCALAR", "name": "Int"}}},
            {"name": "documentNumber", "type": {"kind": "SCALAR", "name": "String", "ofType": None}},
        ],
    }
    sel = resolve_selection(
        _FakeClient(fields), "issuedInvoices", ["documentNumber"], incremental_field=None, override_pk=None
    )
    assert sel.pk_fields == ["guid"]  # UUID-typed field chosen over the (non-unique) Int id


def test_resolve_selection_forces_incremental_field():
    client = _FakeClient(_invoice_fields())
    sel = resolve_selection(
        client, "issuedInvoices", ["documentNumber"], incremental_field="lastChangeDate", override_pk=None
    )
    assert "lastChangeDate" in sel.scalars


def test_all_field_paths_includes_scalars_nested_and_lists():
    client = _FakeClient(_invoice_fields())
    paths = all_field_paths(client, "issuedInvoices")
    assert "id" in paths and "documentNumber" in paths and "lastChangeDate" in paths
    assert "currency.code" in paths  # nested object scalar
    assert "items" in paths  # list -> child table
    # objects themselves are not selectable directly (only their sub-scalars)
    assert "currency" not in paths


def test_prune_fields_removes_scalars_nested_and_list_members():
    sel = Selection(
        endpoint="issuedInvoices",
        pk_fields=["guid"],
        scalars=["guid", "documentNumber", "originalDocumentNumber"],
        nested={"partnerAddress": ["identificationNumber", "vatIdentificationNumberSk"]},
        lists={"items": ChildSelection(["sequence", "description"], pk_field="sequence")},
    )
    pruned = prune_fields(sel, {"originalDocumentNumber", "vatIdentificationNumberSk"})
    assert pruned.scalars == ["guid", "documentNumber"]
    assert pruned.nested == {"partnerAddress": ["identificationNumber"]}
    assert pruned.lists["items"].scalars == ["sequence", "description"]
    assert pruned.pk_fields == ["guid"]


def test_prune_fields_drops_emptied_nested_and_whole_list_by_name():
    sel = Selection(
        endpoint="issuedInvoices",
        pk_fields=["guid"],
        scalars=["guid"],
        nested={"myAddress": ["vatIdentificationNumberSk"]},
        lists={"shippingListServices": ChildSelection(["name"], pk_field=None)},
    )
    pruned = prune_fields(sel, {"vatIdentificationNumberSk", "shippingListServices"})
    assert pruned.nested == {}  # nested object emptied -> dropped
    assert pruned.lists == {}  # whole list removed by name


def test_prune_fields_resets_child_pk_when_removed():
    sel = Selection(
        endpoint="x",
        pk_fields=[],
        scalars=["code"],
        nested={},
        lists={"rows": ChildSelection(["sequence", "amount"], pk_field="sequence")},
    )
    pruned = prune_fields(sel, {"sequence"})
    assert pruned.lists["rows"].scalars == ["amount"]
    assert pruned.lists["rows"].pk_field is None


def test_resolve_selection_override_pk():
    client = _FakeClient(_invoice_fields())
    sel = resolve_selection(
        client, "issuedInvoices", ["documentNumber"], incremental_field=None, override_pk=["documentNumber"]
    )
    assert sel.pk_fields == ["documentNumber"]


def test_resolve_selection_composite_override_pk_is_preserved():
    client = _FakeClient(_invoice_fields())
    sel = resolve_selection(
        client, "issuedInvoices", ["documentNumber"], incremental_field=None, override_pk=["id", "documentNumber"]
    )
    assert sel.pk_fields == ["id", "documentNumber"]  # both kept, not truncated to one
    assert "id" in sel.scalars and "documentNumber" in sel.scalars  # both force-included as columns


def test_resolve_selection_invalid_override_pk_raises():
    client = _FakeClient(_invoice_fields())
    with pytest.raises(UserException, match="not found among scalar fields"):
        resolve_selection(
            client, "issuedInvoices", ["documentNumber"], incremental_field=None, override_pk=["doesNotExist"]
        )
