import pytest
from keboola.component.exceptions import UserException

from configuration import Configuration


def _base():
    return {
        "base_url": "http://localhost:28085",
        "client_id": "cid",
        "#client_secret": "secret",
        "agenda_id": "ag-1",
        "endpoint": "issuedInvoices",
        "fields": ["id", "documentNumber"],
    }


def test_parses_minimal_with_defaults():
    c = Configuration(**_base())
    assert c.load_type == "full_load"
    assert c.incremental_field == "lastChangeDate"
    assert c.client_secret == "secret"
    assert c.destination.table_name is None


def test_incremental_and_destination():
    data = _base() | {
        "load_type": "incremental_load",
        "date_from": "2024-01-01",
        "destination": {"table_name": "inv", "primary_key": ["id"]},
    }
    c = Configuration(**data)
    assert c.is_incremental is True
    assert c.destination.primary_key == ["id"]


def test_missing_required_raises_userexception():
    with pytest.raises(UserException):
        Configuration(base_url="x")  # missing client_id etc.


def test_empty_incremental_field_with_incremental_load_raises():
    data = _base() | {"load_type": "incremental_load", "incremental_field": ""}
    with pytest.raises(UserException, match="incremental_field"):
        Configuration(**data)


def test_empty_incremental_field_with_date_bound_raises():
    data = _base() | {"incremental_field": "", "date_from": "2024-01-01"}
    with pytest.raises(UserException, match="incremental_field"):
        Configuration(**data)


def test_empty_incremental_field_is_fine_for_plain_full_load():
    Configuration(**(_base() | {"incremental_field": ""}))  # no filter built → no error
