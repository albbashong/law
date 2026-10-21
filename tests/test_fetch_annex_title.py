from fetch_annex import _clean_title


def test_clean_title():
    assert _clean_title("[별표 16]  자연녹지지역안에서 건축할 수 있는 건축물(제30조제16호 관련)").startswith("자연녹지지역안에서")
    assert _clean_title("별표 3의2 공개공지 기준") == "공개공지 기준"


def test_xml_with_html_entities_parses():
    from fetch_annex import parse_xml
    root = parse_xml("<a><b>가&nbsp;나 &amp; 다 &#123;</b></a>".encode())
    assert root.findtext("b").startswith("가")
