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

def extract_thai_province(text: str) -> str:
    if not text:
        return ""

    normalized = re.sub(r"\s+", "", text)
    normalized = re.sub(r"[^\u0E00-\u0E7F]", "", normalized)

    if len(normalized) < 3:
        return ""

    for province in sorted(THAI_PROVINCES, key=len, reverse=True):
        if province in normalized:
            return province

    best_province = ""
    best_score = 0.0

    for province in THAI_PROVINCES:
        score = SequenceMatcher(None, normalized, province).ratio()
        if score > best_score:
            best_score = score
            best_province = province

    if best_score >= 0.78:
        return best_province

    return ""