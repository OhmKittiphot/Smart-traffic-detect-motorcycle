"""Add-on: public road CCTV of the iTIC Foundation (Chachoengsao) as extra live sources.

The dashboard lists these next to the IP camera (RTSP_URL) under "Live camera". To remove the add-on, delete
this file: the dropdown then offers only the IP camera, and a saved iTIC choice falls back to it on restart.
"""

# key -> (label shown in the dropdown, HLS stream URL)
# Other cameras on the same server: ccs01-03, ccs05-09 (same URL pattern)
CAMERAS = {
    "itic-ccs06": ("iTIC CCTV · Chachoengsao ccs06", "https://camerai1.iticfoundation.org/hls/ccs06.m3u8"),
}


def live_sources() -> dict:
    return dict(CAMERAS)
