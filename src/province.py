import re
from difflib import SequenceMatcher


# Thai name (as OCR reads it from the plate) -> official English name, for the dashboard's province list
PROVINCE_NAMES_EN = {
    "กระบี่": "Krabi", "กาญจนบุรี": "Kanchanaburi", "กาฬสินธุ์": "Kalasin", "กำแพงเพชร": "Kamphaeng Phet",
    "ขอนแก่น": "Khon Kaen", "จันทบุรี": "Chanthaburi", "ฉะเชิงเทรา": "Chachoengsao", "ชลบุรี": "Chon Buri",
    "ชัยนาท": "Chai Nat", "ชัยภูมิ": "Chaiyaphum", "ชุมพร": "Chumphon", "เชียงราย": "Chiang Rai",
    "เชียงใหม่": "Chiang Mai", "ตรัง": "Trang", "ตราด": "Trat", "ตาก": "Tak", "นครนายก": "Nakhon Nayok",
    "นครปฐม": "Nakhon Pathom", "นครพนม": "Nakhon Phanom", "นครราชสีมา": "Nakhon Ratchasima",
    "นครศรีธรรมราช": "Nakhon Si Thammarat", "นครสวรรค์": "Nakhon Sawan", "นนทบุรี": "Nonthaburi",
    "นราธิวาส": "Narathiwat", "น่าน": "Nan", "บึงกาฬ": "Bueng Kan", "บุรีรัมย์": "Buri Ram",
    "ปทุมธานี": "Pathum Thani", "ประจวบคีรีขันธ์": "Prachuap Khiri Khan", "ปราจีนบุรี": "Prachin Buri",
    "ปัตตานี": "Pattani", "พระนครศรีอยุธยา": "Phra Nakhon Si Ayutthaya", "พะเยา": "Phayao", "พังงา": "Phangnga",
    "พัทลุง": "Phatthalung", "พิจิตร": "Phichit", "พิษณุโลก": "Phitsanulok", "เพชรบุรี": "Phetchaburi",
    "เพชรบูรณ์": "Phetchabun", "แพร่": "Phrae", "ภูเก็ต": "Phuket", "มหาสารคาม": "Maha Sarakham",
    "มุกดาหาร": "Mukdahan", "แม่ฮ่องสอน": "Mae Hong Son", "ยะลา": "Yala", "ยโสธร": "Yasothon",
    "ร้อยเอ็ด": "Roi Et", "ระนอง": "Ranong", "ระยอง": "Rayong", "ราชบุรี": "Ratchaburi", "ลพบุรี": "Lop Buri",
    "ลำปาง": "Lampang", "ลำพูน": "Lamphun", "เลย": "Loei", "ศรีสะเกษ": "Si Sa Ket", "สกลนคร": "Sakon Nakhon",
    "สงขลา": "Songkhla", "สตูล": "Satun", "สมุทรปราการ": "Samut Prakan", "สมุทรสงคราม": "Samut Songkhram",
    "สมุทรสาคร": "Samut Sakhon", "สระแก้ว": "Sa Kaeo", "สระบุรี": "Saraburi", "สิงห์บุรี": "Sing Buri",
    "สุโขทัย": "Sukhothai", "สุพรรณบุรี": "Suphan Buri", "สุราษฎร์ธานี": "Surat Thani", "สุรินทร์": "Surin",
    "หนองคาย": "Nong Khai", "หนองบัวลำภู": "Nong Bua Lam Phu", "อ่างทอง": "Ang Thong",
    "อำนาจเจริญ": "Amnat Charoen", "อุดรธานี": "Udon Thani", "อุตรดิตถ์": "Uttaradit", "อุทัยธานี": "Uthai Thani",
    "อุบลราชธานี": "Ubon Ratchathani", "กรุงเทพมหานคร": "Bangkok",
}
THAI_PROVINCES = list(PROVINCE_NAMES_EN)
# Longest first, so a whole name is found before a shorter one that is part of it
_LONGEST_FIRST = sorted(THAI_PROVINCES, key=len, reverse=True)
# Lowest similarity at which OCR text without a whole province name is still read as the closest province
_MIN_SIMILARITY = 0.78


def extract_thai_province(text: str) -> str:
    # The province on a plate's province line as OCR read it: a name found whole in the text, else the most
    # similar name when it is similar enough; '' when there is none.
    thai = re.sub(r"[^\u0E00-\u0E7F]", "", text or "")   # Thai characters only (no spaces, digits, Latin)
    if len(thai) < 3:
        return ""
    for province in _LONGEST_FIRST:
        if province in thai:
            return province
    scores = {province: SequenceMatcher(None, thai, province).ratio() for province in THAI_PROVINCES}
    best = max(scores, key=scores.get)
    return best if scores[best] >= _MIN_SIMILARITY else ""