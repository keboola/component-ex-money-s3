from collections import OrderedDict

from keboola.component.dao import BaseType, ColumnDefinition
from keboola.utils.header_normalizer import NormalizerStrategy, get_normalizer

from query_builder import Selection

_HEADER_NORMALIZER = get_normalizer(NormalizerStrategy.DEFAULT)

# Map Money S3 / HotChocolate GraphQL scalar types to Keboola base types. Anything not
# listed (UUID, String, ID, TimeSpan, …) stays STRING.
_GQL_BASE_TYPE = {
    "Int": BaseType.integer,
    "Long": BaseType.integer,
    "Short": BaseType.integer,
    "Byte": BaseType.integer,
    "Decimal": BaseType.numeric,
    "Float": BaseType.float,
    "Boolean": BaseType.boolean,
    "Date": BaseType.date,
    "DateTime": BaseType.timestamp,
    "DateTimeOffset": BaseType.timestamp,
}


def _normalize_one(name: str) -> str:
    return normalize_columns([name])[0]


def _column(gql_type: str | None, is_pk: bool, nullable: bool = True) -> ColumnDefinition:
    base = _GQL_BASE_TYPE.get(gql_type or "", BaseType.string)
    return ColumnDefinition(data_types=base(), primary_key=is_pk, nullable=nullable)


def normalize_columns(names: list[str]) -> list[str]:
    """Normalize column names the way the Keboola Storage platform does, so the manifest
    schema, primary keys and header match the stored columns. Uses Keboola's default header
    normalizer and additionally strips leading underscores, which the platform webalizes
    away (e.g. '_index' -> 'index')."""
    normalized = _HEADER_NORMALIZER.normalize_header(list(names))
    return [n.lstrip("_") or n for n in normalized]


def fk_columns(sel: Selection) -> list[str]:
    # One foreign-key column per parent primary-key field; empty when the parent has no PK
    # (avoids a bogus "<endpoint>_None").
    return [f"{sel.endpoint}_{pk}" for pk in sel.pk_fields]


def fk_values(sel: Selection, item: dict) -> dict:
    """The foreign-key column -> value mapping for a parent row."""
    return {f"{sel.endpoint}_{pk}": item.get(pk) for pk in sel.pk_fields}


def parent_columns(sel: Selection) -> list[str]:
    cols = list(sel.scalars)
    for obj, subs in sel.nested.items():
        cols.extend(f"{obj}_{sub}" for sub in subs)
    return cols


def flatten_parent(item: dict, sel: Selection) -> dict:
    row: dict = {s: item.get(s) for s in sel.scalars}
    for obj, subs in sel.nested.items():
        nested_val = item.get(obj) or {}
        for sub in subs:
            row[f"{obj}_{sub}"] = nested_val.get(sub) if isinstance(nested_val, dict) else None
    return row


def child_columns(sel: Selection, list_name: str) -> list[str]:
    child = sel.lists[list_name]
    cols = fk_columns(sel)
    if not child.pk_field:
        cols.append("_index")
    # pk_field (e.g. sequence) is already one of child.scalars
    cols.extend(child.scalars)
    return cols


def extract_children(item: dict, sel: Selection) -> dict[str, list[dict]]:
    fks = fk_values(sel, item)
    out: dict[str, list[dict]] = {}
    for list_name, child in sel.lists.items():
        rows: list[dict] = []
        for idx, element in enumerate(item.get(list_name) or []):
            row: dict = dict(fks)
            if not child.pk_field:
                row["_index"] = idx
            for s in child.scalars:
                row[s] = element.get(s)
            rows.append(row)
        out[list_name] = rows
    return out


def parent_schema(sel: Selection) -> "OrderedDict[str, ColumnDefinition]":
    """Typed schema (normalized column name -> ColumnDefinition) for the parent table.

    Nullability comes from the GraphQL NON_NULL marker for top-level scalars; nested object
    columns stay nullable (the object itself may be absent)."""
    pk = set(sel.pk_fields)
    schema: OrderedDict[str, ColumnDefinition] = OrderedDict()
    for raw in parent_columns(sel):
        schema[_normalize_one(raw)] = _column(sel.column_types.get(raw), raw in pk, nullable=raw not in sel.non_null)
    return schema


def child_schema(sel: Selection, list_name: str) -> "OrderedDict[str, ColumnDefinition]":
    """Typed schema for a child table. Foreign-key columns take the parent PK's type, the
    synthetic `_index` is an integer, and the rest come from the child element's scalar types."""
    child = sel.lists[list_name]
    fks = fk_columns(sel)
    pk_cols = set(fks) | {child.pk_field or "_index"}
    schema: OrderedDict[str, ColumnDefinition] = OrderedDict()
    for raw in child_columns(sel, list_name):
        if raw == "_index":
            gql_type, nullable = "Int", False  # always present per child row
        elif raw in fks:
            gql_type = sel.column_types.get(raw[len(sel.endpoint) + 1 :])  # parent_<pk> -> pk type
            nullable = False  # FK = parent (non-null) PK value
        else:
            gql_type, nullable = child.column_types.get(raw), raw not in child.non_null
        schema[_normalize_one(raw)] = _column(gql_type, raw in pk_cols, nullable=nullable)
    return schema
