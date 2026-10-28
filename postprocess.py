#!/usr/bin/env python3
"""Conservative, offline postprocessing for Russian internal passport OCR.

Usage: python postprocess.py input.jsonl output.jsonl
Only the Python standard library is required.
"""
import datetime as dt
import csv
import json
from pathlib import Path
import re
import sys


FIELDS = (
    "Last_name_ru", "First_name_ru", "Middle_name_ru", "Sex_ru",
    "Birth_date", "Birth_place_ru", "Issue_date",
    "Issue_organisation_code", "Licence_number", "Issue_organization_ru",
)
AS_OF = dt.date(2026, 10, 1)
RU = "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
MR = "ABVGDE2JZIQKLMNOPRSTUFHC34WXY9678"
TRANSLIT = str.maketrans(dict(zip(RU, MR)))
MRZ_WEIGHTS = (7, 3, 1)


def subdivision_codes():
    path = Path(__file__).resolve().parent / "reference" / "subdivisions.csv"
    with path.open(encoding="utf-8", newline="") as src:
        return {row["code"] for row in csv.DictReader(src)}


KNOWN_CODES = subdivision_codes()


def norm(value):
    return " ".join(str(value or "").split()).upper()


def read_field(field):
    if field is None:
        return None, 0.0
    lines = field.get("lines") or []
    if not lines:
        return None, 0.0
    # A field box still covers all printed lines even when the line detector
    # silently drops one. Such a partial string must not be accepted.
    box = field.get("box") or [0, 0, 0, 0]
    heights = [(line.get("box") or [0, 0, 0, 0])[3] for line in lines]
    if len(lines) == 1 and heights[0] and box[3] > 1.45 * heights[0]:
        return None, 0.0
    if len(lines) > 1 and heights and max(heights) and box[3] > (len(lines) + 0.45) * max(heights):
        return None, 0.0
    value = norm(" ".join(line.get("text", "") for line in lines))
    conf = [c for line in lines for c in line.get("char_conf", [])]
    return value, min(conf) if conf else 0.0


def date(value):
    try:
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
            return None
        return dt.datetime.strptime(value, "%d.%m.%Y").date()
    except (ValueError, TypeError):
        return None


def birthday_in_year(birth, year):
    # For 29 February use 1 March in non-leap years at age boundaries.
    try:
        return birth.replace(year=year)
    except ValueError:
        return dt.date(year, 3, 1)


def valid_on_date(fields):
    birth, issue = date(fields["Birth_date"]), date(fields["Issue_date"])
    if not birth or not issue or birth > issue or issue > AS_OF:
        return False
    if AS_OF.year - birth.year > 120:
        return False
    fourteenth = birthday_in_year(birth, birth.year + 14)
    if fourteenth is None or issue < fourteenth:
        return False
    # An issued passport remains valid until replacement at 20 or 45,
    # subject to a maximum of 90 calendar days after that birthday.
    for age in (20, 45):
        boundary = birthday_in_year(birth, birth.year + age)
        if boundary is None:
            return False
        if issue < boundary and AS_OF > boundary + dt.timedelta(days=90):
            return False
    return True


def check_digit(value):
    total = 0
    for i, ch in enumerate(value):
        v = int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10 if "A" <= ch <= "Z" else 0 if ch == "<" else -1000
        total += v * MRZ_WEIGHTS[i % 3]
    return str(total % 10)


def mrz_consistent(fields, mrz):
    if not mrz or len(mrz.get("lines") or []) != 2:
        return False
    a, b = (line.get("text", "").upper() for line in mrz["lines"])
    if len(a) != 44 or len(b) != 44 or not a.startswith("PNRUS") or b[10:13] != "RUS":
        return False
    if not re.fullmatch(r"[A-Z0-9<]{44}", a) or not re.fullmatch(r"[A-Z0-9<]{44}", b):
        return False
    if b[9] != check_digit(b[:9]) or b[19] != check_digit(b[13:19]):
        return False
    if b[42] != check_digit(b[28:41]) or b[43] != check_digit(b[:10] + b[13:20] + b[21:43]):
        return False
    # MRZ includes 1st, 2nd and 4th digits of the passport series at 0:3, 28.
    serial = re.fullmatch(r"(\d{2}) (\d{2}) (\d{6})", fields["Licence_number"])
    code = re.fullmatch(r"\d{3}-\d{3}", fields["Issue_organisation_code"])
    if not serial or not code:
        return False
    if b[:9] != serial[1] + serial[2][0] + serial[3] or b[28] != serial[2][1]:
        return False
    if b[35:41] != fields["Issue_organisation_code"].replace("-", ""):
        return False
    birth, issue = date(fields["Birth_date"]), date(fields["Issue_date"])
    if not birth or not issue:
        return False
    if b[13:19] != birth.strftime("%y%m%d") or b[29:35] != issue.strftime("%y%m%d"):
        return False
    if b[20] != {"МУЖ.": "M", "ЖЕН.": "F"}.get(fields["Sex_ru"]):
        return False
    # Names may be cut off at position 44; compare only the visible prefix.
    name = (fields["Last_name_ru"] + "<<" + fields["First_name_ru"] + "<" + fields["Middle_name_ru"])
    name = name.replace("-", "<").replace(" ", "<").translate(TRANSLIT)
    visible = a[5:].rstrip("<")
    name = name.rstrip("<")
    if not (name == visible or (len(visible) == 39 and name.startswith(visible))):
        return False
    return True


def process(doc, thresholds=None, require_mrz=True, include_mobile_v2=False):
    thresholds = thresholds or {
        "Last_name_ru": 0.90, "First_name_ru": 0.90,
        "Middle_name_ru": 0.90, "Sex_ru": 0.995,
        "Birth_date": 0.995, "Birth_place_ru": 0.99,
        "Issue_date": 0.995, "Issue_organisation_code": 0.995,
        "Licence_number": 0.995, "Issue_organization_ru": 0.99,
    }
    reject = {"doc_id": doc["doc_id"], "decision": "reject"}
    if doc.get("source") == "mobile_v2" and not include_mobile_v2:
        return reject
    fields = {}
    for field in FIELDS:
        value, confidence = read_field((doc.get("viz") or {}).get(field))
        if field == "Middle_name_ru" and value is None:
            value = ""
            confidence = 1.0
        if value is None or confidence < thresholds[field]:
            return reject
        fields[field] = value
    if fields["Sex_ru"] not in ("МУЖ.", "ЖЕН."):
        return reject
    if fields["Issue_organisation_code"] not in KNOWN_CODES:
        return reject
    if not valid_on_date(fields):
        return reject
    if require_mrz and not mrz_consistent(fields, doc.get("mrz")):
        return reject
    return {"doc_id": doc["doc_id"], "decision": "accept", "fields": fields}


def main(input_path, output_path):
    with open(input_path, encoding="utf-8") as src, open(output_path, "w", encoding="utf-8") as dst:
        for line in src:
            if line.strip():
                dst.write(json.dumps(process(json.loads(line)), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: python postprocess.py input.jsonl output.jsonl")
    main(sys.argv[1], sys.argv[2])
