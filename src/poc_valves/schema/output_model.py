from __future__ import annotations

from typing import Optional, Tuple
import re
import pandas as pd
from pydantic import BaseModel, Field, create_model


def _sanitize_field_name(name: str) -> str:
    # lowercase, replace non-alphanum with underscore, collapse underscores
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip())
    s = re.sub(r"_+", "_", s)
    if not s:
        s = "field"
    if s[0].isdigit():
        s = "f_" + s
    return s.lower()


def build_output_model(xlsx_path: str) -> Tuple[type[BaseModel], dict[str, str]]:
    """Builds and returns a dynamic Pydantic model class based on the
    `Sv2 Field Name` column from the provided Excel file. Also returns a
    mapping of original Sv2 names -> sanitized python attribute names.

    The model fields are annotated as Optional[str] and include `description`
    metadata taken from a description-like column if present.
    """
    df = pd.read_excel(xlsx_path, sheet_name=0)
    # normalize column names
    cols = {c: " ".join(str(c).split()).strip() for c in df.columns}
    df.rename(columns=cols, inplace=True)

    # heuristics for description column
    desc_col = None
    for candidate in ("Description", "Field Description", "Notes", "Enquiry Field Name (Customer Spec)"):
        if candidate in df.columns:
            desc_col = candidate
            break

    fields = {}
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        sv2_name = str(row.get("Sv2 Field Name") or "").strip()
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

        fields[py_name] = (Optional[str], Field(None, description=str(desc)))
        mapping[sv2_name] = py_name

    Model = create_model("SV2OutputModel", __base__=BaseModel, **fields)
    return Model, mapping


def generate_static_model_file(xlsx_path: str, out_py: str) -> None:
    """Generates a static Python file containing a Pydantic model class
    `SV2OutputModel` with fields derived from the given excel file.
    """
    # generate via build_output_model and write file
    Model, mapping = build_output_model(xlsx_path)
    with open(out_py, "w", encoding="utf-8") as fh:
        fh.write('from __future__ import annotations\n')
        fh.write('from typing import Optional\n')
        fh.write('from pydantic import BaseModel, Field\n\n\n')
        fh.write('class SV2OutputModel(BaseModel):\n')
        for py_name, model_field in Model.__fields__.items():
            desc = model_field.field_info.description or ""
            desc_escaped = desc.replace('\n', ' ').replace('"', '\"')
            fh.write(f"    {py_name}: Optional[str] = Field(None, description=\"{desc_escaped}\")\n")
        fh.write('\n')
