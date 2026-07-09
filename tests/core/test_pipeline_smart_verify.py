"""Tests for the v2.2 Smart Validation Gating addition to ``core.pipeline``.

Covers ``_classify_events_against_master``/``_apply_smart_classification``
(spec section 10.2): classifying confirmed events as "Drop (Rhythm)" or
"Breakdown (Melodic)" by comparing a small window of the drum stem against
the same window of the original Master audio, and gating the whole pass
on ``config.verify == "smart"`` plus a real (non-``None``) temp stem WAV.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from traktorco.audio.detector import DetectedEvent
from traktorco.config import AppConfig
from traktorco.core.pipeline import (
    _LABEL_BREAKDOWN_MELODIC,
    _LABEL_DROP_RHYTHM,
    _apply_smart_classification,
    _classify_events_against_master,
)
from traktorco.nml.constants import CueType
from traktorco.nml.models import CuePoint


def _event(time_ms: float) -> DetectedEvent:
    return DetectedEvent(
        label="cue",
        time_ms=time_ms,
        beat_index=0,
        is_major_phrase=False,
        confidence=1.0,
    )


def _cue(time_ms: float, hotcue: int = 0) -> CuePoint:
    return CuePoint(name="Cue", type=CueType.CUE, start_ms=time_ms, hotcue=hotcue)


class TestClassifyEventsAgainstMaster:
    def test_high_drum_and_high_master_is_drop_rhythm(self):
        events = [_event(1000.0)]
        # drum_rms high, master_rms high -> Drop (Rhythm)
        with patch("traktorco.core.pipeline._window_rms", side_effect=[0.5, 0.5]):
            labels = _classify_events_against_master(events, "drum.wav", "master.wav")

        assert labels == {1000.0: _LABEL_DROP_RHYTHM}

    def test_low_drum_and_high_master_is_breakdown_melodic(self):
        events = [_event(2000.0)]
        # drum_rms low, master_rms high -> Breakdown (Melodic)
        with patch("traktorco.core.pipeline._window_rms", side_effect=[0.0, 0.5]):
            labels = _classify_events_against_master(events, "drum.wav", "master.wav")

        assert labels == {2000.0: _LABEL_BREAKDOWN_MELODIC}

    def test_low_drum_and_low_master_is_unclassified(self):
        events = [_event(3000.0)]
        with patch("traktorco.core.pipeline._window_rms", side_effect=[0.0, 0.0]):
            labels = _classify_events_against_master(events, "drum.wav", "master.wav")

        assert labels == {}

    def test_high_drum_and_low_master_is_unclassified(self):
        # Should not happen in practice (drum stem is a subset of the
        # mix), but must not crash or mislabel.
        events = [_event(4000.0)]
        with patch("traktorco.core.pipeline._window_rms", side_effect=[0.5, 0.0]):
            labels = _classify_events_against_master(events, "drum.wav", "master.wav")

        assert labels == {}

    def test_classifies_each_event_independently(self):
        events = [_event(1000.0), _event(2000.0)]
        with patch(
            "traktorco.core.pipeline._window_rms",
            side_effect=[0.5, 0.5, 0.0, 0.5],  # event1: drop, event2: breakdown
        ):
            labels = _classify_events_against_master(events, "drum.wav", "master.wav")

        assert labels == {
            1000.0: _LABEL_DROP_RHYTHM,
            2000.0: _LABEL_BREAKDOWN_MELODIC,
        }


class TestApplySmartClassification:
    def test_noop_when_verify_is_fast(self):
        events = [_event(1000.0)]
        cues = [_cue(1000.0)]
        config = AppConfig(verify="fast")

        with patch(
            "traktorco.core.pipeline._classify_events_against_master"
        ) as mock_cls:
            _apply_smart_classification(
                events, cues, "drum.wav", Path("drum.wav"), "master.wav", config
            )

        mock_cls.assert_not_called()
        assert cues[0].name == "Cue"

    def test_noop_when_no_temp_stem_wav(self):
        events = [_event(1000.0)]
        cues = [_cue(1000.0)]
        config = AppConfig(verify="smart")

        with patch(
            "traktorco.core.pipeline._classify_events_against_master"
        ) as mock_cls:
            _apply_smart_classification(
                events, cues, "master.wav", None, "master.wav", config
            )

        mock_cls.assert_not_called()
        assert cues[0].name == "Cue"

    def test_relabels_matching_cue_when_smart_and_stem_present(self):
        events = [_event(1000.0)]
        cues = [_cue(1000.0)]
        config = AppConfig(verify="smart")

        with patch(
            "traktorco.core.pipeline._classify_events_against_master",
            return_value={1000.0: _LABEL_DROP_RHYTHM},
        ):
            _apply_smart_classification(
                events, cues, "drum.wav", Path("drum.wav"), "master.wav", config
            )

        assert cues[0].name == _LABEL_DROP_RHYTHM

    def test_leaves_unclassified_cues_untouched(self):
        events = [_event(1000.0), _event(2000.0)]
        cues = [_cue(1000.0, hotcue=0), _cue(2000.0, hotcue=1)]
        config = AppConfig(verify="smart")

        with patch(
            "traktorco.core.pipeline._classify_events_against_master",
            return_value={1000.0: _LABEL_BREAKDOWN_MELODIC},
        ):
            _apply_smart_classification(
                events, cues, "drum.wav", Path("drum.wav"), "master.wav", config
            )

        assert cues[0].name == _LABEL_BREAKDOWN_MELODIC
        assert cues[1].name == "Cue"  # untouched
