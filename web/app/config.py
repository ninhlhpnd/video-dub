"""Cấu hình đọc từ biến môi trường hoặc file web/.env."""
import os
import secrets
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path):
    """Đọc .env đơn giản (KEY=VALUE), không ghi đè biến đã có."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k, v)


_load_dotenv(WEB_DIR / ".env")


class Settings:
    def __init__(self):
        e = os.environ.get
        self.data_dir = Path(e("DATA_DIR") or (WEB_DIR / "data")).resolve()
        self.secret_key = e("SECRET_KEY") or ""
        self.site_name = e("SITE_NAME", "Truyện Hay")
        self.site_url = (e("SITE_URL") or "").rstrip("/")
        self.site_tagline = e("SITE_TAGLINE", "Đọc truyện chữ online miễn phí, cập nhật liên tục")
        self.gemini_api_key = e("GEMINI_API_KEY", "")
        self.gemini_models = [m.strip() for m in e("GEMINI_MODELS", "gemini-flash-latest,gemini-flash-lite-latest").split(",") if m.strip()]
        self.claude_api_key = e("CLAUDE_API_KEY", "")
        self.claude_model = e("CLAUDE_MODEL", "claude-sonnet-5-5")
        self.translator = e("TRANSLATOR", "gemini").lower()
        # Thông tin ủng hộ trang (admin có thể sửa trong bảng quản trị, giá trị trong DB được ưu tiên)
        self.donate_bank = e("SITE_DONATE_BANK", "")
        self.donate_account = e("SITE_DONATE_ACCOUNT", "")
        self.donate_name = e("SITE_DONATE_NAME", "")
        self.donate_momo = e("SITE_DONATE_MOMO", "")
        self.donate_link = e("SITE_DONATE_LINK", "")
        self.donate_webhook_secret = e("DONATE_WEBHOOK_SECRET", "")
        self.worker_enabled = e("WORKER", "1") not in ("0", "false", "no")
        self.fetch_delay = float(e("FETCH_DELAY", "1.5"))
        self.cookie_secure = e("COOKIE_SECURE", "0") in ("1", "true", "yes")

    def ensure(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "covers").mkdir(exist_ok=True)
        (self.data_dir / "avatars").mkdir(exist_ok=True)
        if not self.secret_key:
            # chưa đặt SECRET_KEY: tạo một lần và lưu trong data/ để phiên đăng nhập không mất khi khởi động lại
            f = self.data_dir / "secret_key.txt"
            if f.is_file():
                self.secret_key = f.read_text().strip()
            else:
                self.secret_key = secrets.token_urlsafe(48)
                f.write_text(self.secret_key)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "site.db"


settings = Settings()
