Money S3
=============

Keboola extractor for [Money S3](https://www.money.cz/) accounting software via its GraphQL API.
Extracts any endpoint exposed by the Money S3 API (issued invoices, received invoices, bank
statements, articles, etc.) into Keboola Storage tables, with support for full and incremental loads.

**Table of Contents:**

[TOC]

Functionality Notes
===================

The component connects to a self-hosted or cloud Money S3 instance, authenticates using OAuth2
client credentials, and queries the GraphQL API. Endpoint and field selection is driven entirely
by sync actions — the UI populates dropdowns dynamically from the live API, so no hardcoded
endpoint lists are needed.

Incremental loads track the maximum value of a configurable date field (default `lastChangeDate`)
across runs and filter subsequent requests to only fetch records changed since the last run.

Nested objects (e.g. `currency.code`, `partnerAddress.city`) are flattened into the parent table
as `<object>_<field>` columns. List fields (e.g. `vatRateSummary`, `items`) are extracted into
separate child tables linked to the parent by a foreign-key column.

Prerequisites
=============

- A running Money S3 instance with the S3 API module enabled.
- An application registration in the Money S3 administration panel, providing an `App ID`,
  `Client ID`, and `Client Secret`.
- The agenda (company file) GUID, which can be loaded via the `listAgendas` sync action.

Features
========

| **Feature**             | **Description**                                                              |
|-------------------------|------------------------------------------------------------------------------|
| Generic UI Form         | Dynamic UI form with sync-action-driven dropdowns.                           |
| Row-Based Configuration | Each extraction row targets one endpoint with its own field selection.       |
| Full Load               | Fetches all records from the selected endpoint on every run.                 |
| Incremental Loading     | Tracks the maximum value of a date field; fetches only new/changed records.  |
| Date Range Filter       | Optional `date_from` / `date_to` bounds applied as a GraphQL `where` clause. |
| Child Tables            | List-type fields (e.g. VAT rate summaries, invoice items) go to child tables.|
| Pagination              | Automatic skip/take pagination handles large datasets.                       |

Configuration
=============

Root configuration (connection — shared across all rows)
---------------------------------------------------------

| Parameter        | Type   | Required | Description                                                     |
|------------------|--------|----------|-----------------------------------------------------------------|
| `base_url`       | string | yes      | Base URL of the Money S3 API, e.g. `https://s3.example.com`.   |
| `app_id`         | string | yes      | Application ID registered in Money S3 administration.          |
| `client_id`      | string | yes      | OAuth2 client ID for the registered application.               |
| `#client_secret` | string | yes      | OAuth2 client secret (stored encrypted).                       |
| `scope`          | string | no       | OAuth2 scope. Defaults to `s3api`.                             |
| `agenda_id`      | string | yes      | GUID of the agenda (company file) to extract from.             |

Use the **listAgendas** sync action to load available agendas into the `agenda_id` dropdown.

Row configuration (extraction — one row per endpoint)
------------------------------------------------------

| Parameter           | Type         | Required | Description                                                                         |
|---------------------|--------------|----------|-------------------------------------------------------------------------------------|
| `endpoint`          | string       | yes      | GraphQL query root to extract, e.g. `issuedInvoices`. Load via **listEndpoints**.   |
| `fields`            | list[string] | yes      | Fields to extract. Nested: `currency.code`. Lists: `vatRateSummary`. Load via **listFields**. |
| `load_type`         | string       | yes      | `full_load` or `incremental_load`.                                                  |
| `incremental_field` | string       | no       | Date field used for incremental tracking. Defaults to `lastChangeDate`. Load via **listIncrementalFields**. |
| `date_from`         | string       | no       | Lower bound for the incremental/date-range filter (ISO date, e.g. `2024-01-01`).   |
| `date_to`           | string       | no       | Upper bound for the date-range filter.                                              |
| `destination.table_name` | string | no      | Output table name override. Defaults to the endpoint name.                         |
| `destination.primary_key` | list  | no      | Primary key column override. Defaults to `guid` if available, then `id`.          |

Sync Actions
============

| Action                  | Purpose                                                                 |
|-------------------------|-------------------------------------------------------------------------|
| `listAgendas`           | Populates the agenda dropdown with available company files.             |
| `listEndpoints`         | Populates the endpoint dropdown from the GraphQL schema.                |
| `listFields`            | Populates the fields multi-select for the chosen endpoint.              |
| `listIncrementalFields` | Populates the incremental field dropdown (date-typed fields only).      |

Output
======

Parent tables
-------------

One CSV per extraction row, named after the endpoint (or `destination.table_name`).

- Scalar fields are written as columns directly.
- Nested object fields (e.g. `currency.code`) are flattened to `currency_code`.
- Primary key defaults to `guid` if the type exposes it; falls back to `id`; otherwise no PK is set.
- Manifest `has_header: false` — column names are carried in the manifest schema.

Child tables
------------

List-typed fields (e.g. `vatRateSummary`, `items`, `payments`) are extracted into a separate table
named `<parent_table>_<list_field>`, e.g. `issuedInvoices_vatRateSummary`.

- A foreign-key column `<endpoint>_<parent_pk>` links each child row to its parent.
- If the child type exposes a `sequence` field it is used as the child PK; otherwise a `_index`
  column (0-based position within the parent's list) is added.
- Combined PK: `[<fk_column>, sequence | _index]`.

Example: `issuedInvoices` with `vatRateSummary` selected produces:

```
issuedInvoices.csv                    issuedInvoices_vatRateSummary.csv
id, documentNumber, ..., guid         issuedInvoices_guid, sequence, vatRate, ...
```

Development
-----------

To customize the local data folder path, replace the `CUSTOM_FOLDER` placeholder with your desired path in the `docker-compose.yml` file:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    volumes:
      - ./:/code
      - ./CUSTOM_FOLDER:/data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Clone this repository, initialize the workspace, and run the component using the following
commands:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
git clone https://github.com/keboola/component-ex-money-s3 component-ex-money-s3
cd component-ex-money-s3
docker-compose build
docker-compose run --rm dev
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run the test suite and perform lint checks using this command:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
docker-compose run --rm test
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Integration
===========

For details about deployment and integration with Keboola, refer to the
[deployment section of the developer
documentation](https://developers.keboola.com/extend/component/deployment/).
