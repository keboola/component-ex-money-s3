from collections.abc import Iterator

from keboola.component.exceptions import UserException
from keboola.http_client import HttpClient

from auth import TokenProvider
from graphql_types import unwrap_type

_QUERY_ROOTS = """
query {
  __schema { queryType { fields {
    name
    args { name type { kind name ofType { kind name } } }
    type { kind name ofType { kind name } }
  } } }
}
"""

_TYPE_FIELDS = """
query ($name: String!) {
  __type(name: $name) { fields {
    name type { kind name ofType { kind name ofType { kind name } } }
  } }
}
"""

_INPUT_FIELDS = """
query ($name: String!) {
  __type(name: $name) { inputFields {
    name type { kind name ofType { kind name } }
  } }
}
"""


class MoneyS3Client:
    def __init__(self, base_url: str, token_provider: TokenProvider, agenda_id: str, max_retries: int = 8):
        self._http = HttpClient(
            base_url=base_url.rstrip("/") + "/", max_retries=max_retries, status_forcelist=(429, 500, 502, 503, 504)
        )
        self._tokens = token_provider
        self._agenda_id = agenda_id

    def execute(self, query: str, variables: dict | None = None, with_agenda: bool = True) -> dict:
        headers = {"Authorization": f"Bearer {self._tokens.get_token()}"}
        if with_agenda:
            headers["AgendaId"] = self._agenda_id
        # HttpClient.post (via @response_error_handling) returns the parsed JSON body (dict),
        # not a requests.Response.
        body = self._http.post(
            endpoint_path="graphql", headers=headers, json={"query": query, "variables": variables or {}}
        )
        if body.get("errors"):
            messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
            raise UserException(f"GraphQL error: {messages}")
        return body["data"]

    def paginate(self, query: str, root: str, page_size: int = 500) -> Iterator[dict]:
        skip = 0
        while True:
            data = self.execute(query, variables={"skip": skip, "take": page_size})
            segment = data[root]
            items = segment.get("items") or []
            yield from items
            if not segment.get("pageInfo", {}).get("hasNextPage"):
                break
            skip += page_size

    # --- introspection ---
    def query_roots(self) -> list[dict]:
        return self.execute(_QUERY_ROOTS, with_agenda=False)["__schema"]["queryType"]["fields"]

    def type_fields(self, type_name: str) -> list[dict]:
        data = self.execute(_TYPE_FIELDS, variables={"name": type_name}, with_agenda=False)
        node = data["__type"]
        return node["fields"] if node else []

    def input_fields(self, type_name: str) -> list[dict]:
        data = self.execute(_INPUT_FIELDS, variables={"name": type_name}, with_agenda=False)
        node = data["__type"]
        return node["inputFields"] if node else []

    def agendas(self) -> list[dict]:
        data = self.execute("query { agendas { items { guid displayName name } } }", with_agenda=False)
        return data["agendas"]["items"]

    def endpoint_meta(self, endpoint: str) -> tuple[str, str | None]:
        """Return (item_type_name, filter_input_type_name | None) for a query root."""
        root = next((f for f in self.query_roots() if f["name"] == endpoint), None)
        if root is None:
            raise UserException(f"Unknown endpoint '{endpoint}'.")
        _, segment_type, _ = unwrap_type(root["type"])
        filter_type = None
        for arg in root["args"]:
            if arg["name"] == "where":
                _, filter_type, _ = unwrap_type(arg["type"])
        item_type = None
        for f in self.type_fields(segment_type):
            if f["name"] == "items":
                _, item_type, _ = unwrap_type(f["type"])
        if not item_type:
            raise UserException(f"Could not resolve item type for endpoint '{endpoint}'.")
        return item_type, filter_type
