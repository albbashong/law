import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from indexer.units import law_units, ordin_units

FIX = Path(__file__).parent / "fixtures"


def _root(name):
    return ET.fromstring(gzip.open(FIX / name).read())


def test_law_units_paths():
    units = {u.path: u for u in law_units(_root("law_288857.xml.gz"))}
    assert units["제84조"].title == "용도지역안에서의 건폐율"
    u = units["제84조/1/16"]
    assert u.label == "제84조제1항제16호"
    assert "자연녹지지역" in u.text and "20퍼센트" in u.text
    assert units["제71조/1/16"].text.startswith("16. 자연녹지지역")
    assert "제4조의2" in units


def test_law_article_text_contains_children():
    units = {u.path: u for u in law_units(_root("law_288857.xml.gz"))}
    assert "자연녹지지역 : 20퍼센트 이하" in units["제84조"].text


def test_ordin_units_flat_text():
    units = {u.path: u for u in ordin_units(_root("ordin_2000711.xml.gz"))}
    assert units["제56조"].title == "용도지역 안에서의 건폐율"
    u = units["제56조/-/16"]
    assert u.label == "제56조제16호"
    assert u.text.startswith("16. 자연녹지지역 : 20퍼센트 이하")
    assert "영 제84조제1항" in units["제56조"].text


def test_ordin_hang_split():
    units = {u.path: u for u in ordin_units(_root("ordin_2000711.xml.gz"))}
    # 제59조는 ①항 아래 호가 있다
    assert "제59조/1" in units
    assert units["제59조/1/3"].text.startswith("3. 자연녹지지역의 창고시설")
