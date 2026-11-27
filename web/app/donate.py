"""Ủng hộ: danh sách ngân hàng, link ảnh VietQR, thông tin nhận tiền của tác giả/trang."""
from urllib.parse import quote

from .config import settings
from .db import get_setting
from .utils import unaccent

# (mã BIN dùng trong VietQR, tên viết tắt, tên đầy đủ)
BANKS = [
    ("970436", "Vietcombank", "Ngân hàng TMCP Ngoại thương Việt Nam"),
    ("970415", "VietinBank", "Ngân hàng TMCP Công thương Việt Nam"),
    ("970418", "BIDV", "Ngân hàng TMCP Đầu tư và Phát triển Việt Nam"),
    ("970405", "Agribank", "Ngân hàng Nông nghiệp và Phát triển Nông thôn"),
    ("970407", "Techcombank", "Ngân hàng TMCP Kỹ thương Việt Nam"),
    ("970422", "MB Bank", "Ngân hàng TMCP Quân đội"),
    ("970416", "ACB", "Ngân hàng TMCP Á Châu"),
    ("970432", "VPBank", "Ngân hàng TMCP Việt Nam Thịnh Vượng"),
    ("970423", "TPBank", "Ngân hàng TMCP Tiên Phong"),
    ("970403", "Sacombank", "Ngân hàng TMCP Sài Gòn Thương Tín"),
    ("970437", "HDBank", "Ngân hàng TMCP Phát triển TP.HCM"),
    ("970441", "VIB", "Ngân hàng TMCP Quốc tế Việt Nam"),
    ("970443", "SHB", "Ngân hàng TMCP Sài Gòn - Hà Nội"),
    ("970431", "Eximbank", "Ngân hàng TMCP Xuất Nhập khẩu Việt Nam"),
    ("970426", "MSB", "Ngân hàng TMCP Hàng Hải"),
    ("970448", "OCB", "Ngân hàng TMCP Phương Đông"),
    ("970440", "SeABank", "Ngân hàng TMCP Đông Nam Á"),
    ("970449", "LPBank", "Ngân hàng TMCP Lộc Phát Việt Nam"),
    ("970428", "Nam A Bank", "Ngân hàng TMCP Nam Á"),
    ("970409", "Bac A Bank", "Ngân hàng TMCP Bắc Á"),
    ("970412", "PVcomBank", "Ngân hàng TMCP Đại Chúng Việt Nam"),
    ("970454", "BVBank", "Ngân hàng TMCP Bản Việt"),
    ("970452", "KienlongBank", "Ngân hàng TMCP Kiên Long"),
    ("970425", "ABBANK", "Ngân hàng TMCP An Bình"),
    ("970427", "VietABank", "Ngân hàng TMCP Việt Á"),
    ("970433", "VietBank", "Ngân hàng TMCP Việt Nam Thương Tín"),
    ("970419", "NCB", "Ngân hàng TMCP Quốc Dân"),
    ("970438", "BaoViet Bank", "Ngân hàng TMCP Bảo Việt"),
    ("970400", "Saigonbank", "Ngân hàng TMCP Sài Gòn Công Thương"),
    ("546034", "CAKE", "Ngân hàng số CAKE by VPBank"),
    ("546035", "Ubank", "Ngân hàng số Ubank by VPBank"),
    ("963388", "Timo", "Ngân hàng số Timo by BVBank"),
    ("970424", "Shinhan Bank", "Ngân hàng TNHH MTV Shinhan Việt Nam"),
    ("970457", "Woori Bank", "Ngân hàng TNHH MTV Woori Việt Nam"),
]
BANK_BY_BIN = {b[0]: b for b in BANKS}

PRESET_AMOUNTS = [10_000, 20_000, 50_000, 100_000]


def bank_name(bin_code: str) -> str:
    b = BANK_BY_BIN.get(bin_code or "")
    return b[1] if b else (bin_code or "")


def vietqr_url(bank: str, account: str, amount=None, add_info: str = "", account_name: str = "") -> str:
    """https://img.vietqr.io/image/{BANK}-{ACCOUNT}-compact2.png?amount=..&addInfo=..&accountName=.."""
    bank = quote(str(bank).strip(), safe="")
    account = quote(str(account).strip(), safe="")
    url = f"https://img.vietqr.io/image/{bank}-{account}-compact2.png"
    params = []
    if amount:
        params.append(f"amount={int(amount)}")
    if add_info:
        params.append("addInfo=" + quote(add_info, safe=""))
    if account_name:
        params.append("accountName=" + quote(account_name, safe=""))
    return url + ("?" + "&".join(params) if params else "")


def transfer_note(story_id, username: str = "") -> str:
    """Nội dung chuyển khoản dạng `UH {story_id} {username}` (không dấu, để ngân hàng không cắt chữ)."""
    who = unaccent(username or "bandoc").replace(" ", "")[:20] or "bandoc"
    return f"UH {story_id or 0} {who}"


def payee_from_user(user) -> dict | None:
    """Thông tin nhận ủng hộ của một thành viên; None nếu chưa khai báo gì."""
    if not user:
        return None
    info = {
        "name": user["display_name"],
        "bank": user["bank_bin"], "bank_name": bank_name(user["bank_bin"]),
        "account": user["bank_account"], "holder": user["bank_holder"],
        "momo": user["momo"], "paypal": user["paypal"], "kofi": user["kofi"],
    }
    if not ((info["bank"] and info["account"]) or info["momo"] or info["paypal"] or info["kofi"]):
        return None
    return info


def site_payee() -> dict | None:
    info = {
        "name": settings.site_name,
        "bank": get_setting("donate_bank", settings.donate_bank),
        "account": get_setting("donate_account", settings.donate_account),
        "holder": get_setting("donate_name", settings.donate_name),
        "momo": get_setting("donate_momo", settings.donate_momo),
        "paypal": get_setting("donate_link", settings.donate_link),
        "kofi": "",
    }
    info["bank_name"] = bank_name(info["bank"])
    if not ((info["bank"] and info["account"]) or info["momo"] or info["paypal"]):
        return None
    return info
