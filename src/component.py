"""Money S3 GraphQL extractor."""

import csv
import logging

from keboola.component.base import ComponentBase, sync_action
from keboola.component.exceptions import UserException
from keboola.component.sync_actions import MessageType, SelectElement, ValidationResult

from auth import TokenProvider
from client import MoneyS3Client
from configuration import Configuration
from query_builder import build_query, build_where
from schema import all_field_paths, classify_fields, date_filter_field_names, resolve_selection
from writer import child_columns, extract_children, flatten_parent, parent_columns

_PAGE_SIZE = 500


def max_incremental_value(rows: list[dict], field_name: str) -> str | None:
    values = [r[field_name] for r in rows if r.get(field_name) is not None]
    return max(values) if values else None


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
            tokens = TokenProvider(c.base_url, c.app_id, c.client_id, c.client_secret, c.scope)
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
        query = build_query(selection, where_clause)

        parent_name = cfg.destination.table_name or cfg.endpoint
        rows = list(client.paginate(query, cfg.endpoint, page_size=_PAGE_SIZE))
        logging.info(f"Fetched {len(rows)} records from '{cfg.endpoint}'.")

        self._write_parent(parent_name, selection, rows, cfg)
        self._write_children(parent_name, selection, rows, cfg)

        # Only advance the incremental watermark for true incremental loads (those with a PK,
        # so rows upsert). Without a PK we full-overwrite, and persisting a watermark would make
        # the next run fetch only newer rows and overwrite away the older ones.
        if cfg.is_incremental and selection.pk_field:
            new_value = max_incremental_value(rows, cfg.incremental_field)
            if new_value:
                state = self.get_state_file() or {}
                state[cfg.endpoint] = {"last_value": new_value}
                self.write_state_file(state)

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
        columns = parent_columns(selection)
        pk = [selection.pk_field] if selection.pk_field else []
        # Incremental upsert needs a PK; without one, fall back to full overwrite to avoid
        # appending duplicates on every run.
        incremental = cfg.is_incremental and bool(selection.pk_field)
        if cfg.is_incremental and not selection.pk_field:
            logging.warning(
                f"Endpoint '{selection.endpoint}' has no detectable primary key; "
                f"writing '{name}' as a full overwrite instead of incremental."
            )
        table = self.create_out_table_definition(f"{name}.csv", primary_key=pk, incremental=incremental, schema=columns)
        with open(table.full_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            for item in rows:
                writer.writerow(flatten_parent(item, selection))
        self.write_manifest(table)

    def _child_pk(self, selection, list_name) -> list[str]:
        child = selection.lists[list_name]
        if not selection.pk_field:
            return [child.pk_field] if child.pk_field else ["_index"]
        fk = f"{selection.endpoint}_{selection.pk_field}"
        return [fk, child.pk_field] if child.pk_field else [fk, "_index"]

    def _write_children(self, parent_name, selection, rows, cfg):
        if not selection.lists:
            return
        writers = {}
        files = {}
        try:
            for list_name in selection.lists:
                cols = child_columns(selection, list_name)
                child_table_name = f"{parent_name}_{list_name}"
                pk = self._child_pk(selection, list_name)
                table = self.create_out_table_definition(
                    f"{child_table_name}.csv",
                    primary_key=pk,
                    incremental=cfg.is_incremental and bool(selection.pk_field),
                    schema=cols,
                )
                fh = open(table.full_path, "w", encoding="utf-8", newline="")
                files[list_name] = (fh, table)
                writers[list_name] = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            for item in rows:
                pk_value = item.get(selection.pk_field) if selection.pk_field else None
                for list_name, child_rows in extract_children(item, selection, pk_value).items():
                    for row in child_rows:
                        writers[list_name].writerow(row)
        finally:
            for fh, table in files.values():
                fh.close()
                self.write_manifest(table)

    @sync_action("testConnection")
    def test_connection(self):
        cfg = self._config()
        client = self._get_client()
        # agendas() exercises the full OAuth flow (token fetch) plus the GraphQL endpoint,
        # validating base_url, app_id, client_id, client_secret and scope in one call.
        agendas = client.agendas()
        if cfg.agenda_id and cfg.agenda_id not in {a["guid"] for a in agendas}:
            raise UserException(
                f"Authenticated successfully, but the selected agenda '{cfg.agenda_id}' "
                f"was not found among the {len(agendas)} available agendas."
            )
        return ValidationResult("Connection successful.", MessageType.SUCCESS)

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
        logging.exception(exc)
        exit(1)
    except Exception as exc:
        logging.exception(exc)
        exit(2)
