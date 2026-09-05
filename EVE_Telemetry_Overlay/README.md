# EVE Telemetry Overlay

Borderless transparent Windows desktop telemetry + raw EVE Online log stream.

It displays only genuine data:
- CPU
- memory
- network RX/TX
- disk I/O
- process/kernel data
- NVIDIA GPU/VRAM/temp/power when `nvidia-smi` is available
- occasional real Windows/network metadata
- RAW EVE game-log lines, including `<color>`, `<font>`, `<b>` and other markup

No fake sci-fi filler is generated.

## One-time EXE build

Double-click `BUILD_EXE.bat`.

It will create:

`dist\EVE-Telemetry-Overlay.exe`

After that, just double-click the EXE whenever you want the overlay.

## Controls

- Left-drag: move it while unlocked
- Right-click: controls
- Ctrl+Alt+T: lock/unlock click-through
- Ctrl+Alt+Q: exit
- Right-click > Save position: writes current position/size to `config.json`

## EVE log location

The app resolves your real Windows Documents folder and watches:

`Documents\EVE\logs\Gamelogs`

Existing files start at EOF so it does not dump all old history at startup.
A newly created session log is tailed from the beginning.

## Configuration

Edit `config.json` next to the program.

Key fields:
- `x`, `y`, `width`, `height`
- `font_family`, `font_size`
- `foreground`
- `refresh_ms`
- `eve_poll_ms`
- `max_lines`
- `always_on_top`
- `click_through`
- `show_gpu`
- `show_process_lines`
- `show_windows_noise`
