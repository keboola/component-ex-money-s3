from dataclasses import dataclass, field


@dataclass
class ChildSelection:
    scalars: list[str]
    pk_field: str | None  # 'sequence' when the element type has it, else None


@dataclass
class Selection:
    endpoint: str
    pk_fields: list[str]  # parent primary key column(s); empty when none detected/configured
    scalars: list[str] = field(default_factory=list)
    nested: dict[str, list[str]] = field(default_factory=dict)  # object -> [subfields]
    lists: dict[str, ChildSelection] = field(default_factory=dict)


def build_where(field_name: str, gte: str | None = None, lte: str | None = None) -> str | None:
    clauses = []
    if gte is not None:
        clauses.append(f'{{ {field_name}: {{ gte: "{gte}" }} }}')
    if lte is not None:
        clauses.append(f'{{ {field_name}: {{ lte: "{lte}" }} }}')
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return "{ and: [ " + ", ".join(clauses) + " ] }"


def _item_selection(sel: Selection) -> str:
    parts = list(sel.scalars)
    for obj, subs in sel.nested.items():
        parts.append(f"{obj} {{ {' '.join(subs)} }}")
    for name, child in sel.lists.items():
        parts.append(f"{name} {{ {' '.join(child.scalars)} }}")
    return " ".join(parts)


def build_query(sel: Selection, where_clause: str | None) -> str:
    # NOTE: no `order` argument. The Money S3 server duplicates rows when any `order`
    # clause is present (server-side sort bug); skip/take pagination is stable without it.
    args = ["skip: $skip", "take: $take"]
    if where_clause:
        args.append(f"where: {where_clause}")
    arg_str = ", ".join(args)
    return (
        "query ($skip: Int!, $take: Int!) {\n"
        f"  {sel.endpoint}({arg_str}) {{\n"
        "    pageInfo { hasNextPage }\n"
        "    totalCount\n"
        f"    items {{ {_item_selection(sel)} }}\n"
        "  }\n"
        "}"
    )
