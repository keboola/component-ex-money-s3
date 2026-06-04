from query_builder import ChildSelection, Selection
from writer import child_columns, extract_children, flatten_parent, normalize_columns, parent_columns


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
    pk_field="id",
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
    children = extract_children(ITEM, SEL, parent_pk_value=12)
    assert children["vatRateSummary"] == [
        {"issuedInvoices_id": 12, "_index": 0, "vatRate": 21.0, "totalVat": 0.0},
        {"issuedInvoices_id": 12, "_index": 1, "vatRate": 12.0, "totalVat": 1.0},
    ]
    assert children["items"] == [{"issuedInvoices_id": 12, "sequence": 1, "description": "A"}]


def test_extract_children_empty_list():
    item = dict(ITEM, vatRateSummary=[], items=[])
    children = extract_children(item, SEL, parent_pk_value=12)
    assert children == {"vatRateSummary": [], "items": []}


def test_no_parent_pk_omits_fk_column():
    # When the parent has no pk_field, child tables must NOT emit a bogus "<endpoint>_None"
    # column — neither in the column list nor in the row data.
    sel = Selection(
        endpoint="currencies",
        pk_field=None,
        scalars=["code"],
        nested={},
        lists={"rates": ChildSelection(["rate"], pk_field=None)},
    )
    assert child_columns(sel, "rates") == ["_index", "rate"]
    item = {"code": "EUR", "rates": [{"rate": 1.0}, {"rate": 2.0}]}
    assert extract_children(item, sel, parent_pk_value=None)["rates"] == [
        {"_index": 0, "rate": 1.0},
        {"_index": 1, "rate": 2.0},
    ]
