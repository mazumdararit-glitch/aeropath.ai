"""Accuracy metrics for the Phase 0 gate.

The gate exists because the PRD names the street-level model as both the main
differentiator and the main accuracy risk, but never says how accurate is
accurate enough. These are the numbers that decide whether Phase 1 starts.

Two metrics, deliberately:

* **MAPE** answers "how far off is the number", and is compared against the raw
  provider value so the street-level model has to *earn* its place.
* **Category agreement** answers "how often does the user see the right colour",
  which is what the product actually promises. A model can improve MAPE while
  making the colour worse, and vice versa, so neither alone is sufficient.

Percentage error is undefined when the reference is zero, and the commonly-used
``|pred - actual| / actual`` blows up near zero for a pollutant that is
essentially absent. Reference values below a floor are therefore excluded from
MAPE and counted separately, rather than silently inflating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aeropath.schemas.common import AQCategory
from aeropath.services.scoring import categories

# Below this reference value a percentage error is dominated by rounding.
REFERENCE_FLOOR = 5.0


@dataclass(frozen=True, slots=True)
class Pair:
    predicted: float
    actual: float


@dataclass(frozen=True, slots=True)
class ErrorStats:
    name: str
    n: int
    mape: float
    bias: float
    rmse: float
    excluded: int

    @property
    def is_empty(self) -> bool:
        return self.n == 0


@dataclass(frozen=True, slots=True)
class CategoryAgreement:
    total: int
    agreed: int
    confusion: dict[tuple[AQCategory, AQCategory], int] = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return self.agreed / self.total if self.total else 0.0

    @property
    def worst_confusions(self) -> list[tuple[AQCategory, AQCategory, int]]:
        """Off-diagonal cells, worst first. A two-band miss is a trust failure."""

        def distance(pair: tuple[AQCategory, AQCategory]) -> int:
            predicted, actual = pair
            return abs(categories.severity_index(predicted) - categories.severity_index(actual))

        off = [
            (predicted, actual, count)
            for (predicted, actual), count in self.confusion.items()
            if predicted is not actual
        ]
        return sorted(off, key=lambda item: (-distance((item[0], item[1])), -item[2]))


def error_stats(name: str, pairs: list[Pair], *, floor: float = REFERENCE_FLOOR) -> ErrorStats:
    """MAPE, bias and RMSE against a reference series."""
    usable = [p for p in pairs if p.actual >= floor]
    excluded = len(pairs) - len(usable)
    if not usable:
        return ErrorStats(name=name, n=0, mape=0.0, bias=0.0, rmse=0.0, excluded=excluded)

    abs_pct = [abs(p.predicted - p.actual) / p.actual for p in usable]
    signed = [(p.predicted - p.actual) / p.actual for p in usable]
    squared = [(p.predicted - p.actual) ** 2 for p in usable]

    return ErrorStats(
        name=name,
        n=len(usable),
        mape=100.0 * sum(abs_pct) / len(usable),
        bias=100.0 * sum(signed) / len(usable),
        rmse=(sum(squared) / len(usable)) ** 0.5,
        excluded=excluded,
    )


def relative_improvement(baseline: ErrorStats, candidate: ErrorStats) -> float:
    """Percent MAPE reduction of ``candidate`` over ``baseline``.

    Returns 0.0 when the baseline has no usable pairs, so an improvement cannot
    be claimed from an empty comparison.
    """
    if baseline.is_empty or candidate.is_empty or baseline.mape == 0:
        return 0.0
    return 100.0 * (baseline.mape - candidate.mape) / baseline.mape


def category_agreement(pairs: list[tuple[float, float]]) -> CategoryAgreement:
    """How often the estimated category matches the reference category."""
    confusion: dict[tuple[AQCategory, AQCategory], int] = {}
    agreed = 0
    for predicted, actual in pairs:
        predicted_cat = categories.classify(predicted)
        actual_cat = categories.classify(actual)
        key = (predicted_cat, actual_cat)
        confusion[key] = confusion.get(key, 0) + 1
        if predicted_cat is actual_cat:
            agreed += 1
    return CategoryAgreement(total=len(pairs), agreed=agreed, confusion=confusion)


@dataclass(frozen=True, slots=True)
class GateResult:
    """The Phase 0 verdict.

    Both conditions must hold, and both are **relative to the raw baseline**.
    Requiring the model to beat the raw provider is what stops a rules engine
    that is merely *different* from shipping as if it were better.

    The category condition used to be an absolute 70%. That was miscalibrated:
    raw spatial interpolation itself only reaches ~66% agreement on the CPCB
    network, because the network is sparser than the phenomenon. An absolute
    target therefore measures the network's own spatial noise, not the model.
    Asking instead for a *gain over the baseline that is evaluated on the same
    samples* is the same question the MAPE condition asks, and it is answerable
    at any network density.

    ``min_baseline_agreement`` guards the inverse failure: a baseline so weak
    that beating it proves nothing would otherwise turn a relative win into a
    spurious PASS.
    """

    category_agreement: float
    adjusted_mape: float
    raw_mape: float
    raw_category_agreement: float
    min_category_gain: float = 0.05
    min_relative_improvement: float = 5.0
    min_baseline_agreement: float = 0.50
    samples: int = 0

    @property
    def improvement(self) -> float:
        if self.raw_mape == 0:
            return 0.0
        return 100.0 * (self.raw_mape - self.adjusted_mape) / self.raw_mape

    @property
    def category_gain(self) -> float:
        """Agreement gained over raw, as a fraction (0.05 is five points)."""
        return self.category_agreement - self.raw_category_agreement

    @property
    def baseline_informative(self) -> bool:
        return self.raw_category_agreement >= self.min_baseline_agreement

    @property
    def category_pass(self) -> bool:
        return self.category_gain >= self.min_category_gain

    @property
    def improvement_pass(self) -> bool:
        return self.improvement >= self.min_relative_improvement

    @property
    def passed(self) -> bool:
        return (
            self.samples > 0
            and self.baseline_informative
            and self.category_pass
            and self.improvement_pass
        )

    def verdict(self) -> str:
        if self.samples == 0:
            return "INCONCLUSIVE: no comparable samples"
        if not self.baseline_informative:
            return (
                f"INCONCLUSIVE: raw baseline agreement "
                f"{self.raw_category_agreement:.1%} "
                f"< {self.min_baseline_agreement:.0%}; a relative gain "
                f"against a baseline this weak would not be meaningful"
            )
        if self.passed:
            return "PASS"
        failures = []
        if not self.category_pass:
            failures.append(
                f"category agreement gain {self.category_gain * 100:+.1f}pts "
                f"< {self.min_category_gain * 100:.0f}pts"
            )
        if not self.improvement_pass:
            failures.append(
                f"MAPE improvement {self.improvement:+.1f}% < {self.min_relative_improvement:.0f}%"
            )
        return "FAIL: " + "; ".join(failures)
