from query_builder import ChildSelection, Selection, build_query, build_where


def test_build_where_gte_only():
    assert build_where("lastChangeDate", gte="2024-01-01") == {"lastChangeDate": {"gte": "2024-01-01"}}


def test_build_where_range_uses_and():
    w = build_where("lastChangeDate", gte="2024-01-01", lte="2024-02-01")
    assert w == {"and": [{"lastChangeDate": {"gte": "2024-01-01"}}, {"lastChangeDate": {"lte": "2024-02-01"}}]}


def test_build_where_none():
    assert build_where("lastChangeDate") is None


def _selection():
    return Selection(
        endpoint="issuedInvoices",
        pk_fields=["id"],
        scalars=["id", "documentNumber", "lastChangeDate"],
        nested={"currency": ["code"]},
        lists={
            "vatRateSummary": ChildSelection(scalars=["vatRate", "totalVat"], pk_field=None),
            "items": ChildSelection(scalars=["sequence", "description"], pk_field="sequence"),
        },
        filter_type="IIssuedInvoiceFilterInput",
    )


def test_build_query_full_load_structure():
    q = build_query(_selection())
    assert "query ($skip: Int!, $take: Int!)" in q
    assert "issuedInvoices(skip: $skip, take: $take)" in q
    assert "pageInfo { hasNextPage }" in q
    assert "totalCount" in q
    assert "currency { code }" in q
    assert "vatRateSummary { vatRate totalVat }" in q
    assert "items { sequence description }" in q


def test_build_query_never_emits_order_clause():
    # The Money S3 server duplicates rows when any `order` clause is present, so the
    # builder must NEVER emit one, even when a pk_field is set.
    assert "order:" not in build_query(_selection())


def test_build_query_with_where_uses_typed_variable():
    q = build_query(_selection(), with_where=True)
    # the filter travels as a typed $where variable, never interpolated into the query string
    assert "$where: IIssuedInvoiceFilterInput" in q
    assert "where: $where" in q


def test_build_query_without_pk_omits_order():
    sel = Selection(endpoint="currencies", pk_fields=[], scalars=["code"], nested={}, lists={})
    q = build_query(sel)
    assert "order:" not in q
    assert "currencies(skip: $skip, take: $take)" in q
