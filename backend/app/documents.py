import re
from datetime import date
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps


def checksum(value):
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return str(
        sum((alphabet.index(c) if c != "<" else 0) * (7, 3, 1)[i % 3] for i, c in enumerate(value)) % 10
    )


def mrz_date(value, expiry=False):
    year, month, day = int(value[:2]), int(value[2:4]), int(value[4:6])
    today = date.today()
    year += 2000 if expiry or year <= today.year % 100 else 1900
    return date(year, month, day).isoformat()


def parse_mrz(lines):
    clean = [re.sub(r"[^A-Z0-9<]", "", line.upper()) for line in lines]
    for i, a in enumerate(clean[:-1]):
        b = clean[i + 1]
        if len(a) != 44 or len(b) != 44 or not a.startswith("P"):
            continue
        try:
            checks = [
                checksum(b[:9]) == b[9],
                checksum(b[13:19]) == b[19],
                checksum(b[21:27]) == b[27],
                checksum(b[:10] + b[13:20] + b[21:43]) == b[43],
            ]
            if b[42] != "<":
                checks.append(checksum(b[28:42]) == b[42])
            surname, _, given = a[5:].partition("<<")
            fields = {
                "country": a[2:5],
                "number": b[:9].replace("<", ""),
                "name": " ".join((surname + " " + given).replace("<", " ").split()),
                "birth_date": mrz_date(b[13:19]),
                "expiry_date": mrz_date(b[21:27], True),
            }
            issues = [] if all(checks) else ["MRZ_CHECKSUM"]
            if date.fromisoformat(fields["expiry_date"]) < date.today():
                issues.append("DOCUMENT_EXPIRED")
            return fields, issues
        except (ValueError, IndexError):
            continue
    return None, ["MRZ_UNREADABLE_OR_UNSUPPORTED"]


def read_document(path: Path):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        if min(image.size) < 450:
            return {
                "status": "RETAKE",
                "fields": {},
                "issues": ["IMAGE_TOO_SMALL"],
                "engine": "tesseract-td3",
            }
        image.thumbnail((2400, 2400))
        text = pytesseract.image_to_string(image, lang="eng", config="--psm 6", timeout=30)
        # A separate bottom crop improves MRZ reading without substituting similar characters.
        crop = image.crop((0, int(image.height * 0.72), image.width, image.height))
        mrz_text = pytesseract.image_to_string(
            crop,
            lang="eng",
            config="--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
            timeout=30,
        )
        mrz_lines = []
        for line in mrz_text.splitlines():
            value = re.sub(r"[^A-Z0-9<]", "", line)
            if value.startswith("P<") and "<<" in value and value.endswith("<<<") and 30 <= len(value) < 44:
                value = value.ljust(44, "<")
            mrz_lines.append(value)
        fields, issues = parse_mrz(mrz_lines)
        if fields is None:
            fields, issues = parse_mrz(text.splitlines())
        if fields is None:
            return {"status": "REVIEW_REQUIRED", "fields": {}, "issues": issues, "engine": "tesseract-td3"}
        visible = "\n".join(line for line in text.splitlines() if "<" not in line)
        normalized = re.sub(r"[^A-Z0-9]", "", visible.upper())
        if fields["number"] not in normalized:
            issues.append("VISIBLE_NUMBER_NEEDS_REVIEW")
        surname = re.search(r"(?:SURNAME|LAST NAME)\s*[:.]?\s+([A-Z ]+)", visible.upper())
        given = re.search(r"(?:GIVEN NAMES?|FIRST NAMES?)\s*[:.]?\s+([A-Z ]+)", visible.upper())
        if surname and given:
            visible_name = " ".join((surname[1] + " " + given[1]).split())
            if visible_name != fields["name"]:
                issues.append("VISIBLE_NAME_MISMATCH")
        else:
            issues.append("VISIBLE_NAME_NEEDS_REVIEW")
        return {
            "status": "PASSED" if not issues else "REVIEW_REQUIRED",
            "fields": fields,
            "issues": issues,
            "engine": "tesseract-td3",
            "note": "Consistency checks only; original required at window",
        }
