"""Thư viện nhạc nền miễn phí bản quyền: nhạc của Kevin MacLeod (incompetech.com), giấy phép CC BY 4.0
— được dùng cả cho video kiếm tiền, chỉ cần ghi tên tác giả (app tự thêm dòng ghi nguồn vào mô tả khi đăng)."""
from pathlib import Path
from urllib.parse import quote

import requests

from .common import TOOLS, log, progress

FOLDER = TOOLS / "music"
BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"

LIBRARY = [
    # (tên bài, tâm trạng)
    ("Ice Flow", "Hồi hộp, bí ẩn"),
    ("Darkling", "Hồi hộp, bí ẩn"),
    ("Investigations", "Hồi hộp, bí ẩn"),
    ("Comfortable Mystery 4", "Hồi hộp, bí ẩn"),
    ("Hidden Agenda", "Hồi hộp, bí ẩn"),
    ("Crypto", "Hồi hộp, bí ẩn"),
    ("Unseen Horrors", "Kinh dị"),
    ("Volatile Reaction", "Hành động, kịch tính"),
    ("Movement Proposition", "Hành động, kịch tính"),
    ("Five Armies", "Hành động, kịch tính"),
    ("Impact Prelude", "Hoành tráng"),
    ("Heroic Age", "Hoành tráng"),
    ("Sneaky Snitch", "Hài hước"),
    ("Monkeys Spinning Monkeys", "Hài hước"),
    ("Fluffing a Duck", "Hài hước"),
    ("Dreamy Flashback", "Cảm động, nhẹ nhàng"),
    ("Heartwarming", "Cảm động, nhẹ nhàng"),
    ("Sad Trio", "Buồn"),
]


def credit(title):
    return f'Nhạc: "{title}" - Kevin MacLeod (incompetech.com), giấy phép CC BY 4.0'


def file_for(title):
    return FOLDER / f"{title}.mp3"


def title_of(path):
    """Tên bài nếu [path] là nhạc trong thư viện, không thì None."""
    p = Path(path or "")
    return p.stem if p.parent == FOLDER and any(t == p.stem for t, _ in LIBRARY) else None


def ensure():
    """Tải các bài còn thiếu; trả về danh sách bài kèm đường dẫn."""
    FOLDER.mkdir(parents=True, exist_ok=True)
    missing = [t for t, _ in LIBRARY if not file_for(t).exists()]
    for k, title in enumerate(missing):
        progress("music", k / len(missing) * 100, f"Đang tải nhạc {k + 1}/{len(missing)}: {title}")
        tmp = file_for(title).with_suffix(".part")
        try:
            with requests.get(BASE + quote(title) + ".mp3", stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
            tmp.replace(file_for(title))
        except Exception as e:
            tmp.unlink(missing_ok=True)
            log(f"Không tải được bài {title}: {e}")
    return [{"title": t, "mood": m, "file": str(file_for(t)), "credit": credit(t)}
            for t, m in LIBRARY if file_for(t).exists()]
