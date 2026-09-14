"""Find a sheet's records in the intermediate model from its layout's dotted `source` path."""

import types
import typing
from typing import Any

from pydantic import BaseModel

from backend.models.extraction import ExtractedField, ExtractionSheets
from backend.pipeline.workbook.layout import SheetKind, SheetSpec


def _annotation(model: type[BaseModel], attribute: str) -> Any:
    return model.model_fields[attribute].annotation


def _strip_optional(annotation: Any) -> Any:
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def record_class(spec: SheetSpec) -> type[BaseModel]:
    """The record model of a sheet: the list item type for tables, the model for key/value."""
    model: type[BaseModel] = ExtractionSheets
    annotation: Any = None
    for part in spec.source.split("."):
        annotation = _strip_optional(_annotation(model, part))
        if typing.get_origin(annotation) is list:
            annotation = typing.get_args(annotation)[0]
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            model = annotation
    if not (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
        raise TypeError(f"{spec.source} does not lead to a record model")
    return annotation


def sheet_rows(sheets: ExtractionSheets, spec: SheetSpec) -> list[Any]:
    """The records shown on a sheet: one object for key/value sheets, the list for tables."""
    obj: Any = sheets
    for part in spec.source.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return []
    if spec.kind == SheetKind.KEY_VALUE:
        return [obj]
    return obj  # type: ignore[no-any-return]


def ensure_rows(sheets: ExtractionSheets, spec: SheetSpec) -> list[Any] | None:
    """The sheet's list, creating empty containers on the way; None if a required parent record
    (such as the study record that holds governance dates) does not exist."""
    obj: Any = sheets
    model: type[BaseModel] = ExtractionSheets
    parts = spec.source.split(".")
    for i, part in enumerate(parts):
        annotation = _strip_optional(_annotation(model, part))
        value = getattr(obj, part)
        last = i == len(parts) - 1
        if value is None:
            if last and typing.get_origin(annotation) is list:
                value = []
            elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
                try:
                    value = annotation()
                except Exception:
                    return None  # a record with required fields cannot be invented
            else:
                return None
            setattr(obj, part, value)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            model = annotation
        obj = value
    return obj if isinstance(obj, list) else None


def empty_record(spec: SheetSpec, **values: Any) -> BaseModel:
    """A record of the sheet with every field empty (row_id and nested lists at their defaults)."""
    cls = record_class(spec)
    fields: dict[str, Any] = {
        name: ExtractedField()
        for name, info in cls.model_fields.items()
        if isinstance(info.annotation, type) and issubclass(info.annotation, ExtractedField)
    }
    fields.update(values)
    return cls(**fields)


def group_active(spec: SheetSpec, row: Any, group: str) -> bool:
    return any(not getattr(row, c.field).is_empty for c in spec.group_columns(group))  # type: ignore[arg-type]
