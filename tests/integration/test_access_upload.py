"""S07 集成测试：鉴权、会话、Origin 校验、受限上传。"""
from pathlib import Path
import zipfile

import pytest
from fastapi import HTTPException

from nero_ppt.db import connection as dbconn
from nero_ppt.services.access import AccessService, check_same_origin, hash_password, verify_password
from nero_ppt.services.uploads import UploadError, validate_upload
from nero_ppt.web.deps import require_same_origin

MATERIAL_EXTS = {".pdf": "", ".docx": "", ".txt": "", ".md": "", ".pptx": ""}


@pytest.fixture()
def access(tmp_path):
    conn = dbconn.connect(tmp_path / "auth.db")
    dbconn.migrate(conn)
    svc = AccessService(conn)
    yield svc
    conn.close()


def test_password_irreversible_and_verifiable():
    h = hash_password("secret-pw")
    assert "secret-pw" not in h
    assert verify_password("secret-pw", h)
    assert not verify_password("wrong", h)


def test_user_create_once_only(access):
    pw = access.create_user("alice")
    assert len(pw) >= 16  # 高熵
    with pytest.raises(ValueError):
        access.create_user("alice")


def test_login_flow_and_logout(access):
    access.create_user("alice")
    session = access.login("alice", "wrong-password")
    assert session is None
    session = access.login("alice", None)  # 密码为 None 不崩溃
    assert session is None
    good = access.login("alice", _pw_of(access, "alice"))
    assert good
    user = access.session_user(good)
    assert user["username"] == "alice"
    access.logout(good)
    assert access.session_user(good) is None


def _pw_of(access, username: str) -> str:
    """测试辅助：直接重置为已知密码。"""
    from nero_ppt.services.access import hash_password

    user = access.get_user_by_name(username)
    access.conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password("known-pw-1"), user["id"]))
    return "known-pw-1"


def test_login_throttle_after_5_failures(access):
    access.create_user("bob")
    for _ in range(5):
        assert access.login("bob", "bad", ip="1.2.3.4") is None
    # 第 6 次：即使密码正确也被限流拒绝
    assert access.login("bob", _pw_of(access, "bob"), ip="1.2.3.4") is None
    # 其他 IP 不受影响
    assert access.login("bob", _pw_of(access, "bob"), ip="5.6.7.8") is not None


def test_origin_check():
    assert check_same_origin("http://host.example/path", "host.example")
    assert not check_same_origin("http://evil.example", "host.example")
    assert check_same_origin(None, "host.example")  # 非浏览器客户端


def test_require_same_origin_raises():
    import starlette.requests

    class FakeURL:
        def __init__(self):
            self.netloc = ""

    class FakeRequest:
        headers = {"origin": "http://evil.example", "host": "good.example"}

    with pytest.raises(HTTPException) as e:
        require_same_origin(FakeRequest())
    assert e.value.status_code == 403


# ---------------------------------------------------------------- 上传


def _make_pptx_bytes() -> bytes:
    from pptx import Presentation

    import io

    buf = io.BytesIO()
    Presentation().save(buf)
    return buf.getvalue()


def test_upload_type_and_signature():
    pptx = _make_pptx_bytes()
    assert validate_upload("模板.pptx", pptx, allowed_exts=MATERIAL_EXTS, max_bytes=30 * 1024 * 1024) == ".pptx"
    with pytest.raises(UploadError):
        validate_upload("假.docx", b"not-a-zip", allowed_exts=MATERIAL_EXTS, max_bytes=30 * 1024 * 1024)
    with pytest.raises(UploadError):
        validate_upload("脚本.exe", pptx, allowed_exts=MATERIAL_EXTS, max_bytes=30 * 1024 * 1024)
    with pytest.raises(UploadError):
        validate_upload("空.pdf", b"", allowed_exts=MATERIAL_EXTS, max_bytes=30 * 1024 * 1024)
    with pytest.raises(UploadError):
        validate_upload("大.pdf", b"x" * (31 * 1024 * 1024), allowed_exts=MATERIAL_EXTS, max_bytes=30 * 1024 * 1024)


def test_upload_pdf_signature():
    ok_pdf = b"%PDF-1.7\n" + b"x" * 100
    assert validate_upload("a.pdf", ok_pdf, allowed_exts=MATERIAL_EXTS, max_bytes=1024 * 1024) == ".pdf"
    with pytest.raises(UploadError):
        validate_upload("伪装.pdf", b"MZ" + b"x" * 100, allowed_exts=MATERIAL_EXTS, max_bytes=1024 * 1024)


def test_upload_txt_utf8():
    assert validate_upload("a.txt", "中文内容".encode(), allowed_exts=MATERIAL_EXTS, max_bytes=1024) == ".txt"
    with pytest.raises(UploadError):
        validate_upload("b.txt", b"\xff\xfe\x00garbage", allowed_exts=MATERIAL_EXTS, max_bytes=1024)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_malicious_zip_rejected():
    pptx = _make_pptx_bytes()
    # 路径逃逸
    evil = _zip_bytes({"../evil.txt": b"x"})
    with pytest.raises(UploadError):
        validate_upload("evil.pptx", evil, allowed_exts=MATERIAL_EXTS, max_bytes=1024 * 1024)
    # 绝对路径
    evil2 = _zip_bytes({"/etc/evil.txt": b"x"})
    with pytest.raises(UploadError):
        validate_upload("evil2.pptx", evil2, allowed_exts=MATERIAL_EXTS, max_bytes=1024 * 1024)
    # 正常 OOXML 通过
    assert validate_upload("好.pptx", pptx, allowed_exts=MATERIAL_EXTS, max_bytes=1024 * 1024) == ".pptx"


def test_zip_bomb_guard():
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(2100):
            zf.writestr(f"f{i}.txt", b"x")
    data = buf.getvalue()
    with pytest.raises(UploadError) as e:
        validate_upload("many.pptx", data, allowed_exts=MATERIAL_EXTS, max_bytes=1024 * 1024)
    assert "条目数" in str(e.value)
