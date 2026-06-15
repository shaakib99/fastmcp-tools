from pydantic import BaseModel, Field


class QueryParams(BaseModel):
    q: str = Field("", description="Search query")
    limit: int = Field(10, description="Number of records to be pulled")
    skip: int = Field(0, description="Number of records to be skipped")