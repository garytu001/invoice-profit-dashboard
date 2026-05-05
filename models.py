from pydantic import BaseModel, Field


class ConfirmPayload(BaseModel):
    source_filename: str = Field(default="manual-review")
    parsed: dict


class CostOverridePayload(BaseModel):
    product: str
    grade: str | None = None
    spec: str | None = None
    cost_per_unit: float
    cost_unit: str


class ProfitCalcPayload(BaseModel):
    invoice_id: int
    cost_overrides: list[CostOverridePayload] = Field(default_factory=list)


class CostRowPayload(BaseModel):
    product: str
    grade: str | None = None
    spec: str | None = None
    cost_per_unit: float
    cost_unit: str = "才"
    effective_from: str | None = None


class ItemCostOverridePayload(BaseModel):
    invoice_item_id: int
    cost_per_unit: float
    cost_unit: str