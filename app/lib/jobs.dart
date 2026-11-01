import 'package:flutter/foundation.dart';

import 'engine.dart';
import 'main.dart';
import 'models.dart';

/// Một lượt chạy engine cho một project. Vẫn chạy tiếp khi người dùng rời màn hình project.
class Job {
  final String dir;
  final List<String> plan; // các bước sẽ chạy, để tính "bước x/y"
  final engine = Engine();
  final logs = <String>[];
  final DateTime startedAt = DateTime.now();
  DateTime stepStartedAt = DateTime.now();

  String? step;
  double pct = 0;
  String msg = 'Đang khởi động...';
  String? error;
  bool running = true;

  Job(this.dir, this.plan);

  int get stepIndex => step == null ? 0 : plan.indexOf(step!) + 1;

  /// % tổng của cả lượt chạy (mỗi bước tính bằng nhau).
  double get overall {
    if (plan.isEmpty) return pct / 100;
    final i = stepIndex == 0 ? 0 : stepIndex - 1;
    return ((i + pct / 100) / plan.length).clamp(0, 1);
  }

  Duration get elapsed => DateTime.now().difference(startedAt);

  /// Ước tính thời gian còn lại của bước hiện tại (null khi chưa đủ dữ liệu).
  Duration? get stepEta {
    final e = DateTime.now().difference(stepStartedAt);
    if (pct < 3 || e.inSeconds < 4) return null;
    return Duration(seconds: (e.inSeconds * (100 - pct) / pct).round());
  }

  void log(String m) {
    logs.add(m);
    if (logs.length > 500) logs.removeAt(0);
  }
}

String fmtDuration(Duration d) {
  final s = d.inSeconds;
  if (s < 60) return '$s giây';
  if (s < 3600) return '${s ~/ 60} phút ${(s % 60).toString().padLeft(2, '0')} giây';
  return '${s ~/ 3600} giờ ${((s % 3600) ~/ 60).toString().padLeft(2, '0')} phút';
}

class JobManager extends ChangeNotifier {
  final _jobs = <String, Job>{};

  Job? of(String dir) => _jobs[dir];
  bool isRunning(String dir) => _jobs[dir]?.running == true;
  int get runningCount => _jobs.values.where((j) => j.running).length;

  /// Chạy engine cho project; trả về khi xong. [plan] là danh sách bước dự kiến.
  Future<Job> start(Project pr, List<String> args, List<String> plan) async {
    final job = Job(pr.dir, plan);
    _jobs[pr.dir] = job;
    job.log('▶ ${args.join(' ')}');
    notifyListeners();
    await for (final ev in job.engine.run([...args, '--project', pr.dir], engineOverride: settings.get('engine_dir'))) {
      switch (ev.type) {
        case 'step':
          job.step = ev.step;
          job.pct = 0;
          job.stepStartedAt = DateTime.now();
          job.log('— ${stepNames[ev.step] ?? ev.step}');
        case 'progress':
          if (job.step != ev.step) {
            job.step = ev.step;
            job.stepStartedAt = DateTime.now();
          }
          job.pct = ev.pct;
          if (ev.msg.isNotEmpty) job.msg = ev.msg;
        case 'log':
        case 'raw':
          job.log(ev.msg);
          job.msg = ev.msg;
        case 'error':
          job.error = ev.msg;
          job.log('✖ ${ev.msg}');
          if ((ev.data['trace'] ?? '').toString().isNotEmpty) job.log(ev.data['trace'].toString());
        case 'done':
          job.log('✔ Hoàn tất sau ${fmtDuration(job.elapsed)}');
      }
      notifyListeners();
    }
    job.running = false;
    notifyListeners();
    return job;
  }

  Future<void> stop(String dir) async => _jobs[dir]?.engine.stop();
}

final jobs = JobManager();
