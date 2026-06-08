from unittest.mock import PropertyMock, patch

import pytest
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import SelectElement

import component as component_module


class _StubClient:
    def query_roots(self):
        return [{"name": "issuedInvoices"}, {"name": "currencies"}]

    def endpoint_meta(self, endpoint):
        return "IIssuedInvoice", "IIssuedInvoiceFilterInput"

    def type_fields(self, type_name):
        return (
            [
                {"name": "id", "type": {"kind": "SCALAR", "name": "Int", "ofType": None}},
                {"name": "currency", "type": {"kind": "OBJECT", "name": "ICurrency", "ofType": None}},
                {"name": "items", "type": {"kind": "LIST", "ofType": {"kind": "OBJECT", "name": "IIssuedInvoiceItem"}}},
            ]
            if type_name == "IIssuedInvoice"
            else [{"name": "code", "type": {"kind": "SCALAR", "name": "String", "ofType": None}}]
        )

    def input_fields(self, type_name):
        return [
            {"name": "lastChangeDate", "type": {"kind": "INPUT_OBJECT", "name": "DateOnlyFilterInput", "ofType": None}}
        ]

    def agendas(self):
        return [{"guid": "ag-1", "displayName": "Demo s.r.o."}]


def _component_with_stub():
    comp = component_module.Component.__new__(component_module.Component)
    comp._client = _StubClient()
    comp._cfg = type(
        "C", (), {"endpoint": "issuedInvoices", "incremental_field": "lastChangeDate", "agenda_id": "ag-1"}
    )()
    return comp


def _call(comp, method_name):
    """Call a sync-action method with a patched configuration (action='run') so the
    @sync_action decorator simply passes through the return value without JSON output."""
    with patch.object(type(comp), "configuration", new_callable=PropertyMock) as mock_cfg:
        mock_cfg.return_value = type("Cfg", (), {"action": "run"})()
        return getattr(comp, method_name)()


def test_list_endpoints():
    comp = _component_with_stub()
    assert SelectElement("issuedInvoices") in _call(comp, "list_endpoints")


def test_list_fields_includes_scalar_nested_and_list():
    comp = _component_with_stub()
    values = {e.value for e in _call(comp, "list_fields")}
    assert {"id", "currency.code", "items"} <= values


def test_list_incremental_fields():
    comp = _component_with_stub()
    assert SelectElement("lastChangeDate") in _call(comp, "list_incremental_fields")


def test_list_agendas():
    comp = _component_with_stub()
    result = _call(comp, "list_agendas")
    assert result[0].value == "ag-1"
    assert result[0].label == "Demo s.r.o."


def test_connection_success_returns_none():
    comp = _component_with_stub()
    assert _call(comp, "test_connection") is None


def test_connection_unknown_agenda_fails():
    comp = _component_with_stub()
    comp._cfg = type("C", (), {"endpoint": "issuedInvoices", "agenda_id": "does-not-exist"})()
    with pytest.raises(UserException):
        _call(comp, "test_connection")
