"""Run this under Wine Python to test the bundled Windows GStreamer runtime.

Prerequisites:
  - build_gstreamer.sh has been run (gstreamer/ exists)
  - build_pygobject.sh has been installed under Wine Python (gi module)
  - patch_pyd.py has been run on gi/_gi.pyd in site-packages

Usage:
  export WINEPREFIX=build/windows/wine
  wine python windows/test_gst_wine.py
"""
import os
import sys
import threading

# ── Resolve paths ──────────────────────────────────────────────────────────
# Under Wine, __file__ is like Z:\storage\...  Strip the drive letter so
# os.path operations are clean, then re-add Z: for env vars.
_script = os.path.dirname(__file__)
if len(_script) > 2 and _script[1] == ":":
    _script = _script[2:]  # strip drive letter
REPO = os.path.normpath(os.path.join(_script, ".."))
GST_ROOT = os.path.join(REPO, "gstreamer")
GST_BIN = os.path.join(GST_ROOT, "bin")
GST_PLUGINS = os.path.join(GST_ROOT, "lib", "gstreamer-1.0")
GST_TYPELIB = os.path.join(GST_ROOT, "lib", "girepository-1.0")

# Convert a Linux absolute path to a Windows-style path under Wine.
# GI / GStreamer loaders need backslashes in env vars (e.g. PATH).
win_path = lambda p: "Z:" + p.replace("/", "\\")

# ── Environment ────────────────────────────────────────────────────────────
os.environ["PATH"] = win_path(GST_BIN) + ";" + os.environ.get("PATH", "")
os.environ["GST_PLUGIN_SYSTEM_PATH"] = ""
os.environ["GST_PLUGIN_PATH"] = win_path(GST_PLUGINS)
os.environ["GI_TYPELIB_PATH"] = win_path(GST_TYPELIB)
os.environ["GST_REGISTRY_UPDATE"] = "no"
os.environ["GST_REGISTRY_FORK"] = "no"



# ── Imports ────────────────────────────────────────────────────────────────
import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import Gst, GstApp, GLib

# ── Tests ──────────────────────────────────────────────────────────────────
passed = 0
failed = 0

def check(ok: bool, msg: str):
    global passed, failed
    if ok:
        print(f"  PASS: {msg}")
        passed += 1
    else:
        print(f"  FAIL: {msg}")
        failed += 1

print("=== 1. Gst.init() ===")
sys.stdout.flush()
Gst.init(None)
print(f"  GStreamer {Gst.version_string()}")
check(True, "init")

print("\n=== 2. Critical elements ===")
for name in ["rtspsrc", "appsink", "decodebin", "h264parse", "openh264dec",
             "videoconvertscale", "rtph264depay", "jpegdec", "typefind",
             "queue", "capsfilter", "fakesink", "udpsrc", "multiqueue"]:
    f = Gst.ElementFactory.find(name)
    check(f is not None, f"element '{name}'")

print("\n=== 3. videotestsrc -> appsink (frame delivery) ===")
result = {"frames": 0}
def on_sample(sink):
    sample = sink.emit("pull-sample")
    if sample:
        result["frames"] += 1
    return Gst.FlowReturn.OK

p = Gst.parse_launch(
    "videotestsrc pattern=ball ! "
    "videoconvertscale ! video/x-raw,width=640,height=480,framerate=15/1 ! "
    "appsink name=sink emit-signals=true sync=false max-buffers=1"
)
sink = p.get_by_name("sink")
sink.connect("new-sample", on_sample)
p.set_state(Gst.State.PLAYING)
loop = GLib.MainLoop()
GLib.timeout_add(2000, loop.quit)
loop.run()
p.set_state(Gst.State.NULL)
check(result["frames"] > 0, f"got {result['frames']} frames in 2s")

print("\n=== 4. RTSP pipeline parse ===")
try:
    pipe = Gst.parse_launch(
        "rtspsrc location=rtsp://localhost:8554/live latency=500 ! "
        "queue ! rtph264depay ! h264parse ! openh264dec ! "
        "videoconvertscale ! video/x-raw,width=320,height=240 ! "
        "fakesink"
    )
    check(True, "RTSP pipeline parsed")
    pipe.set_state(Gst.State.NULL)
except Exception as e:
    check(False, f"RTSP pipeline parse: {e}")

print("\n=== 5. GLib MainLoop integration ===")
try:
    loop = GLib.MainLoop()
    t = threading.Thread(target=loop.run, daemon=True)
    t.start()
    GLib.timeout_add(100, loop.quit)
    t.join(timeout=2)
    check(True, "MainLoop thread start/stop")
except Exception as e:
    check(False, f"MainLoop: {e}")

# ── Summary ────────────────────────────────────────────────────────────────
print(f"\n{'='*40}")
print(f"  {passed}/{passed+failed} tests passed")
if failed == 0:
    print("  ALL TESTS PASSED")
else:
    print(f"  {failed} FAILURES — see above")
print(f"{'='*40}")
