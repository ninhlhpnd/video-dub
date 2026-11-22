import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

/// Các bước của từng chế độ — giống PIPELINES/steps_for trong engine/vdub/common.py.
const pipelines = {
  'dub': ['transcribe', 'speakers', 'translate', 'overlays', 'separate', 'tts', 'render'],
  'review': ['script', 'overlays', 'separate', 'tts', 'render'],
};

bool wantsSeparate(Map<String, dynamic> o) =>
    o['mode'] == 'review' ? o['review_remove_voice'] == true : o['keep_background'] != false;

List<String> stepsFor(Map<String, dynamic> o) {
  final mode = o['mode'] == 'review' ? 'review' : 'dub';
  return pipelines[mode]!
      .where((s) => s != 'separate' || wantsSeparate(o))
      .where((s) => s != 'speakers' || o['multi_voice'] == true)
      .where((s) => s != 'overlays' || o['fix_overlays'] == true)
      .toList();
}

/// Cách xử lý một dòng chữ Trung trên video (khớp action trong engine/vdub/overlays.py).
const overlayActions = {
  'translate': 'Dịch sang tiếng Việt',
  'remove': 'Xóa',
  'channel': 'Thay bằng tên kênh',
  'keep': 'Giữ nguyên',
};

const channelPositions = {
  'top-left': 'Góc trên trái',
  'top-right': 'Góc trên phải',
  'bottom-left': 'Góc dưới trái',
  'bottom-right': 'Góc dưới phải',
};

const stepNames = {
  'transcribe': 'Nhận dạng lời',
  'speakers': 'Phân biệt nhân vật',
  'translate': 'Dịch tiếng Việt',
  'script': 'AI xem phim & viết lời kể',
  'overlays': 'Xử lý chữ trên video',
  'separate': 'Xóa giọng gốc',
  'tts': 'Lồng tiếng',
  'render': 'Xuất video',
  'youtube': 'YouTube',
  'facebook': 'Facebook',
};

const reviewStyles = [
  'Tự chọn giọng văn hợp với nội dung video (bán hàng, hướng dẫn, du lịch, phim...)',
  'Giới thiệu sản phẩm hào hứng, nêu công dụng nổi bật, kết bằng lời kêu gọi mua',
  'Thuyết minh rõ ràng, dễ hiểu, như hướng dẫn từng bước',
  'Kể chuyện hấp dẫn, giọng văn review phim trên TikTok',
  'Hài hước, bình luận dí dỏm, hơi "cà khịa" nhân vật',
  'Kịch tính, hồi hộp, nhấn mạnh các nút thắt',
  'Nhẹ nhàng, cảm xúc, sâu lắng',
  'Ngắn gọn, đi thẳng vào nội dung chính',
];

/// Giọng đọc. "vieneu:..." chạy trên máy (miễn phí, không giới hạn);
/// còn lại là Edge TTS online, dạng "tên|cao độ|tốc độ".
const voices = {
  'vieneu:Trúc Ly': 'Trúc Ly · nữ Bắc, tự nhiên ⭐',
  'vieneu:Mai Anh': 'Mai Anh · nữ Bắc, rõ ràng',
  'vieneu:Đoan Trang': 'Đoan Trang · nữ Bắc, tự nhiên',
  'vieneu:Ngọc Huyền': 'Ngọc Huyền · nữ Bắc, nhẹ nhàng',
  'vieneu:Quỳnh Anh': 'Quỳnh Anh · nữ Bắc, đọc truyện',
  'vieneu:Ngọc Linh': 'Ngọc Linh · nữ Bắc, kể chuyện',
  'vieneu:Kim Thanh': 'Kim Thanh · nữ Nam, đọc truyện',
  'vieneu:Thùy Dung': 'Thùy Dung · nữ Nam, tin tức',
  'vieneu:Mỹ Duyên': 'Mỹ Duyên · nữ Nam, đọc truyện',
  'vieneu:Thục Đoan': 'Thục Đoan · nữ Nam, kể chuyện',
  'vieneu:Ngọc Trân': 'Ngọc Trân · nữ Trung',
  'vieneu:Hải Đăng': 'Hải Đăng · nam Bắc, tự nhiên',
  'vieneu:Adam bựa': 'Adam bựa · nam Bắc, hài hước',
  'vieneu:Quốc Tuấn': 'Quốc Tuấn · nam Bắc, tự nhiên',
  'vieneu:Thiện Minh': 'Thiện Minh · nam Bắc, kể chuyện',
  'vieneu:Minh Đức': 'Minh Đức · nam Bắc, tin tức',
  'vieneu:Adam': 'Adam · nam Nam, tự nhiên',
  'vieneu:Minh Triết': 'Minh Triết · nam Nam, tin tức',
  'vieneu:Thái Sơn': 'Thái Sơn · nam Nam, kể chuyện',
  'vieneu:Quang Sơn': 'Quang Sơn · nam Trung',
  'vi-VN-HoaiMyNeural|+25Hz|+10%': 'Hoài My nhí nhảnh · Edge (online)',
  'vi-VN-HoaiMyNeural': 'Hoài My · Edge (online)',
  'vi-VN-NamMinhNeural|+10Hz|+10%': 'Nam Minh vui · Edge (online)',
  'vi-VN-NamMinhNeural': 'Nam Minh · Edge (online)',
};

const sourceLangs = {
  'auto': 'Tự nhận biết',
  'en': 'Tiếng Anh',
  'zh': 'Tiếng Trung',
  'ja': 'Tiếng Nhật',
  'ko': 'Tiếng Hàn',
  'th': 'Tiếng Thái',
  'fr': 'Tiếng Pháp',
  'de': 'Tiếng Đức',
  'es': 'Tiếng Tây Ban Nha',
  'ru': 'Tiếng Nga',
  'id': 'Tiếng Indonesia',
};

String fmtTime(num sec, {bool ms = true}) {
  final t = sec.toDouble();
  final m = t ~/ 60;
  final s = t - m * 60;
  return ms ? '${m.toString().padLeft(2, '0')}:${s.toStringAsFixed(1).padLeft(4, '0')}'
      : '${m.toString().padLeft(2, '0')}:${s.floor().toString().padLeft(2, '0')}';
}

class Project {
  final String dir;
  Map<String, dynamic> meta;

  Project(this.dir, this.meta);

  static Project? tryLoad(String dir) {
    final f = File(p.join(dir, 'project.json'));
    if (!f.existsSync()) return null;
    try {
      return Project(dir, jsonDecode(f.readAsStringSync()) as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  static List<Project> listIn(String workspace) {
    final d = Directory(workspace);
    if (!d.existsSync()) return [];
    final list = d.listSync().whereType<Directory>().map((e) => tryLoad(e.path)).whereType<Project>().toList();
    list.sort((a, b) => b.created.compareTo(a.created));
    return list;
  }

  void reload() {
    final f = File(p.join(dir, 'project.json'));
    meta = jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
  }

  String get name => meta['name']?.toString() ?? p.basename(dir);
  String get created => meta['created']?.toString() ?? '';
  String get video => meta['video']?.toString() ?? '';
  /// Độ dài video đang làm việc (bản cắt ghép nếu đã tóm tắt phim).
  double get duration => ((meta['edited'] == true ? meta['edited_duration'] : null) ?? meta['duration'] as num?)
          ?.toDouble() ??
      0;
  Map<String, dynamic> get steps => (meta['steps'] as Map?)?.cast<String, dynamic>() ?? {};
  Map<String, dynamic> get options => (meta['options'] as Map?)?.cast<String, dynamic>() ?? {};
  bool done(String step) => steps[step] == 'done';
  bool get isReview => options['mode'] == 'review';
  List<String> get activeSteps => stepsFor(options);
  Map<String, dynamic> get speakers => (meta['speakers'] as Map?)?.cast<String, dynamic>() ?? {};

  void setSpeakerVoice(String key, String voice) {
    reload();
    ((meta['speakers'] as Map)[key] as Map)['voice'] = voice;
    _write();
  }
  String get thumb => p.join(dir, 'thumb.jpg');
  String get output => p.join(dir, 'output.mp4');
  bool get hasOutput => File(output).existsSync();
  List get uploads => (meta['uploads'] as List?) ?? [];

  /// Ghi option: đọc lại file trước để không đè trạng thái các bước engine vừa ghi.
  void setOption(String key, dynamic value) {
    reload();
    (meta['options'] as Map)[key] = value;
    _write();
  }

  void addUpload(Map<String, dynamic> u) {
    reload();
    meta['uploads'] = [...uploads, u];
    _write();
  }

  void _write() {
    File(p.join(dir, 'project.json')).writeAsStringSync(const JsonEncoder.withIndent('  ').convert(meta));
  }

  List<Map<String, dynamic>> loadOverlays() {
    final f = File(p.join(dir, 'overlays.json'));
    if (!f.existsSync()) return [];
    return (jsonDecode(f.readAsStringSync()) as List).cast<Map<String, dynamic>>();
  }

  void saveOverlays(List<Map<String, dynamic>> items) {
    File(p.join(dir, 'overlays.json')).writeAsStringSync(const JsonEncoder.withIndent(' ').convert(items));
  }

  List<Map<String, dynamic>> loadSegments() {
    final f = File(p.join(dir, 'segments.json'));
    if (!f.existsSync()) return [];
    return (jsonDecode(f.readAsStringSync()) as List).cast<Map<String, dynamic>>();
  }

  void saveSegments(List<Map<String, dynamic>> segs) {
    final f = File(p.join(dir, 'segments.json.tmp'));
    f.writeAsStringSync(const JsonEncoder.withIndent(' ').convert(segs));
    f.renameSync(p.join(dir, 'segments.json'));
  }
}
