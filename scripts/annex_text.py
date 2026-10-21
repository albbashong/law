"""별표 첨부파일(HWP/HWPX/PDF/TXT) 바이트에서 텍스트를 추출한다."""
import io
import re
import struct
import subprocess
import tempfile
import zipfile
import zlib

import olefile

HWPTAG_PARA_TEXT = 67
# HWP 문단 텍스트의 제어문자: 이 코드들은 뒤따르는 7개 WCHAR(총 16바이트)를 차지한다
EXTENDED_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def detect(data):
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "hwp"
    if data[:4] == b"PK\x03\x04":
        return "hwpx"
    if data[:5] == b"%PDF-":
        return "pdf"
    return "txt"


def _para_text(raw):
    out = []
    i = 0
    n = len(raw) // 2
    while i < n:
        code = struct.unpack_from("<H", raw, i * 2)[0]
        if code in EXTENDED_CTRL:
            if code == 9:
                out.append("\t")
            i += 8
            continue
        if code in (10, 13):
            out.append("\n")
        elif code >= 32:
            out.append(chr(code))
        i += 1
    # 보충 평면 문자는 서로게이트 쌍으로 들어오므로 UTF-16으로 다시 묶어 디코딩한다
    return "".join(out).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def hwp_text(data):
    ole = olefile.OleFileIO(io.BytesIO(data))
    compressed = ole.openstream("FileHeader").read()[36] & 1
    sections = sorted((e for e in ole.listdir() if e[0] == "BodyText"),
                      key=lambda e: int(re.sub(r"\D", "", e[1]) or 0))
    paras = []
    for sec in sections:
        buf = ole.openstream(sec).read()
        if compressed:
            buf = zlib.decompress(buf, -15)
        pos = 0
        while pos + 4 <= len(buf):
            header = struct.unpack_from("<I", buf, pos)[0]
            tag, size = header & 0x3FF, (header >> 20) & 0xFFF
            pos += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", buf, pos)[0]
                pos += 4
            if tag == HWPTAG_PARA_TEXT:
                paras.append(_para_text(buf[pos:pos + size]))
            pos += size
    return "\n".join(paras)


def hwpx_text(data):
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = sorted((n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                   key=lambda n: int(re.sub(r"\D", "", n)))
    paras = []
    for name in names:
        xml = zf.read(name).decode("utf-8", "replace")
        for p in re.findall(r"<hp:p\b.*?</hp:p>", xml, re.S):
            paras.append("".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", p, re.S)))
    text = "\n".join(paras)
    return re.sub(r"<[^>]+>", "", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def pdf_text(data):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True,
                              timeout=60, check=True).stdout.decode("utf-8", "replace")


def _natural(name):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def zip_text(data):
    """별표 파일 여러 개를 zip 으로 묶은 첨부 — 파일마다 이름(보통 '[별표 N] 제목')을 머리말로 붙여 잇는다."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    parts = []
    for name in sorted((n for n in zf.namelist() if not n.endswith("/")), key=_natural):
        _, text = extract(zf.read(name))
        parts.append(f"{re.sub(r'[.][^.]+$', '', name.rsplit('/', 1)[-1])}\n{text}")
    return "\n\n".join(parts)


def extract(data):
    """(형식, 텍스트) 반환. 빈 줄은 정리한다."""
    kind = detect(data)
    if kind == "hwpx" and not any(re.match(r"Contents/section\d+\.xml$", n)
                                  for n in zipfile.ZipFile(io.BytesIO(data)).namelist()):
        kind = "zip"
    if kind == "zip":
        text = zip_text(data)
    elif kind == "hwp":
        text = hwp_text(data)
    elif kind == "hwpx":
        text = hwpx_text(data)
    elif kind == "pdf":
        text = pdf_text(data)
    else:
        for enc in ("utf-8", "cp949"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                text = data.decode("utf-8", "replace")
    lines = [ln.rstrip() for ln in text.splitlines()]
    return kind, re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
