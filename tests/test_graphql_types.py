from graphql_types import normalize_field, unwrap_type


def test_unwrap_non_null_scalar():
    t = {"kind": "NON_NULL", "name": None, "ofType": {"kind": "SCALAR", "name": "Int", "ofType": None}}
    kind, name, is_list = unwrap_type(t)
    assert (kind, name, is_list) == ("SCALAR", "Int", False)


def test_unwrap_list_object():
    t = {"kind": "LIST", "name": None, "ofType": {"kind": "OBJECT", "name": "IIssuedInvoiceItem", "ofType": None}}
    kind, name, is_list = unwrap_type(t)
    assert (kind, name, is_list) == ("OBJECT", "IIssuedInvoiceItem", True)


def test_normalize_field_scalar():
    f = {"name": "documentNumber", "type": {"kind": "SCALAR", "name": "String", "ofType": None}}
    nf = normalize_field(f)
    assert nf.name == "documentNumber"
    assert nf.is_scalar and not nf.is_object and not nf.is_list


def test_normalize_field_enum_is_scalar():
    f = {
        "name": "type",
        "type": {"kind": "NON_NULL", "name": None, "ofType": {"kind": "ENUM", "name": "ItemType", "ofType": None}},
    }
    assert normalize_field(f).is_scalar


def test_normalize_field_object_and_list():
    obj = {"name": "currency", "type": {"kind": "OBJECT", "name": "ICurrency", "ofType": None}}
    lst = {
        "name": "items",
        "type": {
            "kind": "LIST",
            "name": None,
            "ofType": {"kind": "OBJECT", "name": "IIssuedInvoiceItem", "ofType": None},
        },
    }
    assert normalize_field(obj).is_object and not normalize_field(obj).is_list
    nf = normalize_field(lst)
    assert nf.is_list and nf.type_name == "IIssuedInvoiceItem"
