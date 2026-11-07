# iTIC Foundation (Chachoengsao) road CCTV as extra live sources.

_CAMERA_IDS = ("ccs01", "ccs02", "ccs03", "ccs05", "ccs06", "ccs07", "ccs08", "ccs09", "ccs12", "ccs13", "ccs14")

CAMERAS = {
    f"itic-{cid}": (f"iTIC CCTV, Chachoengsao [{cid}]", f"https://camerai1.iticfoundation.org/hls/{cid}.m3u8")
    for cid in _CAMERA_IDS
}


def live_sources():
    return dict(CAMERAS)
