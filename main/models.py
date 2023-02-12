from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, NamedTuple
from urllib.parse import quote_from_bytes

from dateparser import parse
from pydantic import BaseModel, validator, Field


class TimestampType(str, Enum):
    """
    The timestamp-to-use defines how to filter down images for deletion.
    """
    UPDATED_AT = 'updated_at'
    CREATED_AT = 'created_at'


class AccountType(str, Enum):
    """
    The user's account type defines which endpoints to use.
    """
    ORG = 'org'
    PERSONAL = 'personal'


class PackageResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    updated_at: datetime | None


class ContainerModel(BaseModel):
    tags: list[str]


class MetadataModel(BaseModel):
    package_type: Literal['container']
    container: ContainerModel


class PackageVersionResponse(BaseModel):
    id: int
    name: str
    metadata: MetadataModel
    created_at: datetime | None
    updated_at: datetime | None


class Inputs(BaseModel):
    """
    Parses the action input from sys.argv.
    """
    image_names: list[str]
    cut_off: datetime
    timestamp_to_use: TimestampType
    account_type: AccountType
    org_name: str | None
    untagged_only: bool
    skip_tags: list[str]
    keep_at_least: int = Field(ge=0)
    filter_tags: list[str]
    filter_include_untagged: bool = True

    @validator('skip_tags', 'filter_tags', 'image_names', pre=True)
    def parse_comma_separate_string_as_list(cls, v: str) -> list[str]:
        return [i.strip() for i in v.split(',')] if v else []

    @validator('cut_off', pre=True)
    def parse_human_readable_datetime(cls, v: str) -> datetime:
        parsed_cutoff = parse(v)
        if not parsed_cutoff:
            raise ValueError(f"Unable to parse '{v}'")
        elif parsed_cutoff.tzinfo is None or parsed_cutoff.tzinfo.utcoffset(parsed_cutoff) is None:
            raise ValueError('Timezone is required for the cut-off')
        return parsed_cutoff

    @validator('org_name', pre=True)
    def validate_org_name(cls, v: str, values: dict) -> str | None:
        if values['account_type'] == AccountType.ORG and not v:
            raise ValueError('org-name is required when account-type is org')
        if v:
            return v
        return None
