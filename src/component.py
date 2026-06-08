"""Money S3 GraphQL extractor."""

import csv
import logging
import os
import re

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import SelectElement

from auth import TokenProvider
from client import MoneyS3Client
from configuration import Configuration
from query_builder import build_query, build_where
from schema import all_field_paths, classify_fields, date_filter_field_names, prune_fields, resolve_selection
from writer import child_columns, extract_children, fk_columns, flatten_parent, normalize_columns, parent_columns

_PAGE_SIZE = 500

# Money S3 rejects fields that belong to the other legislation (e.g. an `Sk`-only member
# queried against a `Cz` company) with a per-item error like:
#   Cannot access member 'vatIdentificationNumberSk' on path '/issuedInvoices/items[0]...'.
#   Member is available only for 'Sk' legislation
_LEGISLATION_ERROR = re.compile(r"only for '\w+' legislation", re.IGNORECASE)
_LEGISLATION_MEMBER = re.compile(r"member '([^']+)'", re.IGNORECASE)
_PRUNE_MAX_ROUNDS = 12


def max_incremental_value(rows: list[dict], field_name: str) -> str | None:
    values = [r[field_name] for r in rows if r.get(field_name) is not None]
    return max(values) if values else None


def legislation_incompatible_members(errors: list[dict]) -> set[str]:
    """Extract the member names from GraphQL errors that reject a field for legislation
    reasons. Non-legislation errors are ignored so they still surface on the real run."""
    bad: set[str] = set()
    for err in errors:
        message = err.get("message", "")
        if _LEGISLATION_ERROR.search(message):
            match = _LEGISLATION_MEMBER.search(message)
            if match:
                bad.add(match.group(1))
    return bad


class Component(ComponentBase):
    def __init__(self):
        super().__init__()
        self._cfg: Configuration | None = None
        self._client: MoneyS3Client | None = None

    def _config(self) -> Configuration:
        if self._cfg is None:
            self._cfg = Configuration(**self.configuration.parameters)
        return self._cfg

    def _get_client(self) -> MoneyS3Client:
        if self._client is None:
            c = self._config()
            app_id = self.configuration.image_parameters.get("app_id")
            if not app_id:
                raise UserException("App ID is not configured; it must be provided via the component image parameters.")
            tokens = TokenProvider(c.base_url, app_id, c.client_id, c.client_secret)
            self._client = MoneyS3Client(c.base_url, tokens, c.agenda_id)
        return self._client

    def run(self):
        cfg = self._config()
        if not cfg.endpoint:
            raise UserException("No 'endpoint' selected.")
        client = self._get_client()

        # No explicit field selection → extract every available field (all scalars,
        # nested object scalars and lists as child tables).
        fields = cfg.fields or all_field_paths(client, cfg.endpoint)
        if not fields:
            raise UserException(f"Endpoint '{cfg.endpoint}' exposes no extractable fields.")

        selection = resolve_selection(
            client,
            cfg.endpoint,
            fields,
            incremental_field=cfg.incremental_field if cfg.is_incremental else None,
            override_pk=cfg.destination.primary_key,
        )

        where_clause = self._build_where_clause(cfg)

        parent_name = cfg.destination.table_name or cfg.endpoint
        selection, rows = self._fetch_rows(selection, where_clause, cfg.endpoint)
        logging.info(f"Fetched {len(rows)} records from '{cfg.endpoint}'.")

        self._write_parent(parent_name, selection, rows, cfg)
        self._write_children(parent_name, selection, rows, cfg)

        # Only advance the incremental watermark for true incremental loads (those with a PK,
        # so rows upsert). Without a PK we full-overwrite, and persisting a watermark would make
        # the next run fetch only newer rows and overwrite away the older ones.
        if cfg.is_incremental and selection.pk_fields:
            new_value = max_incremental_value(rows, cfg.incremental_field)
            if new_value:
                state = self.get_state_file() or {}
                state[cfg.endpoint] = {"last_value": new_value}
                self.write_state_file(state)

    def _fetch_rows(self, selection, where_clause, endpoint):
        """Fetch all rows for the endpoint, pruning legislation-incompatible fields on the way.

        The first page doubles as a compatibility probe: if the server rejects any fields
        because they belong to the other legislation (e.g. `Sk`-only members on a `Cz`
        company), those fields are dropped and the first page is re-fetched until the query
        is accepted — so one incompatible field no longer aborts an otherwise valid endpoint.
        Non-legislation errors are still raised with their original message. Returns the
        (possibly pruned) selection together with the collected rows."""
        body = None
        for _ in range(_PRUNE_MAX_ROUNDS):
            query = build_query(selection, where_clause)
            body = self._client.execute_with_errors(query, variables={"skip": 0, "take": _PAGE_SIZE})
            bad = legislation_incompatible_members(body.get("errors") or [])
            if not bad:
                break
            logging.warning(
                f"Endpoint '{endpoint}': dropping fields incompatible with the company "
                f"legislation: {', '.join(sorted(bad))}."
            )
            pruned = prune_fields(selection, bad)
            if pruned == selection:  # nothing left we can remove — avoid looping
                break
            selection = pruned

        if body.get("errors"):  # non-legislation errors (or unremovable ones) — surface them
            messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
            raise UserException(f"GraphQL error: {messages}")

        segment = (body.get("data") or {}).get(endpoint)
        if segment is None:
            raise UserException(f"No data returned for endpoint '{endpoint}'.")
        rows = list(segment.get("items") or [])
        skip = _PAGE_SIZE
        while segment.get("pageInfo", {}).get("hasNextPage"):
            data = self._client.execute(query, variables={"skip": skip, "take": _PAGE_SIZE})
            segment = data[endpoint]
            rows.extend(segment.get("items") or [])
            skip += _PAGE_SIZE
        return selection, rows

    def _build_where_clause(self, cfg: Configuration) -> str | None:
        gte = cfg.date_from
        if cfg.is_incremental:
            state = self.get_state_file() or {}
            last = state.get(cfg.endpoint, {}).get("last_value")
            gte = last or cfg.date_from
        if not (gte or cfg.date_to):
            return None
        return build_where(cfg.incremental_field, gte=gte, lte=cfg.date_to)

    def _write_parent(self, name, selection, rows, cfg):
        if not rows:
            logging.info(f"No rows for '{name}'; skipping table and manifest.")
            return
        raw_columns = parent_columns(selection)
        # Normalize names once for the schema/PK/header; remap row keys via this mapping
        # (no per-row regex).
        schema = normalize_columns(raw_columns)
        rename = dict(zip(raw_columns, schema))
        pk = normalize_columns(selection.pk_fields)
        # Incremental upsert needs a PK; without one, fall back to full overwrite to avoid
        # appending duplicates on every run.
        incremental = cfg.is_incremental and bool(selection.pk_fields)
        if cfg.is_incremental and not selection.pk_fields:
            logging.warning(
                f"Endpoint '{selection.endpoint}' has no detectable primary key; "
                f"writing '{name}' as a full overwrite instead of incremental."
            )
        table = self.create_out_table_definition(
            f"{name}.csv", primary_key=pk, incremental=incremental, schema=schema, has_header=True
        )
        with open(table.full_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=schema, extrasaction="ignore")
            writer.writeheader()
            for item in rows:
                writer.writerow({rename[k]: v for k, v in flatten_parent(item, selection).items()})
        self.write_manifest(table)

    def _child_pk(self, selection, list_name) -> list[str]:
        child = selection.lists[list_name]
        tail = child.pk_field if child.pk_field else "_index"
        return [*fk_columns(selection), tail]

    def _write_children(self, parent_name, selection, rows, cfg):
        if not selection.lists:
            return
        writers = {}
        files = {}
        counts = {}
        renames = {}
        try:
            for list_name in selection.lists:
                raw_cols = child_columns(selection, list_name)
                schema = normalize_columns(raw_cols)
                renames[list_name] = dict(zip(raw_cols, schema))
                child_table_name = f"{parent_name}_{list_name}"
                pk = normalize_columns(self._child_pk(selection, list_name))
                table = self.create_out_table_definition(
                    f"{child_table_name}.csv",
                    primary_key=pk,
                    incremental=cfg.is_incremental and bool(selection.pk_fields),
                    schema=schema,
                    has_header=True,
                )
                fh = open(table.full_path, "w", encoding="utf-8", newline="")
                files[list_name] = (fh, table)
                writers[list_name] = csv.DictWriter(fh, fieldnames=schema, extrasaction="ignore")
                writers[list_name].writeheader()
                counts[list_name] = 0
            for item in rows:
                for list_name, child_rows in extract_children(item, selection).items():
                    rename = renames[list_name]
                    for row in child_rows:
                        writers[list_name].writerow({rename[k]: v for k, v in row.items()})
                        counts[list_name] += 1
        finally:
            for list_name, (fh, table) in files.items():
                fh.close()
                if counts[list_name]:
                    self.write_manifest(table)
                else:
                    logging.info(f"No rows for child table '{table.name}'; skipping table and manifest.")
                    if os.path.exists(table.full_path):
                        os.remove(table.full_path)

    @sync_action("testConnection")
    def test_connection(self):
        cfg = self._config()
        client = self._get_client()
        # agendas() exercises the full OAuth flow (token fetch) plus the GraphQL endpoint,
        # validating base_url, app_id, client_id and client_secret in one call.
        agendas = client.agendas()
        if cfg.agenda_id and cfg.agenda_id not in {a["guid"] for a in agendas}:
            raise UserException(
                f"Authenticated successfully, but the selected agenda '{cfg.agenda_id}' "
                f"was not found among the {len(agendas)} available agendas."
            )

    @sync_action("list_agendas")
    def list_agendas(self):
        items = self._get_client().agendas()
        return [SelectElement(value=a["guid"], label=a.get("displayName") or a.get("name") or a["guid"]) for a in items]

    @sync_action("list_endpoints")
    def list_endpoints(self):
        return [SelectElement(value=r["name"]) for r in self._get_client().query_roots()]

    @sync_action("list_fields")
    def list_fields(self):
        cfg = self._config()
        client = self._get_client()
        item_type, _ = client.endpoint_meta(cfg.endpoint)
        scalars, objects, lists = classify_fields(client.type_fields(item_type))
        elements = [SelectElement(value=s) for s in scalars]
        for obj, obj_type in objects.items():
            sub_scalars, _, _ = classify_fields(client.type_fields(obj_type))
            elements.extend(SelectElement(value=f"{obj}.{sub}", label=f"{obj}.{sub}") for sub in sub_scalars)
        elements.extend(SelectElement(value=lst, label=f"{lst} (child table)") for lst in lists)
        return elements

    @sync_action("list_incremental_fields")
    def list_incremental_fields(self):
        cfg = self._config()
        client = self._get_client()
        _, filter_type = client.endpoint_meta(cfg.endpoint)
        if not filter_type:
            return []
        return [SelectElement(value=n) for n in date_filter_field_names(client.input_fields(filter_type))]


if __name__ == "__main__":
    try:
        comp = Component()
        comp.execute_action()
    except UserException as exc:
        logging.error(str(exc))
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
