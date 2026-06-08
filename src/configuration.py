import logging

from keboola.component.exceptions import UserException
from pydantic import BaseModel, Field, ValidationError, model_validator


class Destination(BaseModel):
    table_name: str | None = None
    primary_key: list[str] | None = None


class Configuration(BaseModel):
    # connection (root config). NOTE: app_id is supplied via image parameters, not here.
    base_url: str
    client_id: str
    client_secret: str = Field(default="", alias="#client_secret")
    agenda_id: str = ""

    # extraction (row config)
    endpoint: str = ""
    fields: list[str] = Field(default_factory=list)
    load_type: str = "full_load"
    incremental_field: str = "lastChange"
    date_from: str | None = None
    date_to: str | None = None
    destination: Destination = Field(default_factory=Destination)
    page_size: int = 500
    debug: bool = False

    def __init__(self, **data):
        try:
            super().__init__(**data)
        except ValidationError as e:
            msgs = [f"{'.'.join(str(p) for p in err['loc']) or 'configuration'}: {err['msg']}" for err in e.errors()]
            raise UserException(f"Validation Error: {', '.join(msgs)}")
        if self.debug:
            logging.debug("Component will run in Debug mode")

    @model_validator(mode="after")
    def _require_incremental_field_when_filtering(self):
        # A `where` filter is built whenever incremental load or a date bound is set; it needs a
        # non-empty field name, otherwise the query would be malformed.
        if (self.is_incremental or self.date_from or self.date_to) and not self.incremental_field.strip():
            raise ValueError("incremental_field must be set for incremental load or when using date_from/date_to.")
        return self

    @property
    def is_incremental(self) -> bool:
        return self.load_type == "incremental_load"

    model_config = {"populate_by_name": True}
