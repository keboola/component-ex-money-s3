from query_builder import ChildSelection, Selection
from writer import (
    child_columns,
    child_schema,
    extract_children,
    flatten_parent,
    normalize_columns,
    parent_columns,
    parent_schema,
)


def _base_type(coldef):
    return coldef.data_types["base"].dtype


def test_parent_schema_maps_graphql_types_to_base_types():
    sel = Selection(
        endpoint="e",
        pk_fields=["guid"],
        scalars=["guid", "id", "total", "issued", "paid"],
        nested={},
        lists={},
        column_types={"guid": "UUID", "id": "Int", "total": "Decimal", "issued": "Date", "paid": "Boolean"},
    )
    schema = parent_schema(sel)
    assert _base_type(schema["guid"]) == "STRING" and schema["guid"].primary_key is True
    assert _base_type(schema["id"]) == "INTEGER"
    assert _base_type(schema["total"]) == "NUMERIC"
    assert _base_type(schema["issued"]) == "DATE"
    assert _base_type(schema["paid"]) == "BOOLEAN"


def test_child_schema_types_fk_index_and_scalars():
    sel = Selection(
        endpoint="e",
        pk_fields=["guid"],
        scalars=["guid"],
        nested={},
        lists={"rows": ChildSelection(["amount"], pk_field=None, column_types={"amount": "Decimal"})},
        column_types={"guid": "UUID"},
    )
    schema = child_schema(sel, "rows")
    assert _base_type(schema["e_guid"]) == "STRING"  # FK takes the parent UUID pk type
    assert _base_type(schema["index"]) == "INTEGER"  # synthetic row index
    assert _base_type(schema["amount"]) == "NUMERIC"


def test_normalize_columns_strips_leading_underscore():
    # The platform webalizes '_index' -> 'index'; we do the same so the manifest matches.
    assert normalize_columns(["_index"]) == ["index"]
    assert normalize_columns(["issuedInvoices_guid", "currency_code", "documentNumber"]) == [
        "issuedInvoices_guid",
        "currency_code",
        "documentNumber",
    ]


def test_normalize_columns_replaces_forbidden_chars():
    assert normalize_columns(["a b", "x.y", "ok_1"]) == ["a_b", "xy", "ok_1"]


SEL = Selection(
    endpoint="issuedInvoices",
    pk_fields=["id"],
    scalars=["id", "documentNumber"],
    nested={"currency": ["code"], "partnerAddress": ["identificationNumber"]},
    lists={
        "vatRateSummary": ChildSelection(["vatRate", "totalVat"], pk_field=None),
        "items": ChildSelection(["sequence", "description"], pk_field="sequence"),
    },
)

ITEM = {
    "id": 12,
    "documentNumber": "1026012",
    "currency": None,
    "partnerAddress": {"identificationNumber": "123"},
    "vatRateSummary": [{"vatRate": 21.0, "totalVat": 0.0}, {"vatRate": 12.0, "totalVat": 1.0}],
    "items": [{"sequence": 1, "description": "A"}],
}


def test_parent_columns():
    assert parent_columns(SEL) == ["id", "documentNumber", "currency_code", "partnerAddress_identificationNumber"]


def test_flatten_parent_flattens_nested_and_handles_null():
    row = flatten_parent(ITEM, SEL)
    assert row == {
        "id": 12,
        "documentNumber": "1026012",
        "currency_code": None,
        "partnerAddress_identificationNumber": "123",
    }


def test_child_columns_index_fallback_and_sequence():
    assert child_columns(SEL, "vatRateSummary") == ["issuedInvoices_id", "_index", "vatRate", "totalVat"]
    assert child_columns(SEL, "items") == ["issuedInvoices_id", "sequence", "description"]


def test_extract_children_builds_fk_and_keys():
    children = extract_children(ITEM, SEL)
    assert children["vatRateSummary"] == [
        {"issuedInvoices_id": 12, "_index": 0, "vatRate": 21.0, "totalVat": 0.0},
        {"issuedInvoices_id": 12, "_index": 1, "vatRate": 12.0, "totalVat": 1.0},
    ]
    assert children["items"] == [{"issuedInvoices_id": 12, "sequence": 1, "description": "A"}]


def test_extract_children_empty_list():
    item = dict(ITEM, vatRateSummary=[], items=[])
    children = extract_children(item, SEL)
    assert children == {"vatRateSummary": [], "items": []}


def test_composite_parent_pk_emits_multiple_fk_columns():
    sel = Selection(
        endpoint="docs",
        pk_fields=["documentNumber", "periodYear"],
        scalars=["documentNumber", "periodYear"],
        nested={},
        lists={"lines": ChildSelection(["amount"], pk_field=None)},
    )
    assert child_columns(sel, "lines") == ["docs_documentNumber", "docs_periodYear", "_index", "amount"]
    item = {"documentNumber": "D1", "periodYear": 2026, "lines": [{"amount": 5}]}
    assert extract_children(item, sel)["lines"] == [
        {"docs_documentNumber": "D1", "docs_periodYear": 2026, "_index": 0, "amount": 5}
    ]


def test_no_parent_pk_omits_fk_column():
    # When the parent has no pk_field, child tables must NOT emit a bogus "<endpoint>_None"
    # column — neither in the column list nor in the row data.
    sel = Selection(
        endpoint="currencies",
        pk_fields=[],
        scalars=["code"],
        nested={},
        lists={"rates": ChildSelection(["rate"], pk_field=None)},
    )
    assert child_columns(sel, "rates") == ["_index", "rate"]
    item = {"code": "EUR", "rates": [{"rate": 1.0}, {"rate": 2.0}]}
    assert extract_children(item, sel)["rates"] == [
        {"_index": 0, "rate": 1.0},
        {"_index": 1, "rate": 2.0},
    ]
