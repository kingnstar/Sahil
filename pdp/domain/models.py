from pydantic import BaseModel, ConfigDict, Field
from typing import Any


class ProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=60)
    description: str = Field(default="", max_length=500)


class ColumnInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)
    field_type: str = "TEXT"
    options: list[str] = Field(default_factory=list)


class RecordInput(BaseModel):
    values: dict[str, Any]
    expected_version: int | None = None
