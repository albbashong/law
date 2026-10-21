from indexer.annex_items import split_annex


def test_enforcement_annex17(annex17_text):
    items = {p: (label, text) for p, label, text in split_annex(annex17_text)}
    label, text = items["2/차"]
    assert label == "제2호 차목"
    assert "제17호의 공장" in text
    assert "첨단업종의 공장" in items["2/차/1"][1]
    assert "건축할 수 있는 건축물" in items["1"][1]


def test_wrapped_lines_are_joined(annex17_text):
    items = {p: t for p, _, t in split_annex(annex17_text)}
    # 원문은 고정폭으로 줄이 끊겨 있다("…그 층수 이하의 건 / 축물에 한한다)").
    # 이어 붙인 줄이 같은 항목에 들어가는지 공백을 빼고 본다.
    assert "그층수이하의건축물에한한다" in items["1"].replace(" ", "")


def test_ordin_annex16(ordin_annex16_text):
    items = {p: t for p, _, t in split_annex(ordin_annex16_text)}
    assert "제17호의 공장" in items["15"]
    assert "첨단업종의 공장" in items["15/1"]
    assert "첨단업종의 공장" in items["15"]


def test_item_marker_without_space():
    text = "1. 건축할 수 없는 건축물\n사. 숙박시설로서 설치하는 것 아.「건축법 시행령」 별표 1 제16호의 위락시설\n"
    items = {p: t for p, _, t in split_annex(text.replace(" 아.「", "\n아.「"))}
    assert "위락시설" in items["1/아"]


def test_item_marker_glued_on_same_line_is_split():
    # 원문 별표 20: "…설치하는 것 아.「건축법 시행령」 별표 1 제16호의 위락시설" 이 한 줄로 붙어 온다
    text = "1. 건축할 수 없는 건축물\n사. 숙박시설로서 설치하는 것 아.「건축법 시행령」 별표 1 제16호의 위락시설\n"
    items = {p: t for p, _, t in split_annex(text)}
    assert "1/아" in items and "위락시설" in items["1/아"] and "위락시설" not in items["1/사"]
