"""Base intelligence module interface per system data contracts."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.core.enums import QualityStatus
from src.core.models import ModuleSignal


@dataclass
class ModuleContext:
    """Execution context for an intelligence module evaluation."""
    instrument_ids: list[str]
    horizon: str
    decision_cutoff: datetime
    run_id: str
    config_version: str = "0.1.0"


@dataclass
class ModuleResult:
    """Output envelope for an intelligence run."""
    status: str  # "success", "partial", "failed"
    module_name: str
    signals: list[ModuleSignal] = field(default_factory=list)
    feature_snapshot_ids: list[str] = field(default_factory=list)
    quality_status: QualityStatus = QualityStatus.VALID
    diagnostics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class BaseModule(ABC):
    """Abstract interface for all intelligence modules."""

    def __init__(self, name: str, version: str = "0.1.0"):
        self.name = name
        self.version = version

    @abstractmethod
    def evaluate(
        self,
        context: ModuleContext,
        session: Any,
    ) -> ModuleResult:
        """Run deterministic or enriched evaluation."""
        pass
