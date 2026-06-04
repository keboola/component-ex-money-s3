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


def _fk_column(sel: Selection) -> str | None:
    # No parent PK → no foreign-key column to emit (avoids a bogus "<endpoint>_None").
    if not sel.pk_field:
        return None
    return f"{sel.endpoint}_{sel.pk_field}"


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
    fk = _fk_column(sel)
    cols = [fk] if fk else []
    if not child.pk_field:
        cols.append("_index")
    # pk_field (e.g. sequence) is already one of child.scalars
    cols.extend(child.scalars)
    return cols


def extract_children(item: dict, sel: Selection, parent_pk_value) -> dict[str, list[dict]]:
    fk = _fk_column(sel)
    out: dict[str, list[dict]] = {}
    for list_name, child in sel.lists.items():
        rows: list[dict] = []
        for idx, element in enumerate(item.get(list_name) or []):
            row: dict = {fk: parent_pk_value} if fk else {}
            if not child.pk_field:
                row["_index"] = idx
            for s in child.scalars:
                row[s] = element.get(s)
            rows.append(row)
        out[list_name] = rows
    return out
