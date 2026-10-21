import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def annex17_text():
    root = ET.fromstring(gzip.open(FIX / "law_288857.xml.gz").read())
    for b in root.iter("별표단위"):
        if b.findtext("별표번호") == "0017" and b.findtext("별표구분") == "별표":
            return "".join(x.text or "" for x in b.iter("별표내용"))
    raise AssertionError("별표 17 없음")


@pytest.fixture
def ordin_annex16_text():
    return (FIX / "ordin_annex16.txt").read_text()
