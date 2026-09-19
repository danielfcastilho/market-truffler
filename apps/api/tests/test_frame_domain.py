from datetime import UTC, datetime

from app.domain.frame import FrameStatus, MarketFrame


def _frame(
    expected: int, available: int, status: FrameStatus = FrameStatus.COMPLETE
) -> MarketFrame:
    now = datetime.now(UTC)
    return MarketFrame(
        frame_time=now,
        expected_instruments=expected,
        available_instruments=available,
        status=status,
        created_at=now,
        finalized_at=now,
    )


def test_completeness_is_a_fraction_of_available_over_expected():
    assert _frame(771, 771).completeness == 1.0
    assert _frame(771, 385).completeness == 385 / 771


def test_completeness_never_silently_reduces_expected_to_hide_a_gap():
    frame = _frame(771, 769)
    assert frame.expected_instruments == 771
    assert frame.available_instruments == 769
    assert frame.completeness < 1.0


def test_completeness_is_vacuously_complete_when_nothing_was_expected():
    assert _frame(0, 0).completeness == 1.0


def test_frame_status_values_match_persisted_strings():
    assert FrameStatus.BUILDING.value == "building"
    assert FrameStatus.COMPLETE.value == "complete"
    assert FrameStatus.PARTIAL.value == "partial"
