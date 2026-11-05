from pydantic import BaseModel, ConfigDict, Field

class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_count: int = Field(ge=1, le=100)
    niche: str = Field(default="Technology", min_length=2, max_length=100)

class CampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    brand_name: str = Field(default="DevFlow AI", min_length=1, max_length=200)

class MessageUpdate(BaseModel):
    subject: str | None = None
    email_body: str | None = None
    dm_body: str | None = None

class SendRequest(BaseModel):
    message_ids: list[int] = Field(min_length=1, max_length=100)
    live_confirmed: bool = False
