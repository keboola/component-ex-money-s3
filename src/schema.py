from graphql_types import normalize_field, unwrap_type
from query_builder import ChildSelection, Selection

_DATE_FILTER_TYPES = {"DateOnlyFilterInput", "DateOperationFilterInput", "DateTimeOperationFilterInput"}


def classify_fields(fields_json: list[dict]) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Return (scalar_names, {object_name: type}, {list_name: element_type})."""
    scalars: list[str] = []
    objects: dict[str, str] = {}
    lists: dict[str, str] = {}
    for raw in fields_json:
        nf = normalize_field(raw)
        if nf.is_list:
            lists[nf.name] = nf.type_name
        elif nf.is_object:
            objects[nf.name] = nf.type_name
        elif nf.is_scalar:
            scalars.append(nf.name)
    return scalars, objects, lists


def detect_pk(scalar_names: list[str]) -> str | None:
    # Prefer `guid`: in Money S3 the integer `id` is NOT unique within an endpoint
    # (it repeats across records), whereas `guid` is a unique UUID. Keying on `id`
    # would collapse distinct records. Fall back to `id`, then none.
    for candidate in ("guid", "id"):
        if candidate in scalar_names:
            return candidate
    return None


def date_filter_field_names(filter_fields_json: list[dict]) -> list[str]:
    names: list[str] = []
    for raw in filter_fields_json:
        _, type_name, _ = unwrap_type(raw["type"])
        if type_name in _DATE_FILTER_TYPES:
            names.append(raw["name"])
    return names


def prune_fields(selection: Selection, bad: set[str]) -> Selection:
    """Return a copy of ``selection`` with every field named in ``bad`` removed —
    from top-level scalars, nested object sub-fields, and child (list) selections.
    A nested object or list left with no fields is dropped entirely. Used to strip
    fields the server rejects (e.g. legislation-incompatible members)."""
    scalars = [s for s in selection.scalars if s not in bad]

    nested: dict[str, list[str]] = {}
    for obj, subs in selection.nested.items():
        if obj in bad:
            continue
        kept = [s for s in subs if s not in bad]
        if kept:
            nested[obj] = kept

    lists: dict[str, ChildSelection] = {}
    for name, child in selection.lists.items():
        if name in bad:
            continue
        kept = [s for s in child.scalars if s not in bad]
        if kept:
            pk = child.pk_field if child.pk_field in kept else None
            lists[name] = ChildSelection(scalars=kept, pk_field=pk)

    return Selection(
        endpoint=selection.endpoint, pk_field=selection.pk_field, scalars=scalars, nested=nested, lists=lists
    )


def all_field_paths(client, endpoint: str) -> list[str]:
    """Every selectable field path for an endpoint — scalars, nested object scalars
    (``obj.sub``) and list field names (child tables). Mirrors what the ``list_fields``
    sync action offers, so it can serve as the default selection when the user picks none."""
    item_type, _ = client.endpoint_meta(endpoint)
    scalars, objects, lists = classify_fields(client.type_fields(item_type))
    paths = list(scalars)
    for obj, obj_type in objects.items():
        sub_scalars, _, _ = classify_fields(client.type_fields(obj_type))
        paths.extend(f"{obj}.{sub}" for sub in sub_scalars)
    paths.extend(lists.keys())
    return paths


def resolve_selection(
    client, endpoint: str, fields: list[str], incremental_field: str | None, override_pk: list[str] | None
) -> Selection:
    item_type, _ = client.endpoint_meta(endpoint)
    entity_fields = client.type_fields(item_type)
    scalar_names, object_types, list_types = classify_fields(entity_fields)

    selected_scalars: list[str] = []
    nested: dict[str, list[str]] = {}
    lists: dict[str, ChildSelection] = {}

    for field_path in fields:
        if "." in field_path:
            obj, sub = field_path.split(".", 1)
            nested.setdefault(obj, []).append(sub)
        elif field_path in list_types:
            child_fields = client.type_fields(list_types[field_path])
            child_scalars, _, _ = classify_fields(child_fields)
            pk = "sequence" if "sequence" in child_scalars else None
            lists[field_path] = ChildSelection(scalars=child_scalars, pk_field=pk)
        elif field_path in scalar_names:
            selected_scalars.append(field_path)
        # silently ignore unknown / object-without-subfield selections

    pk_field = override_pk[0] if override_pk else detect_pk(scalar_names)

    # force-include PK and incremental field as scalar columns
    for required in (pk_field, incremental_field):
        if required and required in scalar_names and required not in selected_scalars:
            selected_scalars.append(required)

    return Selection(endpoint=endpoint, pk_field=pk_field, scalars=selected_scalars, nested=nested, lists=lists)
