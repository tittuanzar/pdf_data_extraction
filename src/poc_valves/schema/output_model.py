from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

import yaml
from pydantic import BaseModel, Field, create_model


def _sanitize_field_name(name: str) -> str:
    """Sanitize a field name for use as a Python attribute."""
    # lowercase, replace non-alphanum with underscore, collapse underscores
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip())
    s = re.sub(r"_+", "_", s)
    if not s:
        s = "field"
    if s[0].isdigit():
        s = "f_" + s
    return s.lower()


def _build_model_from_fields(
    fields_data: list[dict],
) -> Tuple[type[BaseModel], dict[str, str]]:
    """Build a dynamic Pydantic model from a list of field dicts.

    Each dict must have at least 'sv2_field_name'. An optional 'logic'
    or 'source.enquiry_field_name' key is used as the field description.
    """
    fields = {}
    mapping: dict[str, str] = {}
    for entry in fields_data:
        sv2_name = str(entry.get("sv2_field_name") or "").strip()
        if not sv2_name:
            continue
        py_name = _sanitize_field_name(sv2_name)
        # ensure unique
        orig_py = py_name
        i = 1
        while py_name in fields:
            py_name = f"{orig_py}_{i}"
            i += 1

        # build description from available metadata
        desc = entry.get("logic")
        if not desc:
            source = entry.get("source", {}) if isinstance(entry.get("source"), dict) else {}
            desc = source.get("enquiry_field_name")
        if not desc:
            desc = f"Field mapped from '{sv2_name}'"

        fields[py_name] = (
            Optional[str],
            Field(None, description=str(desc)),
        )
        mapping[sv2_name] = py_name

    Model = create_model(
        "SV2OutputModel",
        __base__=BaseModel,
        **fields,
    )
    return Model, mapping


def build_output_model(
    ref_path: str,
) -> Tuple[type[BaseModel], dict[str, str]]:
    """Build a dynamic Pydantic model from a reference file.

    Supports both YAML (.yml/.yaml) and Excel (.xlsx) files.

    For YAML: reads the ``document_extraction_fields`` list and uses
    ``sv2_field_name`` as the field name and ``logic`` as the description.

    For Excel: reads the ``Sv2 Field Name`` column and a description-like
    column if present.
    """
    path = Path(ref_path)

    if path.suffix in (".yml", ".yaml"):
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        fields_data = data.get("document_extraction_fields", []) if isinstance(data, dict) else []
        return _build_model_from_fields(fields_data)

    # Fallback: read from Excel (backward compatibility)
    import pandas as pd

    df = pd.read_excel(ref_path, sheet_name=0)
    # normalize column names
    cols = {
        c: " ".join(str(c).split()).strip()
        for c in df.columns
    }
    df.rename(columns=cols, inplace=True)

    # heuristics for description column
    desc_col = None
    for candidate in (
        "Description",
        "Field Description",
        "Notes",
        "Enquiry Field Name (Customer Spec)",
    ):
        if candidate in df.columns:
            desc_col = candidate
            break

    fields = {}
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        sv2_name = str(
            row.get("Sv2 Field Name") or ""
        ).strip()
        if not sv2_name:
            continue
        py_name = _sanitize_field_name(sv2_name)
        # ensure unique
        orig_py = py_name
        i = 1
        while py_name in fields:
            py_name = f"{orig_py}_{i}"
            i += 1

        desc = None
        if desc_col:
            desc = row.get(desc_col)
            if pd.isna(desc):
                desc = None
        if desc is None:
            desc = f"Field mapped from '{sv2_name}'"

        fields[py_name] = (
            Optional[str],
            Field(None, description=str(desc)),
        )
        mapping[sv2_name] = py_name

    Model = create_model(
        "SV2OutputModel",
        __base__=BaseModel,
        **fields,
    )
    return Model, mapping


def generate_static_model_file(
    ref_path: str, out_py: str
) -> None:
    """Generate a static Python file with a Pydantic model.

    Generates a static Python file containing a Pydantic model class
    `SV2OutputModel` with fields derived from the given reference file.
    """
    # generate via build_output_model and write file
    Model, mapping = build_output_model(ref_path)
    with open(out_py, "w", encoding="utf-8") as fh:
        fh.write("from __future__ import annotations\n")
        fh.write("from typing import Optional\n")
        fh.write("from pydantic import BaseModel, Field\n\n\n")
        fh.write("class SV2OutputModel(BaseModel):\n")
        for py_name, model_field in Model.__fields__.items():
            desc = model_field.field_info.description or ""
            desc_escaped = desc.replace(
                "\n", " "
            ).replace('"', '\\"')
            fh.write(
                f"    {py_name}: Optional[str] = "
                f'Field(None, description="{desc_escaped}")\n'
            )
        fh.write("\n")
