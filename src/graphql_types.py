from dataclasses import dataclass

_SCALAR_KINDS = {"SCALAR", "ENUM"}
_OBJECT_KINDS = {"OBJECT", "INTERFACE", "UNION"}


def unwrap_type(type_json: dict) -> tuple[str, str | None, bool]:
    """Return (kind, name, is_list) unwrapping NON_NULL/LIST wrappers."""
    is_list = False
    node = type_json
    while node and node.get("kind") in ("NON_NULL", "LIST"):
        if node["kind"] == "LIST":
            is_list = True
        node = node.get("ofType")
    if not node:
        return ("SCALAR", None, is_list)
    return (node.get("kind"), node.get("name"), is_list)


@dataclass(frozen=True)
class NormField:
    name: str
    kind: str
    type_name: str | None
    is_list: bool
    is_non_null: bool = False  # outermost type wrapper is NON_NULL (e.g. `Int!`)

    @property
    def is_scalar(self) -> bool:
        return not self.is_list and self.kind in _SCALAR_KINDS

    @property
    def is_object(self) -> bool:
        return not self.is_list and self.kind in _OBJECT_KINDS


def normalize_field(field_json: dict) -> NormField:
    type_json = field_json["type"]
    kind, name, is_list = unwrap_type(type_json)
    return NormField(
        name=field_json["name"],
        kind=kind,
        type_name=name,
        is_list=is_list,
        is_non_null=type_json.get("kind") == "NON_NULL",
    )
