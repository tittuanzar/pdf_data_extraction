from typing import List, Optional

from pydantic import BaseModel, Field


class CategoryInfo(BaseModel):

    category: str

    confidence: float


class PageChunk(BaseModel):

    page_number: int

    content: str

    categories: List[CategoryInfo] = Field(
        default_factory=list
    )


class ExtractionResult(BaseModel):

    ext_id: str

    extracted_value: Optional[str] = None

    source_clause: Optional[str] = None

    source_page: Optional[int] = None

    source_document: Optional[str] = None

    extraction_basis: Optional[str] = None

    remarks_validation: Optional[str] = None
