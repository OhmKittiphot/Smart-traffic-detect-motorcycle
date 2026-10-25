"""Add-on: public road CCTV of the iTIC Foundation (Chachoengsao) as extra live sources.

The dashboard lists these next to the IP camera (RTSP_URL) under "Live camera". To remove the add-on, delete
this file: the dropdown then offers only the IP camera, and a saved iTIC choice falls back to it on restart.
"""

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
