# Smart Traffic: Motorcycle Violation Detection

Smart Traffic analyses CCTV streams and video files to detect motorcycles, count riders, check helmet use, read Thai licence plates and measure speed.
A web dashboard shows the live video and lets staff search plates, view evidence photos, review events and see violation statistics.

## Contents

- [Features](#features)
- [Technologies](#technologies)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration (.env)](#configuration-env)
- [Running](#running)
- [Using the Dashboard](#using-the-dashboard)
- [User Roles](#user-roles)
- [How It Works](#how-it-works)
- [Models and Training Results](#models-and-training-results)
- [Data Storage](#data-storage)
- [Security](#security)
- [Languages (English / Thai)](#languages-english--thai)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [References](#references)
- [Licences](#licences)

## Features

| Area | Details |
|---|---|
| Video sources | RTSP cameras, iTIC Foundation CCTV in Chachoengsao (HLS), and video files in `input/` |
| Detection | Tracks each motorcycle, counts riders, checks helmets, and reads the plate series, number and province |
| Speed | Calculated from 4 points on the road whose real distances are known |
| Violations | No helmet (`NO_HELMET`), too many riders (`OVERLOAD`), speeding (`SPEEDING`) |
| Dashboard | Live video, approximate plate search, evidence photos, event review, statistics, repeat offenders, system status and user management |

## Technologies

### Detection

| Technology | Version | Purpose |
|---|---|---|
| Python | 3.11 | Main language |
| PyTorch / torchvision | 2.5.1 / 0.20.1 (CUDA 12.1) | Runs the models; EfficientNet-B0 for the classifiers |
| Ultralytics YOLO | 8.4.92 | Detects motorcycles, plates and heads |
| PaddleOCR / PaddlePaddle GPU | 3.7.0 / 3.3.1 | Reads the text on plates |
| OpenCV | 4.10.0.84 | Video input, image processing and homography |
| NumPy | 2.3.5 | Image and coordinate calculations |
| CUDA Graphs | Through PyTorch | Reduces kernel launch time on the GPU |

### Web

| Technology | Version | Purpose |
|---|---|---|
| Django | 5.2.18 | Web framework and the `/admin/` site |
| Django REST Framework | 3.18.3 | Internal API of the dashboard |
| SQLite | Included with Python | Database |
| Waitress | 3.0.2 | WSGI server |
| WhiteNoise | 6.12.0 | Serves static files |
| django-axes | 8.3.1 | Locks accounts after repeated failed logins |
| aiortc / PyAV | 1.15.0 / 17.1.0 | Live video over WebRTC, with MJPEG at `/stream/` as the fallback |
| htmx | 2.0.4 | Loads tables and vehicle details as HTML |
| Alpine.js (CSP build) | 3.14.9 | Page state |
| uPlot | 1.6.31 | Charts |
| Leaflet | 1.9.4 | Map for choosing a location |
| OpenStreetMap / Nominatim | External services | Map tiles and place names |
| Noto Sans Thai, Tabian Plate | Bundled fonts | Thai text and plate numbers |

The full package list is in [requirements.txt](requirements.txt).

## Requirements

- Windows (the commands in this document and `setup.bat` are written for Windows)
- Python 3.11
- An NVIDIA GPU with a driver that supports CUDA 12.1, because `requirements.txt` installs the CUDA build of torch and `paddlepaddle-gpu`
- An internet connection during installation, and when using the iTIC cameras or the map

The model files the system uses are already in git, so nothing needs to be downloaded or trained.

## Installation

### Option 1: setup.bat (recommended)

Double-click `setup.bat`. It creates `venv\`, installs the packages, sets up the database, asks you to create an admin account if there is none, and starts the dashboard at http://127.0.0.1:8000.
It is safe to run again: the venv and the admin account are only created once.

### Option 2: Manual

Run every command from the project root, and always use the Python in `venv\`.

```bash
py -3.11 -m venv venv
```

```bash
venv\Scripts\python -m pip install -r requirements.txt
```

```bash
venv\Scripts\python web\manage.py migrate
```

```bash
venv\Scripts\python web\manage.py createsuperuser
```

## Configuration (.env)

Create a `.env` file in the project root. It is not in git because it can contain the camera password. Restart the program after changing it.

```ini
RTSP_URL=rtsp://user:pass@192.168.1.10:554/stream
VIDEO_FILE=input/IMG_9122.MOV
```

| Variable | Meaning |
|---|---|
| `RTSP_URL` | IP camera URL, used by both the desktop program and the dashboard |
| `VIDEO_FILE` | Video file for the desktop program |

Detection settings such as confidence thresholds, image sizes and model paths are in [src/app_config.py](src/app_config.py).

## Running

### Web Dashboard (Django)

```bash
venv\Scripts\python web\run.py
```

Open http://127.0.0.1:8000 and log in. `run.py` migrates the database and collects the static files every time it starts. If there is no admin account yet, it stops and tells you how to create one.

Detection only runs while staff are logged in:

- The first staff login loads the models onto the GPU, which takes about 10–30 seconds.
- Live cameras are processed all the time. Video files open paused and are only processed while they play.
- When the last staff member logs out or their session expires, detection stops and the GPU memory is released.
- If the server restarts while staff are still logged in, detection starts again by itself.
- If detection fails to start, or the OCR process stops, the next staff login restarts it.

### Desktop Program (OpenCV Window)

Run it from the project root with `-m`, because `src/` is a Python package.

```bash
venv\Scripts\python -m src.main
```

Choose `[1]` for the video file (`VIDEO_FILE`) or `[2]` for the RTSP camera (`RTSP_URL`), then use these keys in the video window:

| Key | Action |
|---|---|
| `Space` | Pause or resume (video files only) |
| `C` | Set up speed measurement: click 4 points on the road (far left, far right, near right, near left), then type the real width and length in metres |
| `Q` | Quit |

Each motorcycle's result is printed to the console when it leaves the frame. A calibration set with `C` lasts only until the program closes, while one set in the dashboard is saved in the database.

### Other Commands

Clean up old data now (same as the Clean up now button in Settings):

```bash
venv\Scripts\python web\manage.py cleanup
```

## Using the Dashboard

Tables show normal motorcycles in green and violations in red.

| Page | Purpose |
|---|---|
| **Live view** (`/`) | Watch the video with detections, change the source, play, pause or skip, and see the latest motorcycles |
| **Violations** (`/violations/`) | Statistics for today, 7 days or a chosen range (up to 366 days) by type and by day, a violation calendar, repeat offenders and the records table |
| **Search** (`/search/`) | Approximate plate search, details, evidence photos and event review |
| **System** (`/system/`) | Status of detection, cameras, video files, disk use and the speed calibration of every source |
| **Admin** (`/admin/`) | Manage users and roles, and view the audit log |

### Video Sources

- **IP camera**: uses `RTSP_URL`. If it is not set, or no new frame arrives for 5 seconds, the screen shows NO SIGNAL.
- **iTIC cameras**: 11 cameras are available in the dropdown. They are listed in `web/dashboard/itic_cameras.py`. If that file is removed, the option disappears and the system uses the IP camera.
- **Video files**: only files directly inside `input/` can be chosen (`.mp4`, `.mov`, `.avi`, `.mkv`, `.m4v`, `.ts`). At the end, playback returns to the first frame and waits.
- **Frames per second**: video files offer 10, 15, 25, 30 or 60, up to the file's own rate, and the choice is saved per account. Live cameras stream at 30 fps, or the camera's rate if lower.

### Video Settings

The Video settings button in Live view applies to the source being shown.

| Applies to | Settings |
|---|---|
| Every source | Speed measurement (4 road points), detection area (3–12 points), and a speed limit for that source (blank uses the system limit) |
| Video files | Display frame rate, recording date and time, map location, and skipping repeats when a file is played again |

A motorcycle whose ground contact point is outside the detection area is not counted. Opening the location map needs an internet connection.

### Search and Event Review

- The plate series and number are compared separately, and parts OCR could not read are ignored. For example, searching `6มก65` also finds a plate read only as `65`.
- Similar-looking characters (บ/ป, ด/ต, 3/8) count as close matches, and the closest plates come first.
- Search several plates by separating them with commas, such as `1กข987, 2847`, up to 10 plates.
- Click a row to see the motorcycle when it was closest to the camera, and the sharpest plate photo, even if OCR could not read it.
- Press **Confirm event** or **Reject** and optionally add a note. The system records who reviewed it and when, and Undo review cancels it.
- A repeat offender is a plate with more than one violation in the chosen date range. Plates that differ by one similar-looking character count as the same motorcycle. Partly read plates and rejected events are not counted.

### Settings

The Settings button is at the top right of every page.

- **Admin**: sets the speed limit, the number of riders allowed and the retention periods. New rules apply immediately, even while detection is running.
- **Staff**: sets their own Live view preferences, such as the table filter, skip distance and FPS chart.
- **Every account**: can change its password and language. Accounts that can view records can turn on notifications for new violations.

## User Roles

Roles are Django groups, and page access uses 6 custom permissions in `dashboard.models.Access`, checked by path in `dashboard/middleware.py`. Opening a page without permission redirects to the first page the user may open. A user with no permissions cannot log in.

| Permission | Allows |
|---|---|
| `use_dashboard` | Live video, changing the source, video settings, reviewing events, and the Delete all button |
| `view_records` | Plate search, evidence photos and violation statistics |
| `view_system_status` | Camera and system status |
| `manage_calibration` | Editing or removing saved speed calibrations |
| `delete_records` | Deleting selected or filtered records in Violations |
| `manage_settings` | Violation rules and retention periods |

| Role | Permissions |
|---|---|
| **Staff** | `use_dashboard`, `view_records` |
| **Admin** | `view_records`, `view_system_status`, `manage_calibration`, `delete_records`, `manage_settings`, and managing users and groups in `/admin/` |

- `migrate` creates both groups and a demo account with username `staff` and password `staff`. **Change this password before real use.**
- Add users in `/admin/` and choose a group. Admin accounts also need **Staff status** ticked; Staff accounts do not.
- Admins cannot make anyone a superuser and cannot see superuser accounts in the user list (`RoleUserAdmin` in `dashboard/admin.py`).
- A superuser has every permission and can open every page. It is intended for developers only.
- Permissions can be changed, and new roles added, at `/admin/` → Groups without changing code.

## How It Works

```mermaid
flowchart TD
    A[Camera or video file] --> B[YOLO motorcycle detection]
    B --> C[Motorcycle tracking]
    C --> D[Head detection + helmet classifier]
    C --> E[Rider classifier]
    C --> F[YOLO plate detection]
    C --> G[Speed from homography]
    F --> H[PaddleOCR in a separate process]
    H --> I[Vote across frames]
    D --> J[Report when the motorcycle leaves]
    E --> J
    G --> J
    I --> J
    J --> K[Check violation rules]
    K --> L[Console, or database and evidence photos]
```

1. **Detect motorcycles**: YOLO26n finds motorcycles, which are then filtered by the detection area using the bottom-middle point of each box.
2. **Track**: `MotorcycleTracker` matches motorcycles across frames using positions predicted from their velocity.
3. **Count riders**: the rider classifier evaluates each position (driver, child in front, 3 passenger positions) and averages the results over several frames, giving larger, clearer views more weight.
4. **Check helmets**: YOLO finds the heads above each motorcycle, then the helmet classifier decides from a head crop taken from the full-size frame.
5. **Read plates**: YOLO finds the plate inside the motorcycle box. The plate is cut into 3 lines (series, province, number), and PaddleOCR reads them in a separate process so it does not share the GIL with detection. Characters are then voted on across frames, and the province is matched against the list of Thai provinces.
6. **Measure speed**: a homography converts the ground contact point from image coordinates to road coordinates in metres, and speed is distance over time. If the camera moves in a video file, `CameraMotion` uses ORB and RANSAC to keep the 4 points on the road.
7. **Report**: a motorcycle seen in at least 5 frames is reported when it leaves. `find_violations` applies the rules, and the dashboard saves the record and photos through `dashboard/recorder.py`.

## Models and Training Results

| File in `models/` | Purpose | Architecture | Training data |
|---|---|---|---|
| `yolo26n.pt` | Motorcycle detection | YOLO26n (pretrained weights) | COCO |
| `head_det.pt` | Head detection above each motorcycle | YOLO26s | Crops above the motorcycles in Motorcycle Helmet v7 |
| `helmet_cls.pt` | Helmet / no helmet on each head | EfficientNet-B0 | Head boxes from Motorcycle Helmet v7 |
| `rider_cls.pt` | Rider state in 5 positions | EfficientNet-B0 | HELMET dataset |
| `plate_det_th.pt` | Plate detection | YOLO | motorcycle license plate thai v4 |
| `plate_ocr_th/` | Plate text recognition | PP-OCRv5 mobile (`th_PP-OCRv5_mobile_rec`) | 90,000 synthetic plate images |

Results on each model's validation set, as recorded in `runs/` and in the model files:

| Model | Results |
|---|---|
| `head_det.pt` | mAP50 0.657, mAP50-95 0.269 (test split) |
| `plate_det_th.pt` | Precision 0.910, recall 0.647, mAP50 0.713, mAP50-95 0.536 |
| `helmet_cls.pt` | Balanced accuracy 93.8% (recall: helmet 93.1%, no helmet 94.5%) |
| `rider_cls.pt` | Rider count 89.1%, all positions correct 82.5%, finding a rider without a helmet: precision 94.9%, recall 83.5% |
| `plate_ocr_th/` | Accuracy 62.9%, normalized edit distance 0.767 (3,000 synthetic images, before voting across frames) |

The videos in `input/` are used for evaluation only, never for training.

## Data Storage

`dashboard/storage.py` keeps evidence photos and records within set limits. `run.py` cleans up at startup and every 6 hours.

- Normal motorcycles and rejected events lose their photos first. Their records stay, so statistics do not change.
- By default these photos are deleted after 7 days, records and their photos after 90 days, and photos may use up to 2048 MB. Admins can change these limits in Settings.
- When photos exceed the limit, the oldest photos of normal motorcycles go first, then unreviewed violations, until usage is down to 90%.
- **Confirmed** events are never deleted automatically, because they are evidence. They must be deleted from Violations or with Delete all.
- Photo files that no record refers to are also deleted.

The database is at `web/database/db.sqlite3` and the photos are in `web/media/`. Both are local to each machine and not in git.

## Security

- Every page requires a login, and each page only opens for accounts with its permission.
- 5 wrong passwords lock that account from that IP address for 15 minutes (django-axes).
- Sessions last at most 8 hours and end when the browser closes. Cookies are HttpOnly and SameSite=Strict.
- POST requests are protected by CSRF tokens, and a strict CSP allows no inline scripts. The only external sources allowed are OpenStreetMap and Nominatim.
- Evidence photos are served through a login-protected view, with no public URL.
- Camera URLs, which contain passwords, are never sent to the browser or written to the log.
- The server only listens on `127.0.0.1`, so it can only be reached from this machine.

## Languages (English / Thai)

The dashboard starts in English. Each account can switch to Thai at Settings > Display > Language. The login page is always in English, and text from `src/`, such as the labels on the video, is also in English.

Translations are in `web/dashboard/locale/th/LC_MESSAGES/`, and the compiled `.mo` files are in git, so they work immediately. To add or change text, install [GNU gettext](https://www.gnu.org/software/gettext/) and run these commands in `web/`:

```bash
..\venv\Scripts\python manage.py makemessages -l th --ignore=staticfiles
```

```bash
..\venv\Scripts\python manage.py makemessages -l th -d djangojs --ignore=staticfiles
```

Edit the translations in the `.po` files, then compile them:

```bash
..\venv\Scripts\python manage.py compilemessages
```

## Project Structure

```text
Project_01/
├── src/                 Detection system, shared by the desktop program and the dashboard
├── web/
│   ├── admin/           Django settings and root URLs
│   ├── dashboard/       Web app: models, views, templates, static files and translations
│   ├── manage.py        Django management commands
│   └── run.py           Starts the web server
├── models/              Trained models
├── input/               Video files (not in git)
├── requirements.txt     Python packages
└── setup.bat            First-time setup and launch
```

| Files in `src/` | Purpose |
|---|---|
| `main.py`, `desktop_viewer.py` | Desktop program and OpenCV window |
| `detection_app.py`, `yolo_pipeline.py` | Assemble the system and process each frame |
| `app_config.py` | All detection settings |
| `video_stream.py`, `playback.py`, `frame_exchange.py`, `hls.py` | Video input, playback control, and passing frames between threads |
| `detectors.py`, `cuda_graphs.py` | Run the YOLO models and speed them up on the GPU |
| `tracker.py`, `zone.py` | Motorcycle tracking and the detection area |
| `rider_classifier.py`, `helmet_classifier.py`, `crop_classifier.py`, `rider_voting.py`, `rider_counter.py` | Rider counting and helmet checks |
| `ocr.py`, `ocr_process.py`, `ocr_manager.py`, `ocr_handler.py`, `ocr_voting.py`, `province.py` | Plate reading and combining results |
| `speed.py`, `calibration.py`, `camera_motion.py` | Speed measurement |
| `report.py`, `render.py`, `processing_stats.py`, `utils.py` | Reports, drawing, statistics and shared helpers |

| Files in `web/dashboard/` | Purpose |
|---|---|
| `engine.py` | Runs detection inside the web server |
| `recorder.py`, `record_feed.py` | Save motorcycles and send new records to the page |
| `live_frames.py`, `webrtc.py` | Live video |
| `sources.py`, `source_settings.py`, `itic_cameras.py`, `calibrations.py` | Video sources and their settings |
| `plate_search.py` | Approximate plate search |
| `storage.py`, `system_health.py` | Data cleanup and health checks |
| `middleware.py`, `security.py` | Permission checks and security headers |
| `models.py`, `serializers.py`, `views/`, `urls.py` | Database and API |

The pages talk to the server through `/api/...`, written with DRF, and receive new records and status as Server-Sent Events from `/api/events/...`.

## Troubleshooting

| Problem | Solution |
|---|---|
| `No admin account yet` when starting `run.py` | Run `venv\Scripts\python web\manage.py createsuperuser`, then start again |
| `VIDEO_FILE not found in .env` | Set `VIDEO_FILE` in `.env`, or choose the RTSP camera |
| The dashboard shows NO SIGNAL | Check `RTSP_URL` and the camera connection |
| No files to choose from in the dashboard | Put the video directly in `input/`, not in a subfolder |
| No speed in the records | Set up speed measurement for that source first |
| The account is locked | Wait 15 minutes |
| Live video does not start right after login | Wait 10–30 seconds for the models to load, and check the console |

## References

### Datasets

| Dataset | Size | Licence | Used for |
|---|---|---|---|
| [Motorcycle Helmet v7](https://universe.roboflow.com/naratipws/motorcycle-helmet-w75eo/dataset/7) by naratipws (Roboflow Universe) | 3,835 images | MIT | `head_det.pt`, `helmet_cls.pt` |
| [motorcycle license plate thai v4](https://universe.roboflow.com/zusture/motorcycle-license-plate-thai/dataset/4) by zusture (Roboflow Universe) | 652 images | CC BY 4.0 | `plate_det_th.pt` |
| HELMET dataset (Lin et al., 2020), from a [copy on Hugging Face](https://huggingface.co/datasets/AryanRajMaurya/Helmets) | 910 clips, 10,006 motorcycles | See the source | `rider_cls.pt` |
| [COCO](https://cocodataset.org/) | Dataset of the pretrained weights | See the source | `yolo26n.pt` |

The datasets are kept in `datasets/`, which is not in git.

### Papers

1. H. Lin, J. D. Deng, D. Albers, and F. W. Siebert, "Helmet Use Detection of Tracked Motorcycles Using CNN-Based Multi-Task Learning," *IEEE Access*, vol. 8, pp. 162073–162084, 2020. doi: [10.1109/ACCESS.2020.3021357](https://doi.org/10.1109/ACCESS.2020.3021357)
2. M. Tan and Q. V. Le, "EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks," *ICML*, 2019. [arXiv:1905.11946](https://arxiv.org/abs/1905.11946)
3. E. Rublee, V. Rabaud, K. Konolige, and G. Bradski, "ORB: An efficient alternative to SIFT or SURF," *ICCV*, 2011. doi: [10.1109/ICCV.2011.6126544](https://doi.org/10.1109/ICCV.2011.6126544)
4. M. A. Fischler and R. C. Bolles, "Random Sample Consensus: A Paradigm for Model Fitting with Applications to Image Analysis and Automated Cartography," *Communications of the ACM*, vol. 24, no. 6, pp. 381–395, 1981. doi: [10.1145/358669.358692](https://doi.org/10.1145/358669.358692)

### Software and Services

- [Ultralytics YOLO](https://docs.ultralytics.com/)
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) and [PaddlePaddle](https://www.paddlepaddle.org.cn/en)
- [PyTorch](https://pytorch.org/) and [OpenCV](https://opencv.org/)
- [Django](https://www.djangoproject.com/), [Django REST Framework](https://www.django-rest-framework.org/), [django-axes](https://github.com/jazzband/django-axes), [Waitress](https://github.com/Pylons/waitress) and [WhiteNoise](https://whitenoise.readthedocs.io/)
- [aiortc](https://github.com/aiortc/aiortc) and [PyAV](https://github.com/PyAV-Org/PyAV)
- [htmx](https://htmx.org/), [Alpine.js](https://alpinejs.dev/), [uPlot](https://github.com/leeoniya/uPlot) and [Leaflet](https://leafletjs.com/)
- Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), and place search by [Nominatim](https://nominatim.org/)
- CCTV video from the [iTIC Foundation](https://www.iticfoundation.org/)
- [Noto Sans Thai](https://github.com/notofonts/thai) fonts. Tabian Plate is adapted from Noto. All are under the [SIL Open Font License 1.1](web/dashboard/static/dashboard/fonts/OFL.txt).

## Licences

This project does not have a LICENSE file of its own yet. Third-party components are used under their own licences.

| Component | Licence |
|---|---|
| Ultralytics YOLO | AGPL-3.0 (closed-source commercial use needs an Enterprise License) |
| PaddleOCR, PaddlePaddle, OpenCV | Apache-2.0 |
| PyTorch, torchvision, Django, DRF, aiortc, PyAV | BSD |
| django-axes, WhiteNoise | MIT |
| Waitress | ZPL 2.1 |
