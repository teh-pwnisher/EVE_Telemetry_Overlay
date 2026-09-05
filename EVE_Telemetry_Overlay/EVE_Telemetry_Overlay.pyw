import ctypes
from ctypes import wintypes
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import psutil

APP_NAME = "EVE Telemetry Overlay"

DEFAULT_CONFIG = {
    "x": 25, "y": 80, "width": 720, "height": 980,
    "font_family": "Consolas", "font_size": 9,
    "foreground": "#79ff79", "transparent_key": "#010203",
    "always_on_top": True, "click_through": False,
    "refresh_ms": 450, "eve_poll_ms": 120, "max_lines": 220,
    "show_gpu": True, "gpu_poll_seconds": 4,
    "show_process_lines": True, "process_poll_seconds": 5,
    "show_windows_noise": True, "windows_noise_seconds": 8
}

def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

CONFIG_PATH = app_dir() / "config.json"

def load_config():
    cfg = DEFAULT_CONFIG.copy()
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        else:
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass
    return cfg

CFG = load_config()

def documents_path():
    if os.name == "nt":
        CSIDL_PERSONAL = 5
        SHGFP_TYPE_CURRENT = 0
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        try:
            ctypes.windll.shell32.SHGetFolderPathW(
                None, CSIDL_PERSONAL, None, SHGFP_TYPE_CURRENT, buf
            )
            if buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"

EVE_LOG_DIR = documents_path() / "EVE" / "logs" / "Gamelogs"

class EveTailer(threading.Thread):
    def __init__(self, output_q, stop_evt, poll_s):
        super().__init__(daemon=True)
        self.output_q = output_q
        self.stop_evt = stop_evt
        self.poll_s = poll_s
        self.offsets = {}

    def initialize(self):
        if not EVE_LOG_DIR.exists():
            self.output_q.put(f"[EVE::ERR] LOG_PATH_NOT_FOUND path={EVE_LOG_DIR}")
            return
        for p in EVE_LOG_DIR.glob("*.txt"):
            try:
                self.offsets[str(p)] = p.stat().st_size
            except OSError:
                pass
        self.output_q.put(f"[EVE::LINK] path={EVE_LOG_DIR} files={len(self.offsets)} mode=TAIL/RAW")

    def read_growth(self, p, start):
        try:
            size = p.stat().st_size
            if size < start:
                start = 0
            if size == start:
                return size
            with open(p, "rb") as f:
                f.seek(start)
                data = f.read()
            if not data:
                return size

            # EVE game logs are often UTF-16 LE; sniff BOM/null bytes.
            enc = "utf-8"
            if data.startswith(b"\xff\xfe") or b"\x00" in data[:200]:
                enc = "utf-16"
            text = data.decode(enc, errors="replace")
            for line in text.splitlines():
                if line:
                    self.output_q.put(line.replace("\x00", ""))
            return size
        except (OSError, PermissionError):
            return start

    def run(self):
        self.initialize()
        while not self.stop_evt.is_set():
            if EVE_LOG_DIR.exists():
                try:
                    current = {str(p): p for p in EVE_LOG_DIR.glob("*.txt")}
                    for key, p in current.items():
                        if key not in self.offsets:
                            self.offsets[key] = 0
                            try:
                                size = p.stat().st_size
                            except OSError:
                                size = 0
                            self.output_q.put(f"[EVE::FILE] OPEN name={p.name} size={size}")
                        self.offsets[key] = self.read_growth(p, self.offsets[key])
                except Exception as e:
                    self.output_q.put(f"[EVE::ERR] {type(e).__name__}: {e}")
            self.stop_evt.wait(self.poll_s)

class Telemetry:
    def __init__(self):
        psutil.cpu_percent(interval=None)
        self.last_net = psutil.net_io_counters()
        self.last_disk = psutil.disk_io_counters()
        self.last_time = time.monotonic()
        self.seq = 0
        self.last_gpu = 0.0
        self.gpu_cache = None
        self.last_proc = 0.0
        self.proc_cache = None
        self.last_noise = 0.0
        self.noise_index = 0
        self.hostname = platform.node() or "NODE"
        self.pid = os.getpid()

    def _freq(self):
        try:
            f = psutil.cpu_freq()
            return f.current if f else 0.0
        except Exception:
            return 0.0

    def sample_gpu(self):
        if not CFG.get("show_gpu", True):
            return None
        now = time.monotonic()
        if now - self.last_gpu < float(CFG.get("gpu_poll_seconds", 4)):
            return self.gpu_cache
        self.last_gpu = now
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                 "--format=csv,noheader,nounits"],
                text=True, stderr=subprocess.DEVNULL, timeout=1.5,
                creationflags=creationflags
            ).strip().splitlines()
            if out:
                p = [x.strip() for x in out[0].split(",")]
                if len(p) >= 7:
                    self.gpu_cache = (
                        f'GPU::{p[0]} name="{p[1]}" util={p[2]}% '
                        f'vram={p[3]}/{p[4]}MiB temp={p[5]}C power={p[6]}W'
                    )
                    return self.gpu_cache
        except Exception:
            self.gpu_cache = None
        return None

    def sample_top_process(self):
        if not CFG.get("show_process_lines", True):
            return None
        now = time.monotonic()
        if now - self.last_proc < float(CFG.get("process_poll_seconds", 5)):
            return self.proc_cache
        self.last_proc = now
        best = None
        try:
            for p in psutil.process_iter(["pid", "name", "memory_info", "num_threads"]):
                try:
                    mi = p.info["memory_info"]
                    rss = mi.rss if mi else 0
                    if best is None or rss > best[0]:
                        best = (rss, p.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if best:
                rss, i = best
                self.proc_cache = f"PROC::PID={i['pid']} NAME={i['name']} RSS={rss}B THREADS={i.get('num_threads','?')}"
                return self.proc_cache
        except Exception:
            pass
        return None

    def sample_noise(self):
        if not CFG.get("show_windows_noise", True):
            return None
        now = time.monotonic()
        if now - self.last_noise < float(CFG.get("windows_noise_seconds", 8)):
            return None
        self.last_noise = now
        options = [
            f"SYS::OS system={platform.system()} release={platform.release()} build={platform.version()} arch={platform.machine()} host={self.hostname}",
            f"SYS::BOOT epoch={psutil.boot_time():.3f} pid={self.pid} logical_cpu={psutil.cpu_count()} physical_cpu={psutil.cpu_count(logical=False)}"
        ]
        try:
            for name, addrs in list(psutil.net_if_addrs().items())[:3]:
                vals = [str(a.address) for a in addrs if a.address]
                if vals:
                    options.append(f'NET::IF name="{name}" addr=' + "|".join(vals))
        except Exception:
            pass
        out = options[self.noise_index % len(options)]
        self.noise_index += 1
        return out

    def next_line(self):
        mono = time.monotonic()
        dt = max(0.001, mono - self.last_time)
        self.last_time = mono
        now = time.time()

        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        net = psutil.net_io_counters()
        disk = psutil.disk_io_counters()
        freq = self._freq()

        rx = max(0.0, (net.bytes_recv - self.last_net.bytes_recv) / dt)
        tx = max(0.0, (net.bytes_sent - self.last_net.bytes_sent) / dt)
        self.last_net = net

        rd = wr = 0.0
        if disk and self.last_disk:
            rd = max(0.0, (disk.read_bytes - self.last_disk.read_bytes) / dt)
            wr = max(0.0, (disk.write_bytes - self.last_disk.write_bytes) / dt)
        self.last_disk = disk

        uptime = max(0, int(now - psutil.boot_time()))
        d, rem = divmod(uptime, 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)

        stamp = time.strftime("%H:%M:%S")
        prefix = f"[{stamp}.{int((now % 1) * 1000):03d}]"
        mode = self.seq % 8
        self.seq += 1

        if mode == 0:
            return f"{prefix} PERF::CPU load={cpu:.2f}% clock={freq:.0f}MHz logical={psutil.cpu_count()}"
        if mode == 1:
            return f"{prefix} PERF::MEM total={vm.total}B used={vm.used}B avail={vm.available}B percent={vm.percent:.2f}%"
        if mode == 2:
            return f"{prefix} NET::_Total RX={rx:.0f}B/s TX={tx:.0f}B/s recv_total={net.bytes_recv} sent_total={net.bytes_sent}"
        if mode == 3:
            return f"{prefix} IO::DISK read={rd:.0f}B/s write={wr:.0f}B/s rd_total={disk.read_bytes if disk else 0} wr_total={disk.write_bytes if disk else 0}"
        if mode == 4:
            st = psutil.cpu_stats()
            return f"{prefix} KERN::OBJ proc={len(psutil.pids())} ctx={getattr(st,'ctx_switches',0)} intr={getattr(st,'interrupts',0)}"
        if mode == 5:
            gpu = self.sample_gpu()
            return f"{prefix} {gpu}" if gpu else f"{prefix} SYS::UPTIME {d:02d}D:{h:02d}H:{m:02d}M:{s:02d}S"
        if mode == 6:
            proc = self.sample_top_process()
            return f"{prefix} {proc}" if proc else f"{prefix} SYS::UPTIME {d:02d}D:{h:02d}H:{m:02d}M:{s:02d}S"
        noise = self.sample_noise()
        return f"{prefix} {noise}" if noise else f"{prefix} SYS::UPTIME {d:02d}D:{h:02d}H:{m:02d}M:{s:02d}S"

class Overlay:
    HOTKEY_TOGGLE = 0xB001
    HOTKEY_EXIT = 0xB002
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    WM_HOTKEY = 0x0312
    PM_REMOVE = 0x0001

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.geometry(f"{CFG['width']}x{CFG['height']}+{CFG['x']}+{CFG['y']}")
        self.root.configure(bg=CFG["transparent_key"])
        try:
            self.root.wm_attributes("-transparentcolor", CFG["transparent_key"])
        except tk.TclError:
            pass
        self.root.attributes("-topmost", bool(CFG.get("always_on_top", True)))

        self.text = tk.Text(
            self.root,
            bg=CFG["transparent_key"],
            fg=CFG["foreground"],
            insertbackground=CFG["foreground"],
            font=(CFG["font_family"], int(CFG["font_size"])),
            wrap="none", bd=0, highlightthickness=0, relief="flat",
            padx=0, pady=0, cursor="arrow"
        )
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")

        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.telemetry = Telemetry()
        self.tailer = EveTailer(
            self.q, self.stop_evt,
            max(0.05, float(CFG.get("eve_poll_ms", 120)) / 1000.0)
        )
        self.tailer.start()

        self.locked = bool(CFG.get("click_through", False))
        self.drag_start = None

        self.text.bind("<ButtonPress-1>", self.start_drag)
        self.text.bind("<B1-Motion>", self.drag)
        self.text.bind("<Button-3>", self.popup_menu)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Lock / Click-through   Ctrl+Alt+T", command=self.toggle_clickthrough)
        self.menu.add_command(label="Save position", command=self.save_position)
        self.menu.add_separator()
        self.menu.add_command(label="Exit   Ctrl+Alt+Q", command=self.close)

        self.register_hotkeys()
        if self.locked:
            self.root.after(250, lambda: self.set_clickthrough(True))

        self.append(f"[SYS::INIT] {APP_NAME} pid={os.getpid()} node={platform.node()}")
        self.append(f"[SYS::CFG] source={CONFIG_PATH}")
        self.append(f"[EVE::CFG] log_root={EVE_LOG_DIR} raw_markup=TRUE sanitize=FALSE")

        self.root.after(int(CFG.get("refresh_ms", 450)), self.emit_telemetry)
        self.root.after(50, self.drain_queue)
        self.root.after(100, self.poll_hotkeys)

    def append(self, line):
        if line is None:
            return
        line = str(line).replace("\x00", "")
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        max_lines = int(CFG.get("max_lines", 220))
        try:
            total = int(self.text.index("end-1c").split(".")[0])
            if total > max_lines:
                self.text.delete("1.0", f"{total - max_lines}.0")
        except Exception:
            pass
        self.text.see("end")
        self.text.configure(state="disabled")

    def emit_telemetry(self):
        if self.stop_evt.is_set():
            return
        try:
            self.append(self.telemetry.next_line())
        except Exception as e:
            self.append(f"[SYS::ERR] {type(e).__name__}: {e}")
        self.root.after(int(CFG.get("refresh_ms", 450)), self.emit_telemetry)

    def drain_queue(self):
        if self.stop_evt.is_set():
            return
        for _ in range(100):
            try:
                self.append(self.q.get_nowait())
            except queue.Empty:
                break
        self.root.after(50, self.drain_queue)

    def start_drag(self, e):
        if not self.locked:
            self.drag_start = (e.x_root, e.y_root, self.root.winfo_x(), self.root.winfo_y())

    def drag(self, e):
        if self.locked or not self.drag_start:
            return
        sx, sy, wx, wy = self.drag_start
        self.root.geometry(f"+{wx + e.x_root - sx}+{wy + e.y_root - sy}")

    def popup_menu(self, e):
        if self.locked:
            return
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def hwnd(self):
        self.root.update_idletasks()
        hwnd = self.root.winfo_id()
        if os.name == "nt":
            p = ctypes.windll.user32.GetParent(hwnd)
            if p:
                hwnd = p
        return hwnd

    def set_clickthrough(self, enabled):
        self.locked = bool(enabled)
        if os.name != "nt":
            return
        try:
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            hwnd = self.hwnd()
            u = ctypes.windll.user32
            get_long = u.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.GetWindowLongW
            set_long = u.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.SetWindowLongW
            style = get_long(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED
            style = (style | WS_EX_TRANSPARENT) if enabled else (style & ~WS_EX_TRANSPARENT)
            set_long(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def toggle_clickthrough(self):
        self.set_clickthrough(not self.locked)

    def save_position(self):
        try:
            CFG["x"] = self.root.winfo_x()
            CFG["y"] = self.root.winfo_y()
            CFG["width"] = self.root.winfo_width()
            CFG["height"] = self.root.winfo_height()
            CFG["click_through"] = self.locked
            CONFIG_PATH.write_text(json.dumps(CFG, indent=2), encoding="utf-8")
            self.append(f"[SYS::CFG] SAVED path={CONFIG_PATH}")
        except Exception as e:
            self.append(f"[SYS::ERR] CONFIG_SAVE {e}")

    def register_hotkeys(self):
        if os.name == "nt":
            try:
                u = ctypes.windll.user32
                u.RegisterHotKey(None, self.HOTKEY_TOGGLE, self.MOD_CONTROL | self.MOD_ALT, ord("T"))
                u.RegisterHotKey(None, self.HOTKEY_EXIT, self.MOD_CONTROL | self.MOD_ALT, ord("Q"))
            except Exception:
                pass

    def unregister_hotkeys(self):
        if os.name == "nt":
            try:
                u = ctypes.windll.user32
                u.UnregisterHotKey(None, self.HOTKEY_TOGGLE)
                u.UnregisterHotKey(None, self.HOTKEY_EXIT)
            except Exception:
                pass

    def poll_hotkeys(self):
        if os.name == "nt":
            try:
                msg = wintypes.MSG()
                u = ctypes.windll.user32
                while u.PeekMessageW(ctypes.byref(msg), None, self.WM_HOTKEY, self.WM_HOTKEY, self.PM_REMOVE):
                    if msg.wParam == self.HOTKEY_TOGGLE:
                        self.toggle_clickthrough()
                    elif msg.wParam == self.HOTKEY_EXIT:
                        self.close()
                        return
            except Exception:
                pass
        if not self.stop_evt.is_set():
            self.root.after(100, self.poll_hotkeys)

    def close(self):
        if self.stop_evt.is_set():
            return
        self.stop_evt.set()
        self.unregister_hotkeys()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    Overlay().run()
