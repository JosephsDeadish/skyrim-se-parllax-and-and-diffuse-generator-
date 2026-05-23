"""Generate assets/panda.ico for use as the PyInstaller application icon."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generate_textures import _create_panda_icon_image

_SIZES = [16, 32, 48, 64, 128, 256]

out_path = Path(__file__).parent / "panda.ico"
frames = [_create_panda_icon_image(size=s) for s in _SIZES]
frames[0].save(
    out_path,
    format="ICO",
    sizes=[(s, s) for s in _SIZES],
    append_images=frames[1:],
)
print(f"Written: {out_path}")
