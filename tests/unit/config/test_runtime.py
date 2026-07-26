from pathlib import Path

import pytest

from polis.config.errors import RuntimeOverlayError
from polis.config.runtime import RuntimeConfig
from polis.config.settings import load_settings


def test_runtime_overlay_is_temporal_and_order_independent() -> None:
    runtime = RuntimeConfig(load_settings(Path("configs/smoke.yaml")))
    assert runtime.get("salience.deliberate_share", 2) == 0.07
    runtime.enact("salience.deliberate_share", 0.1, 10, "p1", 4, enacted_tick=2)
    runtime.enact("salience.deliberate_share", 0.2, 5, "p2", 3, enacted_tick=2)
    assert runtime.get("salience.deliberate_share", 4) == 0.07
    assert runtime.get("salience.deliberate_share", 5) == 0.2
    assert runtime.get("salience.deliberate_share", 10) == 0.1


def test_runtime_overlay_rejects_retroactive_change() -> None:
    runtime = RuntimeConfig(load_settings(Path("configs/smoke.yaml")))
    with pytest.raises(RuntimeOverlayError):
        runtime.enact("salience.deliberate_share", 0.2, 4, "p", 1, enacted_tick=4)
