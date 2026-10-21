from fetch_annex import is_error_page


def test_script_alert_is_error_page():
    assert is_error_page("<script>\r\nalert('잘못된 접근입니다.');\r\n</script>\r\n".encode())
    assert is_error_page(b"<!DOCTYPE html><html>")
    assert is_error_page(b"")


def test_real_files_are_not_error_pages():
    assert not is_error_page(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
    assert not is_error_page(b"PK\x03\x04rest")
    assert not is_error_page("[별표 1] 기준표".encode())
