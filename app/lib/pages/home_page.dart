import 'dart:async';
import 'dart:io';

import 'package:desktop_drop/desktop_drop.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

import '../engine.dart';
import '../jobs.dart';
import '../main.dart';
import '../models.dart';
import 'project_page.dart';
import 'settings_page.dart';

const videoExts = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.m4v', '.wmv', '.ts'};

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  List<Project> projects = [];
  bool dragging = false;
  String? busy;

  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    _refresh();
    jobs.addListener(_onJobs);
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (jobs.runningCount > 0 && mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    jobs.removeListener(_onJobs);
    _ticker?.cancel();
    super.dispose();
  }

  void _onJobs() {
    if (mounted) _refresh();
  }

  void _refresh() => setState(() => projects = Project.listIn(settings.workspace));

  Future<void> _addVideos(List<String> paths) async {
    paths = paths.where((f) => videoExts.contains(p.extension(f).toLowerCase())).toList();
    if (paths.isEmpty) {
      _snack('Không có file video hợp lệ');
      return;
    }
    Project? last;
    for (final (i, f) in paths.indexed) {
      setState(() => busy = 'Đang đọc thông tin video ${paths.length > 1 ? '${i + 1}/${paths.length}' : ''}: '
          '${p.basename(f)}');
      final ev = await Engine.once(['new', '--video', f, '--workspace', settings.workspace],
          engineOverride: settings.get('engine_dir'));
      if (ev.type == 'error') {
        _snack('Lỗi ${p.basename(f)}: ${ev.msg}');
      } else {
        last = Project.tryLoad(ev.data['project']);
      }
    }
    setState(() => busy = null);
    _refresh();
    if (last != null && paths.length == 1) _open(last);
  }

  Future<void> _pick() async {
    final files = await FilePicker.pickFiles(type: FileType.video, dialogTitle: 'Chọn video cần lồng tiếng');
    await _addVideos(files.map((e) => e.path).whereType<String>().toList());
  }

  Future<void> _open(Project pr) async {
    await Navigator.push(context, MaterialPageRoute(builder: (_) => ProjectPage(project: pr)));
    _refresh();
  }

  Future<void> _delete(Project pr) async {
    if (jobs.isRunning(pr.dir)) {
      _snack('Video này đang được xử lý, hãy dừng trước khi xóa');
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Xóa project?'),
        content: Text('Xóa "${pr.name}" và toàn bộ file đã xử lý (video gốc không bị xóa).'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Xóa')),
        ],
      ),
    );
    if (ok == true) {
      await Directory(pr.dir).delete(recursive: true);
      _refresh();
    }
  }

  void _snack(String m) => ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));

  @override
  Widget build(BuildContext context) {
    final noKey = settings.get('gemini_api_key').isEmpty;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Video Dub · Lồng tiếng Việt tự động'),
        actions: [
          if (jobs.runningCount > 0)
            Padding(
              padding: const EdgeInsets.only(right: 12),
              child: Chip(
                avatar: const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                label: Text('${jobs.runningCount} video đang xử lý'),
              ),
            ),
          IconButton(tooltip: 'Làm mới', onPressed: _refresh, icon: const Icon(Icons.refresh)),
          IconButton(
            tooltip: 'Cài đặt',
            icon: const Icon(Icons.settings),
            onPressed: () async {
              await Navigator.push(context, MaterialPageRoute(builder: (_) => const SettingsPage()));
              _refresh();
            },
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: DropTarget(
        onDragEntered: (_) => setState(() => dragging = true),
        onDragExited: (_) => setState(() => dragging = false),
        onDragDone: (d) {
          setState(() => dragging = false);
          _addVideos(d.files.map((e) => e.path).toList());
        },
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (noKey)
                Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: MaterialBanner(
                    content: const Text('Chưa nhập Gemini API key — cần để dịch phụ đề sang tiếng Việt.'),
                    leading: const Icon(Icons.key, color: Colors.amber),
                    actions: [
                      TextButton(
                        onPressed: () async {
                          await Navigator.push(context, MaterialPageRoute(builder: (_) => const SettingsPage()));
                          setState(() {});
                        },
                        child: const Text('Mở Cài đặt'),
                      ),
                    ],
                  ),
                ),
              _dropZone(),
              const SizedBox(height: 20),
              Text('Project (${projects.length})', style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 10),
              Expanded(
                child: projects.isEmpty
                    ? const Center(child: Text('Chưa có video nào', style: TextStyle(color: Colors.white38)))
                    : GridView.builder(
                        gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                            maxCrossAxisExtent: 300, mainAxisSpacing: 14, crossAxisSpacing: 14, childAspectRatio: 1.05),
                        itemCount: projects.length,
                        itemBuilder: (_, i) => _card(projects[i]),
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _dropZone() {
    final c = Theme.of(context).colorScheme;
    return AnimatedContainer(
      duration: const Duration(milliseconds: 150),
      height: 130,
      decoration: BoxDecoration(
        color: dragging ? c.primary.withValues(alpha: 0.15) : const Color(0xFF1F1F29),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: dragging ? c.primary : Colors.white24, width: dragging ? 2 : 1),
      ),
      child: busy != null
          ? Center(
              child: Row(mainAxisSize: MainAxisSize.min, children: [
                const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5)),
                const SizedBox(width: 14),
                Text(busy!),
              ]),
            )
          : Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.movie_filter_outlined, size: 44, color: c.primary),
                const SizedBox(width: 18),
                Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Kéo thả video vào đây', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
                    const SizedBox(height: 4),
                    Text('mp4, mov, mkv, webm… — có thể thả nhiều video cùng lúc',
                        style: TextStyle(color: Colors.white.withValues(alpha: 0.5))),
                  ],
                ),
                const SizedBox(width: 30),
                FilledButton.icon(onPressed: _pick, icon: const Icon(Icons.add), label: const Text('Chọn video')),
              ],
            ),
    );
  }

  Widget _card(Project pr) {
    final doneCount = pipelineSteps.where(pr.done).length;
    final job = jobs.of(pr.dir);
    final thumb = File(pr.thumb);
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () => _open(pr),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Expanded(
              child: Stack(fit: StackFit.expand, children: [
                thumb.existsSync()
                    ? Image.file(thumb, fit: BoxFit.cover)
                    : Container(color: Colors.black26, child: const Icon(Icons.movie, size: 40)),
                Positioned(
                  right: 6,
                  bottom: 6,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(color: Colors.black87, borderRadius: BorderRadius.circular(4)),
                    child: Text(fmtTime(pr.duration, ms: false), style: const TextStyle(fontSize: 12)),
                  ),
                ),
                Positioned(
                  right: 0,
                  top: 0,
                  child: PopupMenuButton<String>(
                    icon: const Icon(Icons.more_vert, shadows: [Shadow(blurRadius: 6)]),
                    onSelected: (v) {
                      if (v == 'del') _delete(pr);
                      if (v == 'folder') Process.run('explorer', [pr.dir]);
                    },
                    itemBuilder: (_) => const [
                      PopupMenuItem(value: 'folder', child: Text('Mở thư mục')),
                      PopupMenuItem(value: 'del', child: Text('Xóa project')),
                    ],
                  ),
                ),
              ]),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(pr.name, maxLines: 1, overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 8),
                if (job != null && job.running) ...[
                  Text(
                    job.step == null
                        ? 'Đang khởi động...'
                        : 'Bước ${job.stepIndex}/${job.plan.length}: ${stepNames[job.step]} · ${job.pct.toStringAsFixed(0)}%',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.primary),
                  ),
                  const SizedBox(height: 4),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(value: job.overall, minHeight: 6),
                  ),
                ] else
                Row(children: [
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(4),
                      child: LinearProgressIndicator(value: doneCount / pipelineSteps.length, minHeight: 6),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(pr.hasOutput ? 'Đã xuất' : '$doneCount/${pipelineSteps.length}',
                      style: TextStyle(fontSize: 12, color: pr.hasOutput ? Colors.greenAccent : Colors.white54)),
                  if (pr.uploads.isNotEmpty) ...[
                    const SizedBox(width: 6),
                    const Icon(Icons.cloud_done, size: 16, color: Colors.lightBlueAccent),
                  ],
                ]),
              ]),
            ),
          ],
        ),
      ),
    );
  }
}
