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
    filter_type: str | None = None  # GraphQL input type of the `where` argument, if any


def build_where(field_name: str, gte: str | None = None, lte: str | None = None) -> dict | None:
    """Build the `where` filter object. Returned as a plain dict so it can be sent as a typed
    GraphQL variable (not interpolated into the query) — the server validates it, and there is
    no string-injection surface for the field name or the date values."""
    clauses = []
    if gte is not None:
        clauses.append({field_name: {"gte": gte}})
    if lte is not None:
        clauses.append({field_name: {"lte": lte}})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"and": clauses}


def _item_selection(sel: Selection) -> str:
    parts = list(sel.scalars)
    for obj, subs in sel.nested.items():
        parts.append(f"{obj} {{ {' '.join(subs)} }}")
    for name, child in sel.lists.items():
        parts.append(f"{name} {{ {' '.join(child.scalars)} }}")
    return " ".join(parts)


def build_query(sel: Selection, with_where: bool = False) -> str:
    # NOTE: no `order` argument. The Money S3 server duplicates rows when any `order`
    # clause is present (server-side sort bug); skip/take pagination is stable without it.
    # The `where` filter is passed as a typed variable ($where), never interpolated.
    var_decls = ["$skip: Int!", "$take: Int!"]
    args = ["skip: $skip", "take: $take"]
    if with_where:
        var_decls.append(f"$where: {sel.filter_type}")
        args.append("where: $where")
    return (
        f"query ({', '.join(var_decls)}) {{\n"
        f"  {sel.endpoint}({', '.join(args)}) {{\n"
        "    pageInfo { hasNextPage }\n"
        "    totalCount\n"
        f"    items {{ {_item_selection(sel)} }}\n"
        "  }\n"
        "}"
    )
