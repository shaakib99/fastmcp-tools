from pydantic import BaseModel, Field


class QueryParams(BaseModel):
    q: str = Field("", description="Search query")
    limit: int = Field(10, description="Number of records to be pulled")
    skip: int = Field(0, description="Number of records to be skipped")

class Action(BaseModel):
    action: str = Field("", description="Action such as fill, click. Valid value are fill, click")
    tag: str = Field("", description='HTML field tag, that can be used to fill any specific input or click any specific button')
    value: str = Field("", description="If any input field needs any value, this field will be populated")

class WebsiteInteractionModel(BaseModel):
    url: str = Field("", description="URL of the specific website")
    actions_by_sequence: list[Action] = Field([], description='''List of sequential action that playwright can execute. 
                                              Example [Action("fill", "#username", "test")]''')