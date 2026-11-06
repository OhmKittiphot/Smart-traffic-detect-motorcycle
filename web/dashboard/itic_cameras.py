# Add-on: iTIC Foundation (Chachoengsao) road CCTV as extra live sources.
# Delete this file to remove it; a saved iTIC choice then falls back to the IP camera.

# Cameras that served video when checked (ccs04, ccs10, ccs11 and ccs15 did not). Closest motorcycle views:
# ccs01, ccs05 (traffic light queue), ccs13, ccs14
_CAMERA_IDS = ("ccs01", "ccs02", "ccs03", "ccs05", "ccs06", "ccs07", "ccs08", "ccs09", "ccs12", "ccs13", "ccs14")

# key -> (label shown in the dropdown, HLS stream URL)
CAMERAS = {
    f"itic-{cid}": (f"iTIC CCTV · Chachoengsao {cid}", f"https://camerai1.iticfoundation.org/hls/{cid}.m3u8")
    for cid in _CAMERA_IDS
}


def live_sources() -> dict:
    return dict(CAMERAS)
