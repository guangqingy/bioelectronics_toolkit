from __future__ import annotations

from pydantic import Field

from .request_validation import OptFloat, RequestModel


class EchemTokenScanRequest(RequestModel):
    """Parse a folder of recording filenames into tokens and facets."""

    folder: str = ""
    paths: list[str] = Field(default_factory=list)
    limit: int = Field(default=5000, ge=1, le=5000)


class EchemPulseMetricsRequest(RequestModel):
    """Quantify one chronoamperometry recording."""

    path: str = Field(min_length=1)
    preset: str = "auto"
    polarity: str | None = None
    signed: bool = False
    baseline_ms: OptFloat = None
    post_fraction: OptFloat = None
    post_cap_ms: OptFloat = None
    edge_exclusion_s: OptFloat = None
    threshold_mad: OptFloat = None
    minimum_gap_s: OptFloat = None
    detrend_window_s: OptFloat = None
    electrode_area_cm2: float = Field(default=0.25, gt=0)


class EchemCycleMetricsRequest(RequestModel):
    """Quantify one chronopotentiometry recording."""

    path: str = Field(min_length=1)
    preset: str = "auto"
    expected_period_s: OptFloat = None
    edge_exclusion_s: OptFloat = None
    threshold_mad: OptFloat = None
    electrode_area_cm2: float = Field(default=0.25, gt=0)


class EchemBatchMetricsRequest(RequestModel):
    """Quantify many recordings and return one tokenized row per file."""

    paths: list[str] = Field(default_factory=list)
    folder: str = ""
    preset: str = "auto"
    signed: bool = False
    expected_period_s: OptFloat = None
    electrode_area_cm2: float = Field(default=0.25, gt=0)
    cv_window_low_V: float = -0.25
    cv_window_high_V: float = -0.12
    cv_edge_guard_V: float = Field(default=0.02, ge=0)
    limit: int = Field(default=5000, ge=1, le=5000)
