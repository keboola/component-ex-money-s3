import pytest
from keboola.component.exceptions import UserException

from client import MoneyS3Client, repair_cp1250


def test_repair_cp1250_fixes_czech_caron_r():
    assert repair_cp1250("hospodáøství") == "hospodářství"
    assert repair_cp1250("pøepravné") == "přepravné"
    # 'š' (which already survives the server's cp1252 round-trip) stays correct
    assert repair_cp1250("odvody vedlejších hospodáøství") == "odvody vedlejších hospodářství"


def test_repair_cp1250_leaves_ascii_and_correct_text_untouched():
    assert repair_cp1250("CODE-123") == "CODE-123"
    # genuinely-correct 'ř' (U+0159) isn't representable in cp1252 -> guard leaves it alone
    assert repair_cp1250("přeprava") == "přeprava"
    assert repair_cp1250("") == ""


def test_repair_cp1250_recurses_through_dicts_and_lists():
    payload = {"items": [{"description": "pøepravné", "code": "0417", "n": 5, "ok": None}]}
    assert repair_cp1250(payload) == {"items": [{"description": "přepravné", "code": "0417", "n": 5, "ok": None}]}


class _FakeToken:
    def get_token(self):
        return "TOK"


# keboola HttpClient.post returns the already-parsed JSON body (a dict), so the mock
# returns raw dicts — NOT an object with a .json() method.
def _client(mocker, responses):
    c = MoneyS3Client("http://h:28085", _FakeToken(), "ag-1")
    mocker.patch.object(c._http, "post", side_effect=list(responses))
    return c


def test_execute_sends_bearer_and_agenda(mocker):
    c = MoneyS3Client("http://h", _FakeToken(), "ag-1")
    post = mocker.patch.object(c._http, "post", return_value={"data": {"x": 1}})
    data = c.execute("query { x }")
    assert data == {"x": 1}
    _, kwargs = post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer TOK"
    assert kwargs["headers"]["AgendaId"] == "ag-1"


def test_execute_raises_on_graphql_errors(mocker):
    c = _client(mocker, [{"errors": [{"message": "License invalid"}], "data": None}])
    with pytest.raises(UserException, match="License invalid"):
        c.execute("query { x }")


def test_execute_no_agenda_when_disabled(mocker):
    c = MoneyS3Client("http://h", _FakeToken(), "ag-1")
    post = mocker.patch.object(c._http, "post", return_value={"data": {}})
    c.execute("query { x }", with_agenda=False)
    assert "AgendaId" not in post.call_args.kwargs["headers"]


def test_paginate_follows_has_next_page(mocker):
    page1 = {"data": {"ep": {"items": [{"id": 1}, {"id": 2}], "pageInfo": {"hasNextPage": True}}}}
    page2 = {"data": {"ep": {"items": [{"id": 3}], "pageInfo": {"hasNextPage": False}}}}
    c = _client(mocker, [page1, page2])
    items = list(c.paginate("query ($skip:Int!,$take:Int!){ ep }", "ep", page_size=2))
    assert [i["id"] for i in items] == [1, 2, 3]


def test_type_fields_is_cached_across_calls(mocker):
    # The schema is constant for the lifetime of a run, so a given type must be introspected once.
    # Without caching, resolving an endpoint re-introspects each nested object type once per
    # sub-field — hundreds of redundant requests for a single table.
    c = MoneyS3Client("http://h", _FakeToken(), "ag-1")
    post = mocker.patch.object(c._http, "post", return_value={"data": {"__type": {"fields": [{"name": "x"}]}}})
    first = c.type_fields("SomeType")
    second = c.type_fields("SomeType")
    assert first == second == [{"name": "x"}]
    assert post.call_count == 1  # second call served from cache
    c.type_fields("OtherType")
    assert post.call_count == 2  # a different type still hits the API


def test_query_roots_and_input_fields_are_cached(mocker):
    c = MoneyS3Client("http://h", _FakeToken(), "ag-1")
    roots = {"data": {"__schema": {"queryType": {"fields": [{"name": "ep"}]}}}}
    inputs = {"data": {"__type": {"inputFields": [{"name": "lastChange"}]}}}
    post = mocker.patch.object(c._http, "post", side_effect=[roots, inputs])
    assert c.query_roots() == c.query_roots() == [{"name": "ep"}]
    assert c.input_fields("F") == c.input_fields("F") == [{"name": "lastChange"}]
    assert post.call_count == 2  # one request each, despite two calls apiece


def test_endpoint_meta_resolves_item_and_filter_types(mocker):
    introspection = {
        "data": {
            "__schema": {
                "queryType": {
                    "fields": [
                        {
                            "name": "issuedInvoices",
                            "args": [
                                {
                                    "name": "where",
                                    "type": {
                                        "kind": "INPUT_OBJECT",
                                        "name": "IIssuedInvoiceFilterInput",
                                        "ofType": None,
                                    },
                                }
                            ],
                            "type": {"kind": "OBJECT", "name": "IssuedInvoicesCollectionSegment", "ofType": None},
                        }
                    ]
                }
            }
        }
    }
    seg = {
        "data": {
            "__type": {
                "fields": [
                    {"name": "items", "type": {"kind": "LIST", "ofType": {"kind": "OBJECT", "name": "IIssuedInvoice"}}}
                ]
            }
        }
    }
    c = _client(mocker, [introspection, seg])
    item_type, filter_type = c.endpoint_meta("issuedInvoices")
    assert item_type == "IIssuedInvoice"
    assert filter_type == "IIssuedInvoiceFilterInput"
