openpilot in simulator
=====================

openpilot implements a [bridge](run_bridge.py) that allows it to run in the [MetaDrive simulator](https://github.com/metadriverse/metadrive).

## Launching openpilot
First, start openpilot.
``` bash
# Run locally
./openpilot/tools/sim/launch_openpilot.sh
```

## Bridge usage
```
$ ./run_bridge.py -h
usage: run_bridge.py [-h] [--joystick] [--high_quality] [--dual_camera]
Bridge between the simulator and openpilot.

options:
  -h, --help            show this help message and exit
  --joystick
  --high_quality
  --dual_camera
```

#### Bridge Controls:
- To engage openpilot press 2, then press 1 to increase the speed and 2 to decrease.
- To disengage, press "S" (simulates a user brake)

#### All inputs:

```
| key  |   functionality       |
|------|-----------------------|
|  1   | Cruise Resume / Accel |
|  2   | Cruise Set    / Decel |
|  3   | Cruise Cancel         |
|  r   | Reset Simulation      |
|  i   | Toggle Ignition       |
|  q   | Exit all              |
| wasd | Control manually      |
```

## MetaDrive

### Launching Metadrive
Start bridge processes located in openpilot/tools/sim:
``` bash
./run_bridge.py
```
## macOS (UNVALIDATED-ON-MAC)

Support for running the MetaDrive bridge on macOS is being tracked in
[commaai/openpilot#33207](https://github.com/commaai/openpilot/issues/33207).

Setup notes:

1. Install openpilot's normal macOS dependencies (`tools/setup.sh` / `op.sh setup`),
   then install the simulator dependency (currently commented out in `pyproject.toml`'s
   `tools` extra — re-enable it; the existing marker already includes macOS):
   ``` bash
   uv sync --extra tools   # or: pip install "metadrive-simulator @ git+https://github.com/commaai/metadrive.git@minimal"
   ```
2. Rendering: MetaDrive/Panda3D cannot open a true offscreen window on macOS,
   so MetaDrive forces an onscreen Cocoa window even when `use_render` is False.
   A small window will appear while the bridge runs — this is expected.
   A GUI session is required; running over bare SSH without a logged-in window
   server will fail with "Could not open window".
3. Do NOT set `EGL_PLATFORM` / other Mesa-EGL environment variables used by the
   Linux CI job; EGL is Linux-only, macOS uses CGL (OpenGL 4.1 core).
4. `--joystick` is Linux-only (`/dev/input` + evdev). Use the keyboard controls.
5. Run the test the same way as Linux:
   ``` bash
   pytest -s openpilot/tools/sim/tests/test_metadrive_bridge.py
   ```

Known limitations to validate on real hardware: window focus stealing during
the test, performance on Apple Silicon vs. Linux, and clean shutdown (Ctrl-C).
