# Smart Traffic: ระบบตรวจจับรถจักรยานยนต์ที่ทำผิดกฎจราจร

ระบบวิเคราะห์ภาพจากกล้อง CCTV หรือไฟล์วิดีโอ เพื่อตรวจจับรถจักรยานยนต์ นับจำนวนคนขี่ ตรวจการสวมหมวกกันน็อก อ่านป้ายทะเบียนไทย และวัดความเร็ว
พร้อมเว็บสำหรับดูภาพสด ค้นหาทะเบียน ดูภาพหลักฐาน ยืนยันเหตุการณ์ และดูสถิติการฝ่าฝืน

## สารบัญ

- [ความสามารถของระบบ](#ความสามารถของระบบ)
- [เทคโนโลยีที่ใช้](#เทคโนโลยีที่ใช้)
- [สิ่งที่ต้องมี](#สิ่งที่ต้องมี)
- [การติดตั้ง](#การติดตั้ง)
- [การตั้งค่า (.env)](#การตั้งค่า-env)
- [วิธีการรัน](#วิธีการรัน)
- [การใช้งานเว็บ](#การใช้งานเว็บ)
- [สิทธิ์ผู้ใช้](#สิทธิ์ผู้ใช้)
- [หลักการทำงาน](#หลักการทำงาน)
- [โมเดลและผลการเทรน](#โมเดลและผลการเทรน)
- [พื้นที่จัดเก็บข้อมูล](#พื้นที่จัดเก็บข้อมูล)
- [ความปลอดภัย](#ความปลอดภัย)
- [ภาษา (ไทย / English)](#ภาษา-ไทย--english)
- [โครงสร้างโปรเจกต์](#โครงสร้างโปรเจกต์)
- [แก้ปัญหาเบื้องต้น](#แก้ปัญหาเบื้องต้น)
- [อ้างอิง](#อ้างอิง)
- [สัญญาอนุญาต](#สัญญาอนุญาต)

## ความสามารถของระบบ

| ด้าน | รายละเอียด |
|---|---|
| แหล่งวิดีโอ | กล้อง RTSP, กล้อง CCTV ของมูลนิธิ iTIC จังหวัดฉะเชิงเทรา (HLS) และไฟล์วิดีโอในโฟลเดอร์ `input/` |
| การตรวจจับ | ติดตามรถแต่ละคัน นับคนขี่ ตรวจหมวกกันน็อก อ่านหมวดอักษร เลขทะเบียน และจังหวัด |
| ความเร็ว | คำนวณจาก 4 จุดบนพื้นถนนที่รู้ระยะจริง |
| การฝ่าฝืน | ไม่สวมหมวกกันน็อก (`NO_HELMET`), ซ้อนเกินกำหนด (`OVERLOAD`), ขับเร็วเกินกำหนด (`SPEEDING`) |
| เว็บ | ภาพสด ค้นหาทะเบียนแบบใกล้เคียง ภาพหลักฐาน การยืนยันเหตุการณ์ สถิติ ผู้กระทำผิดซ้ำ สถานะระบบ และจัดการผู้ใช้ |

## เทคโนโลยีที่ใช้

### ระบบตรวจจับ

| เทคโนโลยี | รุ่น | ใช้ทำอะไร |
|---|---|---|
| Python | 3.11 | ภาษาหลัก |
| PyTorch / torchvision | 2.5.1 / 0.20.1 (CUDA 12.1) | รันโมเดล และ EfficientNet-B0 ของ classifier |
| Ultralytics YOLO | 8.4.92 | ตรวจจับรถ ป้ายทะเบียน และศีรษะ |
| PaddleOCR / PaddlePaddle GPU | 3.7.0 / 3.3.1 | อ่านตัวอักษรบนป้ายทะเบียน |
| OpenCV | 4.10.0.84 | อ่านวิดีโอ ประมวลผลภาพ และคำนวณ homography |
| NumPy | 2.3.5 | คำนวณข้อมูลภาพและพิกัด |
| CUDA Graphs | ผ่าน PyTorch | ลดเวลาเรียก kernel ของโมเดลบน GPU |

### เว็บ

| เทคโนโลยี | รุ่น | ใช้ทำอะไร |
|---|---|---|
| Django | 5.2.18 | เว็บเฟรมเวิร์กและหน้า `/admin/` |
| Django REST Framework | 3.18.3 | API ภายในของหน้าเว็บ |
| SQLite | มากับ Python | ฐานข้อมูล |
| Waitress | 3.0.2 | WSGI server |
| WhiteNoise | 6.12.0 | ส่งไฟล์ static |
| django-axes | 8.3.1 | ล็อกบัญชีเมื่อใส่รหัสผ่านผิดหลายครั้ง |
| aiortc / PyAV | 1.15.0 / 17.1.0 | ส่งภาพสดผ่าน WebRTC (ใช้ MJPEG ที่ `/stream/` เป็นทางสำรอง) |
| htmx | 2.0.4 | โหลดตารางและรายละเอียดรถเป็น HTML |
| Alpine.js (CSP build) | 3.14.9 | จัดการสถานะของหน้าเว็บ |
| uPlot | 1.6.31 | กราฟ |
| Leaflet | 1.9.4 | แผนที่สำหรับปักหมุดสถานที่ |
| OpenStreetMap / Nominatim | บริการภายนอก | ภาพแผนที่และชื่อสถานที่ |
| Noto Sans Thai, Tabian Plate | ฟอนต์ในโปรเจกต์ | ข้อความภาษาไทยและเลขทะเบียน |

รายการ package ทั้งหมดอยู่ใน [requirements.txt](requirements.txt)

## สิ่งที่ต้องมี

- Windows (คำสั่งในเอกสารนี้และ `setup.bat` เขียนสำหรับ Windows)
- Python 3.11
- การ์ดจอ NVIDIA พร้อมไดรเวอร์ที่รองรับ CUDA 12.1 เพราะ `requirements.txt` ใช้ torch รุ่น CUDA และ `paddlepaddle-gpu`
- อินเทอร์เน็ตตอนติดตั้ง และตอนใช้กล้อง iTIC หรือแผนที่

ไฟล์โมเดลที่ระบบใช้อยู่ใน git แล้ว จึงไม่ต้องดาวน์โหลดหรือเทรนเพิ่ม

## การติดตั้ง

### วิธีที่ 1: ใช้ setup.bat (แนะนำ)

ดับเบิลคลิก `setup.bat` สคริปต์จะสร้าง `venv\` ติดตั้ง package ตั้งฐานข้อมูล ให้สร้างบัญชี admin ถ้ายังไม่มี แล้วเปิดเว็บที่ http://127.0.0.1:8000
รันซ้ำได้ โดยจะไม่สร้าง venv หรือบัญชี admin ซ้ำ

### วิธีที่ 2: ติดตั้งเอง

รันทุกคำสั่งจากโฟลเดอร์รากของโปรเจกต์ และใช้ Python จาก `venv\` เสมอ

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

## การตั้งค่า (.env)

สร้างไฟล์ `.env` ที่โฟลเดอร์รากของโปรเจกต์ ไฟล์นี้ไม่อยู่ใน git เพราะอาจมีรหัสผ่านของกล้อง แก้แล้วต้องเปิดโปรแกรมใหม่

```ini
RTSP_URL=rtsp://user:pass@192.168.1.10:554/stream
VIDEO_FILE=input/IMG_9122.MOV
```

| ตัวแปร | ความหมาย |
|---|---|
| `RTSP_URL` | URL ของกล้อง IP ใช้ทั้งโปรแกรมบนเครื่องและเว็บ |
| `VIDEO_FILE` | ไฟล์วิดีโอของโปรแกรมบนเครื่อง |

ค่าของการตรวจจับ เช่น confidence threshold, ขนาดภาพ และ path ของโมเดล อยู่ใน [src/app_config.py](src/app_config.py)

## วิธีการรัน

### เว็บ (Django)

```bash
venv\Scripts\python web\run.py
```

เปิด http://127.0.0.1:8000 แล้วเข้าสู่ระบบ `run.py` จะ migrate ฐานข้อมูลและรวมไฟล์ static ให้เองทุกครั้งที่เปิด และจะหยุดพร้อมบอกวิธีแก้ถ้ายังไม่มีบัญชี admin

ระบบตรวจจับทำงานเฉพาะตอนที่มี staff เข้าสู่ระบบอยู่

- staff คนแรกที่ login ทำให้ระบบโหลดโมเดลขึ้น GPU ใช้เวลาประมาณ 10–30 วินาที
- กล้องสดถูกตรวจจับตลอดเวลา ส่วนไฟล์วิดีโอจะเปิดมาในสถานะหยุด และถูกตรวจจับเฉพาะตอนกดเล่น
- เมื่อ staff คนสุดท้าย logout หรือ session หมดอายุ ระบบจะหยุดตรวจจับและคืนหน่วยความจำ GPU
- ถ้าเปิดเซิร์ฟเวอร์ใหม่ขณะที่ยังมี staff login ค้างอยู่ ระบบจะเริ่มตรวจจับให้เอง
- ถ้าระบบเริ่มไม่สำเร็จ หรือ process OCR หยุดทำงาน staff คนถัดไปที่ login จะทำให้ระบบเริ่มใหม่

### โปรแกรมบนเครื่อง (หน้าต่าง OpenCV)

ต้องรันจากโฟลเดอร์รากของโปรเจกต์ด้วย `-m` เพราะ `src/` เป็น Python package

```bash
venv\Scripts\python -m src.main
```

เลือก `[1]` ไฟล์วิดีโอ (`VIDEO_FILE`) หรือ `[2]` กล้อง RTSP (`RTSP_URL`) แล้วใช้ปุ่มในหน้าต่างวิดีโอ

| ปุ่ม | การทำงาน |
|---|---|
| `Space` | หยุด/เล่นต่อ (เฉพาะไฟล์วิดีโอ) |
| `C` | ตั้งค่าวัดความเร็ว: คลิก 4 จุดบนพื้นถนน (ซ้ายไกล, ขวาไกล, ขวาใกล้, ซ้ายใกล้) แล้วพิมพ์ความกว้างและยาวจริงเป็นเมตร |
| `Q` | ออกจากโปรแกรม |

ผลของรถแต่ละคันจะพิมพ์ใน console เมื่อรถออกจากภาพ ค่าวัดความเร็วที่ตั้งด้วยปุ่ม `C` ใช้ได้เฉพาะรอบที่เปิดโปรแกรมอยู่ ส่วนค่าที่ตั้งผ่านเว็บจะบันทึกในฐานข้อมูล

### คำสั่งอื่น

ล้างข้อมูลเก่าทันที (เหมือนปุ่ม Clean up now ในหน้า Settings):

```bash
venv\Scripts\python web\manage.py cleanup
```

เทรน head classifier ใหม่ (ต้องมีโฟลเดอร์ `helmet-rider-dataset/` ดูแหล่งข้อมูลใน[อ้างอิง](#ชุดข้อมูล)):

```bash
venv\Scripts\python -m src.train_head_classifier --data helmet-rider-dataset --out models/head_cls.pt
```

## การใช้งานเว็บ

ตารางในหน้าเว็บใช้แถวสีเขียวสำหรับรถปกติ และแถวสีแดงสำหรับรถที่ทำผิดกฎ

| หน้า | การใช้งาน |
|---|---|
| **Live view** (`/`) | ดูภาพสดพร้อมผลตรวจจับ เปลี่ยนแหล่งวิดีโอ เล่น/หยุด/ข้ามวิดีโอ และดูรายการรถล่าสุด |
| **Violations** (`/violations/`) | สถิติวันนี้ / 7 วัน / ช่วงวันที่ที่เลือก (สูงสุด 366 วัน) แยกตามประเภทและรายวัน ปฏิทินการฝ่าฝืน ผู้กระทำผิดซ้ำ และตารางรายการ |
| **Search** (`/search/`) | ค้นหาทะเบียนแบบใกล้เคียง ดูรายละเอียดและภาพหลักฐาน และยืนยันเหตุการณ์ |
| **System** (`/system/`) | สถานะระบบตรวจจับ กล้อง ไฟล์วิดีโอ พื้นที่ดิสก์ และค่าวัดความเร็วของทุกแหล่ง |
| **Admin** (`/admin/`) | จัดการผู้ใช้ กำหนด role และดูบันทึกการใช้งาน |

### แหล่งวิดีโอ

- **กล้อง IP**: ใช้ `RTSP_URL` ถ้าไม่ได้ตั้งค่า หรือไม่มีภาพใหม่เกิน 5 วินาที หน้าจอจะแสดง NO SIGNAL
- **กล้อง iTIC**: เลือกจาก dropdown ได้ 11 ตัว รายการอยู่ใน `web/dashboard/itic_cameras.py` ถ้าลบไฟล์นี้ตัวเลือกจะหายไปและระบบจะใช้กล้อง IP
- **ไฟล์วิดีโอ**: เลือกได้เฉพาะไฟล์ที่อยู่ใน `input/` (`.mp4`, `.mov`, `.avi`, `.mkv`, `.m4v`, `.ts`) เมื่อเล่นจบจะกลับไปเฟรมแรกแล้วหยุดรอ
- **จำนวนภาพต่อวินาที**: ไฟล์วิดีโอเลือกได้ 10, 15, 25, 30 หรือ 60 ไม่เกิน fps ของไฟล์ ค่าที่เลือกเก็บไว้ที่บัญชี กล้องสดส่งที่ 30 ภาพต่อวินาที ไม่เกิน fps ของกล้อง

### Video settings

ปุ่ม Video settings ในหน้า Live view ตั้งค่าแยกตามแหล่งวิดีโอที่กำลังแสดง

| ตั้งได้กับ | การตั้งค่า |
|---|---|
| ทุกแหล่ง | วัดความเร็ว (4 จุดบนถนน), พื้นที่ตรวจจับ (วาด 3–12 จุด), จำกัดความเร็วของแหล่งนั้น (เว้นว่างเพื่อใช้ค่าของระบบ) |
| ไฟล์วิดีโอ | ความเร็วเฟรมที่แสดง, วันและเวลาที่ถ่าย, Location บนแผนที่, กันนับรถซ้ำเมื่อเล่นไฟล์เดิมอีกรอบ |

รถที่จุดล้อแตะถนนอยู่นอกพื้นที่ตรวจจับจะไม่ถูกนับ ส่วนการเปิดแผนที่ของ Location ต้องต่ออินเทอร์เน็ต

### การค้นหาและยืนยันเหตุการณ์

- ระบบแยกเทียบหมวดอักษรกับเลขทะเบียนทีละส่วน ส่วนที่ OCR อ่านไม่ได้จะไม่นำมาคิด เช่น ค้น `6มก65` จะเจอป้ายที่อ่านได้แค่ `65`
- ตัวอักษรที่หน้าตาคล้ายกัน (บ/ป, ด/ต, 3/8) นับว่าใกล้เคียง ผลเรียงจากป้ายที่ใกล้ที่สุดก่อน
- ค้นหลายป้ายพร้อมกันได้โดยคั่นด้วยจุลภาค เช่น `1กข987, 2847` สูงสุด 10 ป้าย
- กดแถวเพื่อดูภาพรถตอนอยู่ใกล้กล้องที่สุด และภาพป้ายที่คมที่สุด แม้ OCR จะอ่านไม่ออก
- กด **Confirm event** หรือ **Reject** พร้อมใส่หมายเหตุได้ ระบบบันทึกว่าใครยืนยันเมื่อไร และกด Undo review เพื่อยกเลิกได้
- ผู้กระทำผิดซ้ำคือทะเบียนที่ฝ่าฝืนมากกว่า 1 ครั้งในช่วงวันที่ที่เลือก ป้ายที่ต่างกันเพียงตัวอักษรคล้ายกัน 1 ตัวนับเป็นคันเดียวกัน ป้ายที่อ่านได้ไม่ครบและเหตุการณ์ที่ Reject แล้วจะไม่นับ

### Settings

ปุ่ม Settings อยู่มุมขวาบนของทุกหน้า

- **Admin**: ตั้งความเร็วสูงสุด จำนวนคนซ้อน และระยะเวลาเก็บข้อมูล กติกาใหม่มีผลทันทีแม้ระบบกำลังตรวจจับ
- **Staff**: ตั้งค่าหน้า Live view ของบัญชีตัวเอง เช่น ตัวกรองตาราง ระยะข้ามวิดีโอ และกราฟ FPS
- **ทุกบัญชี**: เปลี่ยนรหัสผ่านและภาษา บัญชีที่ดูรายการได้เปิดแจ้งเตือนการฝ่าฝืนใหม่ได้

## สิทธิ์ผู้ใช้

Role คือ Group ของ Django ส่วนสิทธิ์ของหน้าเว็บคือ custom permission 6 ตัวใน `dashboard.models.Access` ซึ่ง `dashboard/middleware.py` ตรวจตาม path ถ้าเปิดหน้าที่ไม่มีสิทธิ์ ระบบจะพาไปหน้าแรกที่มีสิทธิ์ ถ้าไม่มีสิทธิ์เลยจะเข้าระบบไม่ได้

| Permission | ใช้ทำอะไร |
|---|---|
| `use_dashboard` | ดูภาพสด เปลี่ยนแหล่งวิดีโอ ตั้งค่าวิดีโอ ยืนยันเหตุการณ์ และลบรายการทั้งหมดด้วยปุ่ม Delete all |
| `view_records` | ค้นหาทะเบียน ดูภาพหลักฐาน และสถิติการฝ่าฝืน |
| `view_system_status` | ดูสถานะกล้องและระบบ |
| `manage_calibration` | แก้หรือลบค่าวัดความเร็วที่บันทึกไว้ |
| `delete_records` | ลบรายการที่เลือกหรือตามตัวกรองในหน้า Violations |
| `manage_settings` | ตั้งกติกาการฝ่าฝืนและระยะเวลาเก็บข้อมูล |

| Role | Permission |
|---|---|
| **Staff** | `use_dashboard`, `view_records` |
| **Admin** | `view_records`, `view_system_status`, `manage_calibration`, `delete_records`, `manage_settings` และจัดการ User/Group ใน `/admin/` |

- `migrate` สร้าง Group ทั้งสองและบัญชี demo username `staff` password `staff` **ควรเปลี่ยนรหัสผ่านก่อนใช้งานจริง**
- เพิ่มผู้ใช้ใน `/admin/` แล้วเลือก Group บัญชี Admin ต้องติ๊ก **Staff status** ด้วย ส่วน Staff ไม่ต้องติ๊ก
- Admin ตั้งใครเป็น superuser ไม่ได้ และมองไม่เห็นบัญชี superuser ในรายชื่อผู้ใช้ (`RoleUserAdmin` ใน `dashboard/admin.py`)
- superuser มีทุก permission จึงใช้ได้ทุกหน้า เหมาะสำหรับนักพัฒนาเท่านั้น
- แก้ permission หรือสร้าง role ใหม่ได้ที่ `/admin/` → Groups โดยไม่ต้องแก้โค้ด

## หลักการทำงาน

```mermaid
flowchart TD
    A[กล้องหรือไฟล์วิดีโอ] --> B[YOLO ตรวจจับรถจักรยานยนต์]
    B --> C[ติดตามรถแต่ละคัน]
    C --> D[ตรวจจับศีรษะ + head classifier]
    C --> E[rider classifier นับคนขี่]
    C --> F[YOLO ตรวจจับป้ายทะเบียน]
    C --> G[วัดความเร็วด้วย homography]
    F --> H[PaddleOCR ใน process แยก]
    H --> I[โหวตผลอ่านหลายเฟรม]
    D --> J[รายงานเมื่อรถออกจากภาพ]
    E --> J
    G --> J
    I --> J
    J --> K[ตรวจกติกาการฝ่าฝืน]
    K --> L[console หรือฐานข้อมูลและภาพหลักฐาน]
```

1. **ตรวจจับรถ**: YOLO26n หารถจักรยานยนต์ แล้วกรองด้วยพื้นที่ตรวจจับ โดยใช้จุดกึ่งกลางด้านล่างของกรอบรถ
2. **ติดตามรถ**: `MotorcycleTracker` จับคู่รถข้ามเฟรมจากตำแหน่งที่ทำนายด้วยความเร็ว
3. **นับคนขี่**: rider classifier ประเมินทีละตำแหน่ง (คนขับ, เด็กด้านหน้า, ผู้ซ้อน 3 ตำแหน่ง) แล้วเฉลี่ยผลหลายเฟรม โดยให้น้ำหนักภาพรถที่ใหญ่และชัดกว่ามากกว่า
4. **ตรวจหมวก**: YOLO หาศีรษะเหนือรถแต่ละคัน แล้ว head classifier ตัดสินจากภาพศีรษะที่ crop จากเฟรมขนาดเต็ม
5. **อ่านป้าย**: YOLO หาป้ายในกรอบรถ ระบบตัดป้ายเป็น 3 บรรทัด (หมวดอักษร จังหวัด เลขทะเบียน) และให้ PaddleOCR อ่านใน process แยก เพื่อไม่แย่ง GIL กับการตรวจจับ จากนั้นโหวตทีละตัวอักษรจากหลายเฟรม และเทียบชื่อจังหวัดกับรายชื่อจังหวัดของไทย
6. **วัดความเร็ว**: homography แปลงจุดล้อแตะถนนจากภาพเป็นพิกัดบนพื้นถนนหน่วยเมตร แล้วคำนวณระยะต่อเวลา ถ้ากล้องในไฟล์วิดีโอขยับ `CameraMotion` จะใช้ ORB และ RANSAC ปรับตำแหน่ง 4 จุดตามภาพ
7. **รายงาน**: รถที่ปรากฏอย่างน้อย 5 เฟรมจะถูกรายงานเมื่อออกจากภาพ `find_violations` ตรวจกติกาการฝ่าฝืน ส่วนเว็บบันทึกรายการและภาพลงฐานข้อมูลด้วย `dashboard/recorder.py`

## โมเดลและผลการเทรน

| ไฟล์ใน `models/` | หน้าที่ | สถาปัตยกรรม | ข้อมูลที่ใช้เทรน |
|---|---|---|---|
| `yolo26n.pt` | ตรวจจับรถจักรยานยนต์ | YOLO26n (น้ำหนักสำเร็จรูป) | COCO |
| `helmet_det.pt` | ตรวจจับศีรษะ | YOLO11s | Motorcycle Helmet v7 |
| `head_cls.pt` | จำแนกหมวก / ไม่มีหมวก | EfficientNet-B0 | กรอบศีรษะจาก Motorcycle Helmet v7 |
| `rider_cls.pt` | สถานะคนขี่ 5 ตำแหน่ง | EfficientNet-B0 | HELMET dataset |
| `plate_det_th.pt` | ตรวจจับป้ายทะเบียน | YOLO | motorcycle license plate thai v4 |
| `plate_ocr_th/` | อ่านตัวอักษรบนป้าย | PP-OCRv5 mobile (`th_PP-OCRv5_mobile_rec`) | ภาพป้ายสังเคราะห์ 90,000 ภาพ |

ไฟล์ `plate_yolo26n.pt`, `yolo26s.pt`, `yolov8n.pt` และ `runs/detect/moto-plate/weights/best.pt` ไม่ได้ถูกเรียกใช้ในค่าตั้งปัจจุบัน

ผลจาก validation set ของแต่ละโมเดล ซึ่งบันทึกไว้ใน `runs/` และในไฟล์โมเดล

| โมเดล | ผลลัพธ์ |
|---|---|
| `helmet_det.pt` | mAP50 0.602, mAP50-95 0.249 |
| `plate_det_th.pt` | Precision 0.910, Recall 0.647, mAP50 0.713, mAP50-95 0.536 |
| `head_cls.pt` | Balanced accuracy 93.8% (recall: มีหมวก 93.1%, ไม่มีหมวก 94.5%) |
| `rider_cls.pt` | นับคนขี่ถูก 89.1%, ถูกทุกตำแหน่ง 82.5%, ตรวจพบคนไม่สวมหมวก precision 94.9% recall 83.5% |
| `plate_ocr_th/` | Accuracy 62.9%, normalized edit distance 0.767 (ภาพสังเคราะห์ 3,000 ภาพ ก่อนโหวตหลายเฟรม) |

วิดีโอใน `input/` ใช้สำหรับประเมินผลเท่านั้น ไม่ได้นำมาเทรนโมเดล

สคริปต์ที่ยังอยู่ในโปรเจกต์คือ `src/train_head_classifier.py` ส่วนสคริปต์เตรียม HELMET dataset และเทรน rider classifier ถูกลบออกแล้ว ดูได้จากประวัติ git ที่ commit `d784601`

## พื้นที่จัดเก็บข้อมูล

`dashboard/storage.py` คุมภาพหลักฐานและรายการรถไม่ให้เกินพื้นที่ที่กำหนด `run.py` ล้างข้อมูลตอนเริ่มและทุก 6 ชั่วโมง

- รถปกติและเหตุการณ์ที่ Reject แล้วจะถูกลบภาพก่อน แต่รายการยังอยู่ จึงไม่กระทบสถิติ
- ค่าเริ่มต้นคือลบภาพเหล่านี้หลัง 7 วัน ลบรายการพร้อมภาพหลัง 90 วัน และเก็บภาพรวมกันได้ไม่เกิน 2048 MB Admin ปรับได้ในหน้า Settings
- เมื่อภาพเกินพื้นที่สูงสุด ระบบจะลบภาพเก่าสุดของรถปกติก่อน แล้วค่อยเป็นการฝ่าฝืนที่ยังไม่ตรวจ จนเหลือ 90%
- เหตุการณ์ที่ **Confirm แล้ว** ไม่ถูกลบอัตโนมัติ เพราะเป็นหลักฐาน ต้องลบเองจากหน้า Violations หรือปุ่ม Delete all
- ไฟล์ภาพที่ไม่มีรายการอ้างถึงจะถูกลบด้วย

ฐานข้อมูลอยู่ที่ `web/database/db.sqlite3` และภาพอยู่ที่ `web/media/` ทั้งคู่แยกตามเครื่องและไม่อยู่ใน git

## ความปลอดภัย

- ทุกหน้าต้องเข้าสู่ระบบ และเปิดได้เฉพาะบัญชีที่มี permission ของหน้านั้น
- ใส่รหัสผ่านผิด 5 ครั้ง บัญชีนั้นจาก IP นั้นจะถูกล็อก 15 นาที (django-axes)
- session มีอายุสูงสุด 8 ชั่วโมงและหมดอายุเมื่อปิดเบราว์เซอร์ คุกกี้เป็น HttpOnly และ SameSite=Strict
- มี CSRF ป้องกันคำสั่ง POST และ CSP แบบเข้มงวด ไม่มี inline script และโหลดจากภายนอกได้เฉพาะแผนที่ OpenStreetMap กับ Nominatim
- ภาพหลักฐานส่งผ่าน view ที่ต้องล็อกอิน ไม่มี URL สาธารณะ
- ไม่ส่ง URL ของกล้องซึ่งมีรหัสผ่านไปที่เบราว์เซอร์ และไม่พิมพ์ลง log
- server ฟังเฉพาะ `127.0.0.1` จึงเข้าได้จากเครื่องนี้เท่านั้น

## ภาษา (ไทย / English)

หน้าเว็บเริ่มต้นเป็นภาษาอังกฤษ แต่ละบัญชีเปลี่ยนเป็นภาษาไทยได้ที่ Settings > Display > Language หน้า login เป็นภาษาอังกฤษเสมอ ส่วนข้อความจาก `src/` เช่น ข้อความบนภาพวิดีโอ ยังเป็นภาษาอังกฤษ

คำแปลอยู่ที่ `web/dashboard/locale/th/LC_MESSAGES/` และไฟล์ `.mo` ที่ compile แล้วอยู่ใน git จึงใช้งานได้ทันที ถ้าเพิ่มหรือแก้ข้อความ ต้องติดตั้ง [GNU gettext](https://www.gnu.org/software/gettext/) แล้วรันคำสั่งในโฟลเดอร์ `web/`

```bash
..\venv\Scripts\python manage.py makemessages -l th --ignore=staticfiles
```

```bash
..\venv\Scripts\python manage.py makemessages -l th -d djangojs --ignore=staticfiles
```

แก้คำแปลในไฟล์ `.po` แล้ว compile

```bash
..\venv\Scripts\python manage.py compilemessages
```

## โครงสร้างโปรเจกต์

```text
Project_01/
├── src/                 ระบบตรวจจับ ใช้ร่วมกันทั้งโปรแกรมบนเครื่องและเว็บ
├── web/
│   ├── admin/           ค่าตั้ง Django และ URL หลัก
│   ├── dashboard/       แอปเว็บ: model, view, template, static และคำแปล
│   ├── manage.py        คำสั่งจัดการ Django
│   └── run.py           ตัวเปิดเซิร์ฟเวอร์เว็บ
├── models/              โมเดลที่เทรนแล้ว
├── input/               ไฟล์วิดีโอ (ไม่อยู่ใน git)
├── requirements.txt     package ของ Python
└── setup.bat            ติดตั้งและเปิดเว็บครั้งแรก
```

| ไฟล์ใน `src/` | หน้าที่ |
|---|---|
| `main.py`, `desktop_viewer.py` | โปรแกรมบนเครื่องและหน้าต่าง OpenCV |
| `detection_app.py`, `yolo_pipeline.py` | ประกอบระบบตรวจจับและขั้นตอนของแต่ละเฟรม |
| `app_config.py` | ค่าตั้งทั้งหมดของการตรวจจับ |
| `video_stream.py`, `playback.py`, `frame_exchange.py`, `hls.py` | รับภาพ ควบคุมการเล่น และส่งเฟรมระหว่าง thread |
| `detectors.py`, `cuda_graphs.py` | เรียกโมเดล YOLO และเร่งความเร็วบน GPU |
| `tracker.py`, `zone.py` | ติดตามรถและพื้นที่ตรวจจับ |
| `rider_classifier.py`, `rider_voting.py`, `rider_counter.py`, `head_classifier.py` | นับคนขี่และตรวจหมวก |
| `ocr.py`, `ocr_process.py`, `ocr_manager.py`, `ocr_handler.py`, `ocr_voting.py`, `province.py` | อ่านและรวมผลป้ายทะเบียน |
| `speed.py`, `calibration.py`, `camera_motion.py` | วัดความเร็ว |
| `report.py`, `render.py`, `processing_stats.py`, `utils.py` | รายงานผล วาดภาพ สถิติ และฟังก์ชันใช้ร่วมกัน |
| `train_head_classifier.py` | เทรน head classifier |

| ไฟล์ใน `web/dashboard/` | หน้าที่ |
|---|---|
| `engine.py` | รันระบบตรวจจับภายในเว็บเซิร์ฟเวอร์ |
| `recorder.py`, `record_feed.py` | บันทึกรถและส่งรายการใหม่ไปหน้าเว็บ |
| `live_frames.py`, `webrtc.py` | ส่งภาพสด |
| `sources.py`, `source_settings.py`, `itic_cameras.py`, `calibrations.py` | แหล่งวิดีโอและค่าตั้งของแต่ละแหล่ง |
| `plate_search.py` | ค้นหาทะเบียนแบบใกล้เคียง |
| `storage.py`, `system_health.py` | ล้างข้อมูลและตรวจสุขภาพระบบ |
| `middleware.py`, `security.py` | ตรวจสิทธิ์และ security header |
| `models.py`, `serializers.py`, `views/`, `urls.py` | ฐานข้อมูลและ API |

หน้าเว็บคุยกับ server ผ่าน `/api/...` ที่เขียนด้วย DRF และรับรายการใหม่กับสถานะแบบ Server-Sent Events ผ่าน `/api/events/...`

## แก้ปัญหาเบื้องต้น

| อาการ | วิธีแก้ |
|---|---|
| `No admin account yet` ตอนเปิด `run.py` | รัน `venv\Scripts\python web\manage.py createsuperuser` แล้วเปิดใหม่ |
| `VIDEO_FILE not found in .env` | ตั้ง `VIDEO_FILE` ใน `.env` หรือเลือกกล้อง RTSP |
| หน้าเว็บแสดง NO SIGNAL | ตรวจ `RTSP_URL` และการเชื่อมต่อกล้อง |
| ไม่มีไฟล์ให้เลือกในเว็บ | วางไฟล์วิดีโอไว้ใน `input/` โดยตรง ไม่ใส่ในโฟลเดอร์ย่อย |
| ไม่มีความเร็วในรายการ | ตั้งค่าวัดความเร็วของแหล่งวิดีโอนั้นก่อน |
| บัญชีถูกล็อก | รอ 15 นาที |
| ภาพสดไม่เริ่มทันทีหลัง login | รอโหลดโมเดล 10–30 วินาที แล้วดูข้อความใน console |

## อ้างอิง

### ชุดข้อมูล

| ชุดข้อมูล | ขนาด | สัญญาอนุญาต | ใช้กับ |
|---|---|---|---|
| [Motorcycle Helmet v7](https://universe.roboflow.com/naratipws/motorcycle-helmet-w75eo/dataset/7) โดย naratipws (Roboflow Universe) | 3,835 ภาพ | MIT | `helmet_det.pt`, `head_cls.pt` |
| [motorcycle license plate thai v4](https://universe.roboflow.com/zusture/motorcycle-license-plate-thai/dataset/4) โดย zusture (Roboflow Universe) | 652 ภาพ | CC BY 4.0 | `plate_det_th.pt` |
| HELMET dataset (Lin et al., 2020) จาก [สำเนาบน Hugging Face](https://huggingface.co/datasets/AryanRajMaurya/Helmets) | 910 คลิป, 10,006 คัน | ดูจากต้นทาง | `rider_cls.pt` |
| [COCO](https://cocodataset.org/) | ชุดข้อมูลของน้ำหนักสำเร็จรูป | ดูจากต้นทาง | `yolo26n.pt` |

โฟลเดอร์ `helmet-rider-dataset/`, `motorcycle-license-plate-dataset/` และ `datasets/` ไม่อยู่ใน git

### งานวิจัย

1. H. Lin, J. D. Deng, D. Albers, and F. W. Siebert, "Helmet Use Detection of Tracked Motorcycles Using CNN-Based Multi-Task Learning," *IEEE Access*, vol. 8, pp. 162073–162084, 2020. doi: [10.1109/ACCESS.2020.3021357](https://doi.org/10.1109/ACCESS.2020.3021357)
2. M. Tan and Q. V. Le, "EfficientNet: Rethinking Model Scaling for Convolutional Neural Networks," *ICML*, 2019. [arXiv:1905.11946](https://arxiv.org/abs/1905.11946)
3. E. Rublee, V. Rabaud, K. Konolige, and G. Bradski, "ORB: An efficient alternative to SIFT or SURF," *ICCV*, 2011. doi: [10.1109/ICCV.2011.6126544](https://doi.org/10.1109/ICCV.2011.6126544)
4. M. A. Fischler and R. C. Bolles, "Random Sample Consensus: A Paradigm for Model Fitting with Applications to Image Analysis and Automated Cartography," *Communications of the ACM*, vol. 24, no. 6, pp. 381–395, 1981. doi: [10.1145/358669.358692](https://doi.org/10.1145/358669.358692)

### ซอฟต์แวร์และบริการ

- [Ultralytics YOLO](https://docs.ultralytics.com/)
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) และ [PaddlePaddle](https://www.paddlepaddle.org.cn/en)
- [PyTorch](https://pytorch.org/) และ [OpenCV](https://opencv.org/)
- [Django](https://www.djangoproject.com/), [Django REST Framework](https://www.django-rest-framework.org/), [django-axes](https://github.com/jazzband/django-axes), [Waitress](https://github.com/Pylons/waitress) และ [WhiteNoise](https://whitenoise.readthedocs.io/)
- [aiortc](https://github.com/aiortc/aiortc) และ [PyAV](https://github.com/PyAV-Org/PyAV)
- [htmx](https://htmx.org/), [Alpine.js](https://alpinejs.dev/), [uPlot](https://github.com/leeoniya/uPlot) และ [Leaflet](https://leafletjs.com/)
- ข้อมูลแผนที่ © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) และการค้นหาสถานที่จาก [Nominatim](https://nominatim.org/)
- ภาพกล้อง CCTV จาก[มูลนิธิ iTIC](https://www.iticfoundation.org/)
- ฟอนต์ [Noto Sans Thai](https://github.com/notofonts/thai) ส่วน Tabian Plate ดัดแปลงจาก Noto ทั้งหมดใช้สัญญาอนุญาต [SIL Open Font License 1.1](web/dashboard/static/dashboard/fonts/OFL.txt)

## สัญญาอนุญาต

โปรเจกต์นี้ยังไม่มีไฟล์ LICENSE ของตัวเอง ส่วนประกอบจากภายนอกใช้ตามสัญญาอนุญาตของแต่ละโครงการ

| ส่วนประกอบ | สัญญาอนุญาต |
|---|---|
| Ultralytics YOLO | AGPL-3.0 (การใช้เชิงพาณิชย์แบบไม่เปิดซอร์สต้องใช้ Enterprise License) |
| PaddleOCR, PaddlePaddle, OpenCV | Apache-2.0 |
| PyTorch, torchvision, Django, DRF, aiortc, PyAV | BSD |
| django-axes, WhiteNoise | MIT |
| Waitress | ZPL 2.1 |
