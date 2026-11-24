from pydantic import BaseModel, Field


class SaveRequest(BaseModel):
    saved: bool = True


class SearchRequest(BaseModel):
    role: str = Field(min_length=1)
    experience: float = Field(ge=0, le=60)
    location: str = Field(min_length=1)
    days: int | None = Field(default=None, ge=1, le=60)
