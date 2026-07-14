"""Tests for Parallel Signal Fusion pipeline integration."""

from __future__ import annotations

import numpy as np

from cuegrid.audio.detector import _fuse_energy
from cuegrid.config import AppConfig


def test_fuses_aligned_energy_vectors_with_configured_weights() -> None:
    master = np.array([1.0, 2.0, 3.0])
    drum = np.array([4.0, 5.0, 6.0])

    combined, aligned_drum = _fuse_energy(master, drum, 0.6, 0.4)

    np.testing.assert_allclose(combined, [2.2, 3.2, 4.2])
    np.testing.assert_array_equal(aligned_drum, drum)


def test_fusion_aligns_to_common_frame_count() -> None:
    combined, aligned_drum = _fuse_energy(
        np.array([1.0, 2.0, 3.0]), np.array([4.0, 5.0]), 0.6, 0.4
    )

    np.testing.assert_allclose(combined, [2.2, 3.2])
    np.testing.assert_array_equal(aligned_drum, [4.0, 5.0])


def test_standard_mode_does_not_process_a_drum_envelope() -> None:
    master = np.array([1.0, 2.0, 3.0])

    combined, aligned_drum = _fuse_energy(master, None, 0.6, 0.4)

    np.testing.assert_array_equal(combined, master)
    assert aligned_drum is None
    config = AppConfig()
    assert config.master_weight == 0.6
    assert config.drum_weight == 0.4
