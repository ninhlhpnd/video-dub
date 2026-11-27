"""Tạo dữ liệu mẫu để xem giao diện (CHỈ dùng khi phát triển).

    python seed.py           # thêm dữ liệu mẫu nếu chưa có
    python seed.py --reset   # xoá data/site.db rồi tạo lại
"""
import random
import sys
import time

from app.config import settings

try:  # console Windows mặc định cp1252 → in tiếng Việt bị lỗi
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    if "--reset" in sys.argv:
        for suffix in ("", "-wal", "-shm"):
            p = settings.db_path.with_name(settings.db_path.name + suffix)
            if p.exists():
                p.unlink()
    from app.auth import create_user
    from app.covers import placeholder_cover
    from app.db import ex, migrate, now, q1, tx, val
    from app.services import create_story, genre_ids_from_names, refresh_story_stats, update_story, upsert_chapter
    from app.utils import day_offset

    migrate()
    if val("SELECT COUNT(*) FROM stories", default=0):
        print("DB đã có truyện, bỏ qua. Dùng `python seed.py --reset` để tạo lại từ đầu.")
        return
    rnd = random.Random(42)

    admin = q1("SELECT * FROM users WHERE username='admin'") or create_user("admin", "admin123", "Quản trị viên", role="admin")
    author = q1("SELECT * FROM users WHERE username='tacgia'") or create_user("tacgia", "tacgia123", "Mặc Vân", role="uploader")
    reader = q1("SELECT * FROM users WHERE username='bandoc'") or create_user("bandoc", "bandoc123", "Bạn Đọc Chăm Chỉ")
    ex("UPDATE users SET bank_bin='970436', bank_account='0123456789', bank_holder='TAC GIA MAU', momo='0900000000', "
       "bio='Tài khoản mẫu để xem giao diện.' WHERE id=?", (author["id"],))

    stories = [
        dict(title="Kiếm Đạo Độc Tôn", author="Mặc Vân", genres=["Tiên hiệp", "Huyền huyễn"], status="ongoing", origin="original",
             desc="Thiếu niên Lâm Phong sinh ra ở trấn nhỏ Thanh Dương, kinh mạch bế tắc, bị người đời chê cười là phế vật.\n"
                  "Một đêm mưa giông, thanh kiếm gỉ sét chôn sau núi bỗng phát sáng, mở ra con đường kiếm đạo nghịch thiên. "
                  "Từ đây, một kiếm phá vạn pháp, một người đứng trên đỉnh cao chín tầng trời."),
        dict(title="Thành Phố Không Ngủ", author="Hạ Lam", genres=["Đô thị", "Trinh thám"], status="completed", origin="original",
             desc="Sài Gòn, ba giờ sáng. Một nữ phóng viên trẻ nhận được tin nhắn từ số máy của người đã mất hai năm trước. "
                  "Cô lần theo từng manh mối qua những con hẻm không bao giờ tắt đèn."),
        dict(title="Trọng Sinh Làm Nông Gia Nữ", author="Thanh Trà", genres=["Điền văn", "Trọng sinh", "Ngôn tình"], status="ongoing",
             origin="translated", desc="Kiếp trước nàng bị người thân lừa gạt, chết trong cô độc. Mở mắt ra, nàng trở về năm mười lăm tuổi, "
             "trong căn nhà tranh dột nát. Lần này, nàng sẽ dùng đôi tay mình trồng ra cả một vùng ruộng tốt."),
        dict(title="Hệ Thống Đầu Bếp Vạn Giới", author="AI Video Dub", genres=["Hệ thống", "Hài hước"], status="ongoing", origin="ai",
             desc="Mở một quán phở nhỏ ở phố cổ, Minh bất ngờ được hệ thống chọn làm đầu bếp phục vụ thực khách đến từ vạn giới. "
                  "Hôm nay là tiên nữ, ngày mai là ma vương, ai cũng mê bát phở bò tái lăn."),
        dict(title="Mạt Thế: Ta Có Một Hòn Đảo", author="Mặc Vân", genres=["Mạt thế", "Khoa huyễn"], status="completed", origin="original",
             desc="Sương mù màu tím phủ kín địa cầu, người biến thành quái vật. Trần Huy tỉnh dậy trên một hòn đảo nhỏ "
                  "giữa biển, nơi duy nhất sương mù không thể chạm tới."),
        dict(title="Cung Khuyết Tàn Mộng", author="Diệp Ảnh", genres=["Cung đấu", "Lịch sử"], status="ongoing", origin="translated",
             desc="Một cung nữ thấp kém, một hoàng đế đa nghi, một hậu cung đầy rẫy âm mưu. Muốn sống, nàng phải học cách "
                  "cười khi lòng đang khóc."),
    ]
    openers = ["Gió sớm lùa qua khe cửa, mang theo mùi đất ẩm sau mưa.", "Tiếng chuông chùa xa xa vọng lại, trầm và dài.",
               "Ánh trăng bạc rải xuống mặt hồ phẳng lặng.", "Phố xá đã lên đèn, dòng người vẫn hối hả.",
               "Trời vừa hửng sáng, sương còn đọng trên lá.", "Căn phòng nhỏ chìm trong im lặng đến nghẹt thở."]
    middles = ["Hắn siết chặt nắm tay, ánh mắt kiên định chưa từng có.", "Nàng khẽ mỉm cười, nhưng trong mắt lại thoáng nét buồn.",
               "“Ngươi nghĩ ta sẽ bỏ cuộc sao?” giọng nói vang lên, lạnh lẽo như băng.",
               "Không ai biết rằng, chính khoảnh khắc ấy đã thay đổi tất cả.", "Một luồng sức mạnh ấm áp lan khắp cơ thể.",
               "Mọi người xung quanh đều sững sờ, không tin vào mắt mình.", "Cô hít một hơi thật sâu rồi bước lên phía trước.",
               "“Chuyện này… rốt cuộc là thế nào?” có người lắp bắp hỏi.", "Ký ức cũ ùa về như thác lũ, rõ ràng đến đau lòng.",
               "Bên ngoài, tiếng bước chân dồn dập mỗi lúc một gần.", "Hắn nhớ lời sư phụ dặn: đường dài mới biết ngựa hay.",
               "Mùi trà thơm nhẹ lan trong không khí, dịu dàng mà xa xăm."]
    closers = ["Đêm ấy, không ai ngủ được.", "Và câu chuyện, chỉ mới bắt đầu.", "Phía chân trời, một vì sao lặng lẽ sáng lên.",
               "Hắn biết, ngày mai sẽ khác.", "Nàng quay lưng bước đi, không ngoảnh lại."]
    chapter_names = ["Khởi đầu", "Gặp gỡ định mệnh", "Bí mật trong đêm", "Thử thách đầu tiên", "Lời hứa", "Bão tố kéo đến",
                     "Người lạ mặt", "Một bước lên trời", "Ân oán", "Trở về", "Ánh sáng cuối đường hầm", "Không thể quay đầu"]

    def chapter_text():
        paras = [rnd.choice(openers)]
        for _ in range(rnd.randint(14, 22)):
            paras.append(" ".join(rnd.sample(middles, rnd.randint(2, 4))))
        paras.append(rnd.choice(closers))
        return "\n\n".join(paras)

    t0 = now()
    with tx():
        for i, d in enumerate(stories):
            owner = author if i % 3 != 2 else admin
            s = create_story(owner["id"], d["title"], description=d["desc"], status=d["status"], origin=d["origin"],
                             author_name=d["author"], genre_ids=genre_ids_from_names(d["genres"]))
            update_story(s["id"], cover=placeholder_cover(d["title"], d["author"], s["id"], seed=i),
                         is_featured=1 if i < 4 else 0)
            n_ch = rnd.randint(8, 14) if d["status"] == "ongoing" else rnd.randint(12, 18)
            for n in range(1, n_ch + 1):
                pa = t0 - (n_ch - n) * 3600 * rnd.randint(3, 20) - i * 1800
                upsert_chapter(s["id"], n, chapter_names[(n - 1) % len(chapter_names)], chapter_text(), publish=True,
                               publish_at=pa, refresh=False)
            # một chương hẹn giờ và một bản nháp để xem khu tác giả
            if d["status"] == "ongoing":
                upsert_chapter(s["id"], n_ch + 1, "Chương hẹn giờ", chapter_text(), publish=True,
                               publish_at=t0 + 86400, refresh=False)
                upsert_chapter(s["id"], n_ch + 2, "Bản nháp", chapter_text(), publish=False, refresh=False)
            refresh_story_stats(s["id"])
            views = 0
            for day in range(0, 20):
                v = rnd.randint(5, 120) * (6 - i)
                views += v
                ex("INSERT INTO story_views_daily(story_id, day, views) VALUES (?,?,?)", (s["id"], day_offset(day), v))
            ex("UPDATE stories SET views=?, follows=?, created_at=? WHERE id=?",
               (views, rnd.randint(3, 400), t0 - (30 - i * 4) * 86400, s["id"]))
            for uid, stars in ((reader["id"], rnd.randint(4, 5)), (admin["id"], rnd.randint(3, 5))):
                ex("INSERT OR IGNORE INTO ratings(user_id, story_id, stars, created_at) VALUES (?,?,?,?)", (uid, s["id"], stars, t0))
            ex("UPDATE stories SET rating_sum=(SELECT SUM(stars) FROM ratings WHERE story_id=?), "
               "rating_count=(SELECT COUNT(*) FROM ratings WHERE story_id=?) WHERE id=?", (s["id"], s["id"], s["id"]))
            ex("INSERT INTO comments(story_id, user_id, body, created_at) VALUES (?,?,?,?)",
               (s["id"], reader["id"], rnd.choice(["Truyện hay quá, mong tác giả ra chương đều!", "Mở đầu cuốn thật sự.",
                                                    "Nhân vật chính rất có chiều sâu.", "Hóng chương mới!"]), t0 - 3600))
            if owner["id"] == author["id"]:
                ex("INSERT INTO donations(story_id, to_user_id, from_user_id, donor_name, amount, message, status, created_at, confirmed_at) "
                   "VALUES (?,?,?,?,?,?,'confirmed',?,?)", (s["id"], author["id"], reader["id"], "Bạn Đọc Chăm Chỉ",
                                                            rnd.choice([20000, 50000, 100000]), "Cố lên tác giả!", t0, t0))
        ex("INSERT OR IGNORE INTO follows(user_id, story_id, last_seen_n, created_at) SELECT ?, id, MAX(last_chapter_n-2, 0), ? "
           "FROM stories LIMIT 3", (reader["id"], t0))
    print("Đã tạo dữ liệu mẫu: 6 truyện.")
    print("Tài khoản CHỈ ĐỂ THỬ (đổi mật khẩu/xoá trước khi chạy thật):")
    print("  admin  / admin123   (quản trị)")
    print("  tacgia / tacgia123  (người đăng, có thông tin nhận ủng hộ mẫu)")
    print("  bandoc / bandoc123  (bạn đọc)")


if __name__ == "__main__":
    t = time.time()
    main()
    print(f"Xong trong {time.time() - t:.1f}s")
