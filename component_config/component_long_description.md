The Money S3 (Seyfor) extractor pulls data from the Money S3 accounting system through its
GraphQL API.

It is a generic, introspection-driven, row-based extractor: each configuration row targets one
API endpoint (e.g. issued invoices, received invoices, the address book, accounting journals or
any codebook). The component reads the API schema at runtime, so it covers every available
endpoint and field without hardcoding.

Features:

- **Field selection** — pick the exact fields to extract, or leave the selection empty to pull
  every available field. Nested objects are flattened into columns and repeating lists are
  written as separate child tables.
- **Typed output** — column data types and nullability are derived from the GraphQL schema.
- **Primary keys** — detected automatically from the API metadata, with an optional override.
- **Full and incremental loads** — incremental loads use a server-side date filter and track a
  watermark between runs.
- **Date range** — optionally bound the extraction with Date From / Date To.

Authentication uses OAuth2 client credentials (Client ID, Client Secret) against a selected
Agenda (company file).
