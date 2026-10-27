"""Views of the dashboard, one module per area:

- pages: the HTML pages and the login
- live: the live view (video stream, source, playback, the table of vehicles passing)
- events: new records pushed to the pages as they are saved (Server-Sent Events)
- webrtc: the live view's video over WebRTC
- records: plate search, reviewing and deleting records, statistics, evidence photos
- calibration: speed calibration, of the current source (live view) and of any source (system page)
- settings: the Settings pop-up (rules, retention, preferences, password)
- system: the system status page

The API views use Django REST Framework; errors are JSON: {"detail": "..."} for a whole request, or
{"<field>": ["..."]} from a serializer.
"""
