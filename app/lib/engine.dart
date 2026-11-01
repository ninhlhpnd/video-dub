import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;

/// Cài đặt chung, lưu ở %APPDATA%\VideoDub\settings.json (engine Python đọc cùng file này).
class AppSettings {
  static final String dir = p.join(Platform.environment['APPDATA'] ?? '.', 'VideoDub');
  static final String file = p.join(dir, 'settings.json');

  Map<String, dynamic> data = {};

  String get workspace =>
      (data['workspace'] as String?)?.trim().isNotEmpty == true
          ? data['workspace']
          : p.join(Platform.environment['USERPROFILE'] ?? '.', 'Videos', 'VideoDub');

  String get(String k, [String def = '']) => (data[k] ?? def).toString();

  Future<void> load() async {
    final f = File(file);
    if (await f.exists()) {
      data = jsonDecode(await f.readAsString()) as Map<String, dynamic>;
    }
  }

  Future<void> save() async {
    await Directory(dir).create(recursive: true);
    await File(file).writeAsString(const JsonEncoder.withIndent('  ').convert(data));
  }
}

class EngineEvent {
  final String type; // progress | log | step | done | error | raw
  final Map<String, dynamic> data;
  EngineEvent(this.type, this.data);

  String get step => data['step']?.toString() ?? '';
  double get pct => (data['pct'] as num?)?.toDouble() ?? 0;
  String get msg => data['msg']?.toString() ?? '';
}

/// Gọi engine Python (dub.py) và đọc tiến độ từ stdout (mỗi dòng 1 JSON).
class Engine {
  static String? _engineDir;
  Process? _proc;
  bool _stopped = false;

  bool get running => _proc != null;

  /// Tìm thư mục engine: đi ngược từ file exe lên tới khi gặp engine/dub.py.
  static String? findEngineDir([String? override]) {
    if (override != null && override.isNotEmpty && File(p.join(override, 'dub.py')).existsSync()) {
      return override;
    }
    if (_engineDir != null) return _engineDir;
    var dir = p.dirname(Platform.resolvedExecutable);
    for (var i = 0; i < 10; i++) {
      for (final c in [p.join(dir, 'engine'), p.join(dir, 'video_dub', 'engine')]) {
        if (File(p.join(c, 'dub.py')).existsSync()) return _engineDir = c;
      }
      final parent = p.dirname(dir);
      if (parent == dir) break;
      dir = parent;
    }
    return null;
  }

  static String _python(String engineDir) {
    final w = p.join(engineDir, '.venv', 'Scripts', 'pythonw.exe');
    return File(w).existsSync() ? w : p.join(engineDir, '.venv', 'Scripts', 'python.exe');
  }

  Stream<EngineEvent> run(List<String> args, {String? engineOverride}) {
    final ctrl = StreamController<EngineEvent>();
    final dir = findEngineDir(engineOverride);
    if (dir == null) {
      ctrl.add(EngineEvent('error', {'msg': 'Không tìm thấy thư mục engine (dub.py). Hãy chỉ đường dẫn trong Cài đặt.'}));
      ctrl.close();
      return ctrl.stream;
    }
    () async {
      try {
        final proc = await Process.start(
          _python(dir),
          ['dub.py', ...args, '--settings', AppSettings.file],
          workingDirectory: dir,
          environment: {'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'},
        );
        _proc = proc;
        var gotEnd = false;
        final errTail = <String>[];
        // forEach trả về Future hoàn tất khi luồng đóng, kể cả khi luồng đóng rất sớm
        // (asFuture() gọi sau khi luồng đã đóng sẽ treo mãi → app xoay vô hạn).
        final out = proc.stdout.transform(utf8.decoder).transform(const LineSplitter()).forEach((line) {
          final t = line.trim();
          if (t.isEmpty) return;
          try {
            final m = jsonDecode(t) as Map<String, dynamic>;
            final ev = EngineEvent(m['type']?.toString() ?? 'raw', m);
            if (ev.type == 'done' || ev.type == 'error') gotEnd = true;
            ctrl.add(ev);
          } catch (_) {
            ctrl.add(EngineEvent('raw', {'msg': t}));
          }
        });
        final err = proc.stderr.transform(utf8.decoder).transform(const LineSplitter()).forEach((line) {
          errTail.add(line);
          if (errTail.length > 30) errTail.removeAt(0);
        });
        final code = await proc.exitCode;
        await out.catchError((_) {});
        await err.catchError((_) {});
        if (!gotEnd) {
          ctrl.add(EngineEvent('error', {
            'msg': _stopped ? 'Đã dừng' : 'Engine thoát bất thường (mã $code)',
            'trace': errTail.join('\n'),
          }));
        }
      } catch (e) {
        ctrl.add(EngineEvent('error', {'msg': 'Không chạy được engine: $e'}));
      } finally {
        _proc = null;
        await ctrl.close();
      }
    }();
    return ctrl.stream;
  }

  /// Dừng engine và cả ffmpeg con của nó.
  Future<void> stop() async {
    final proc = _proc;
    if (proc == null) return;
    _stopped = true;
    await Process.run('taskkill', ['/T', '/F', '/PID', '${proc.pid}']);
  }

  /// Chạy một lệnh ngắn và chỉ lấy sự kiện kết thúc; quá [timeout] thì dừng và báo lỗi.
  static Future<EngineEvent> once(List<String> args,
      {String? engineOverride, Duration timeout = const Duration(seconds: 60)}) async {
    final eng = Engine();
    EngineEvent last = EngineEvent('error', {'msg': 'Engine không phản hồi'});
    final timer = Timer(timeout, () {
      last = EngineEvent('error', {'msg': 'Engine không phản hồi sau ${timeout.inSeconds} giây'});
      eng.stop();
    });
    await for (final e in eng.run(args, engineOverride: engineOverride)) {
      if (e.type == 'done' || (e.type == 'error' && timer.isActive)) last = e;
    }
    timer.cancel();
    return last;
  }
}
