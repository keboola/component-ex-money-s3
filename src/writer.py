from keboola.utils.header_normalizer import NormalizerStrategy, get_normalizer

from query_builder import Selection

_HEADER_NORMALIZER = get_normalizer(NormalizerStrategy.DEFAULT)


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
