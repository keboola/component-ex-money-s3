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
