import 'dart:async';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

import '../engine.dart';
import '../jobs.dart';
import '../main.dart';
import '../models.dart';
import 'upload_dialog.dart';

const charsPerSec = 15; // giống engine/translate.py

class ProjectPage extends StatefulWidget {
  final Project project;
  const ProjectPage({super.key, required this.project});

  @override
  State<ProjectPage> createState() => _ProjectPageState();
}

class _ProjectPageState extends State<ProjectPage> {
  late final Project pr = widget.project;
  List<Map<String, dynamic>> segs = [];
  final ctrls = <int, TextEditingController>{};
  final logScroll = ScrollController();
  Timer? _saveTimer;
  Timer? _ticker; // cập nhật đồng hồ "đã chạy / còn lại" mỗi giây
  bool dirty = false;
  bool onlyOver = false;

  Job? get job => jobs.of(pr.dir);
  bool get running => jobs.isRunning(pr.dir);
  String? _lastStep;
  bool _wasRunning = false;
  int _lastLogCount = 0;

  @override
  void initState() {
    super.initState();
    _loadSegs();
    _wasRunning = running;
    _lastStep = job?.step;
    jobs.addListener(_onJobs);
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (running && mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    jobs.removeListener(_onJobs);
    _ticker?.cancel();
    _saveTimer?.cancel();
    if (dirty) _saveEdits();
    for (final c in ctrls.values) {
      c.dispose();
    }
    logScroll.dispose();
    super.dispose();
  }

  /// Engine vừa sang bước mới hoặc vừa xong → đọc lại phụ đề/trạng thái từ đĩa.
  void _onJobs() {
    if (!mounted) return;
    final j = job;
    if (j != null && (j.step != _lastStep || j.running != _wasRunning)) {
      _lastStep = j.step;
      pr.reload();
      _loadSegs();
      if (_wasRunning && !j.running && j.error == null && pr.hasOutput) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('Đã xuất video xong sau ${fmtDuration(j.elapsed)}!'),
          action: SnackBarAction(label: 'Xem', onPressed: () => Process.run('explorer', [pr.output])),
        ));
      }
      _wasRunning = j.running;
    }
    if (j != null && j.logs.length != _lastLogCount) {
      _lastLogCount = j.logs.length;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (logScroll.hasClients) logScroll.jumpTo(logScroll.position.maxScrollExtent);
      });
    }
    setState(() {});
  }

  void _loadSegs() {
    segs = pr.loadSegments();
    for (final s in segs) {
      final id = s['id'] as int;
      final vi = s['vi']?.toString() ?? '';
      final c = ctrls.putIfAbsent(id, () => TextEditingController());
      if (c.text != vi) c.text = vi;
    }
  }

  void _onEdit(Map<String, dynamic> s, String v) {
    s['vi'] = v;
    dirty = true;
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 700), _saveEdits);
    setState(() {});
  }

  void _saveEdits() {
    if (!dirty || running) return;
    pr.saveSegments(segs);
    dirty = false;
  }

  Future<void> _run(List<String> args) async {
    if (running) return;
    _saveTimer?.cancel();
    _saveEdits();
    final from = args.contains('--from') ? args[args.indexOf('--from') + 1] : args.first;
    final plan = args.first == 'run' ? _activeSteps.skipWhile((s) => s != from).toList() : [args.first];
    _wasRunning = true;
    await jobs.start(pr, args, plan);
  }

  List<String> get _activeSteps =>
      pipelineSteps.where((s) => s != 'separate' || pr.options['keep_background'] != false).toList();

  String? get _firstPending => _activeSteps.cast<String?>().firstWhere((s) => !pr.done(s!), orElse: () => null);

  Future<void> _rerunFrom(String step) async {
    var args = ['run', '--from', step];
    if (step == 'translate' && segs.any((s) => (s['vi'] ?? '').toString().isNotEmpty)) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Dịch lại toàn bộ?'),
          content: const Text('Bản dịch hiện tại (kể cả phần bạn đã sửa tay) sẽ bị thay thế.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Hủy')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Dịch lại')),
          ],
        ),
      );
      if (ok != true) return;
      args = [...args, '--force'];
    }
    await _run(args);
  }

  void _opt(String k, dynamic v) {
    pr.setOption(k, v);
    setState(() {});
  }

  double _slot(int i) {
    final s = segs[i];
    final next = i + 1 < segs.length ? (segs[i + 1]['start'] as num).toDouble() : pr.duration;
    final end = (s['end'] as num).toDouble() + 0.8;
    return (next < end ? next : end) - (s['start'] as num).toDouble();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(pr.name, overflow: TextOverflow.ellipsis),
        actions: [
          TextButton.icon(
            onPressed: () => Process.run('explorer', [pr.video]),
            icon: const Icon(Icons.play_circle_outline),
            label: const Text('Video gốc'),
          ),
          TextButton.icon(
            onPressed: pr.hasOutput ? () => Process.run('explorer', [pr.output]) : null,
            icon: const Icon(Icons.smart_display),
            label: const Text('Xem kết quả'),
          ),
          IconButton(
            tooltip: 'Mở thư mục project',
            onPressed: () => Process.run('explorer', [pr.dir]),
            icon: const Icon(Icons.folder_open),
          ),
          const SizedBox(width: 6),
          FilledButton.icon(
            onPressed: pr.hasOutput && !running
                ? () async {
                    await showDialog(context: context, builder: (_) => UploadDialog(project: pr));
                    setState(() => pr.reload());
                  }
                : null,
            icon: const Icon(Icons.cloud_upload),
            label: const Text('Đăng / Hẹn giờ'),
          ),
          const SizedBox(width: 12),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(
              width: 360,
              child: ListView(children: [_pipelineCard(), const SizedBox(height: 14), _optionsCard()]),
            ),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [Expanded(child: _segmentsCard()), const SizedBox(height: 10), _logPanel()],
              ),
            ),
          ],
        ),
      ),
    );
  }

  // ---------------- Quy trình ----------------

  Widget _pipelineCard() {
    final first = _firstPending;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('Quy trình', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          for (final s in _activeSteps) _stepTile(s),
          const SizedBox(height: 10),
          if (running) ..._progressBlock(job!) else
            FilledButton.icon(
              style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16)),
              onPressed: first == null ? null : () => _run(['run', '--from', first]),
              icon: Icon(first == null ? Icons.check : Icons.auto_awesome),
              label: Text(first == null
                  ? 'Đã hoàn tất'
                  : first == 'transcribe'
                      ? 'Bắt đầu tự động'
                      : 'Chạy tiếp từ "${stepNames[first]}"'),
            ),
          if (!running && job?.error != null) ...[
            const SizedBox(height: 10),
            SelectableText(job!.error!, style: const TextStyle(color: Colors.redAccent)),
          ],
        ]),
      ),
    );
  }

  List<Widget> _progressBlock(Job j) {
    final eta = j.stepEta;
    const dim = TextStyle(fontSize: 12, color: Colors.white60);
    return [
      Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(10),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            Expanded(
              child: Text(
                j.step == null
                    ? 'Đang khởi động engine...'
                    : 'Bước ${j.stepIndex}/${j.plan.length}: ${stepNames[j.step] ?? j.step}',
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            Text('${j.pct.toStringAsFixed(0)}%', style: const TextStyle(fontWeight: FontWeight.w600)),
          ]),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(value: j.pct > 0 ? j.pct / 100 : null, minHeight: 8),
          ),
          const SizedBox(height: 8),
          Text(j.msg, maxLines: 2, overflow: TextOverflow.ellipsis, style: dim),
          const Divider(height: 18),
          Row(children: [
            const Text('Tổng tiến độ', style: dim),
            const Spacer(),
            Text('${(j.overall * 100).toStringAsFixed(0)}%', style: dim),
          ]),
          const SizedBox(height: 4),
          LinearProgressIndicator(value: j.overall, minHeight: 3),
          const SizedBox(height: 8),
          Row(children: [
            const Icon(Icons.timer_outlined, size: 14, color: Colors.white60),
            const SizedBox(width: 4),
            Text('Đã chạy ${fmtDuration(j.elapsed)}', style: dim),
            const Spacer(),
            if (eta != null) Text('Bước này còn ~${fmtDuration(eta)}', style: dim),
          ]),
        ]),
      ),
      const SizedBox(height: 10),
      OutlinedButton.icon(onPressed: () => jobs.stop(pr.dir), icon: const Icon(Icons.stop), label: const Text('Dừng')),
    ];
  }

  Widget _stepTile(String s) {
    final j = job;
    final isRunning = running && j?.step == s;
    final queued = running && j != null && j.plan.contains(s) && j.plan.indexOf(s) >= j.stepIndex;
    final done = pr.done(s) && !queued && !isRunning;
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: isRunning
          ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
          : Icon(
              done ? Icons.check_circle : (queued ? Icons.schedule : Icons.radio_button_unchecked),
              color: done ? Colors.greenAccent : (queued ? Colors.white60 : Colors.white38)),
      title: Text(stepNames[s]!, style: TextStyle(fontWeight: isRunning ? FontWeight.w600 : null)),
      subtitle: isRunning
          ? Text('${j!.pct.toStringAsFixed(0)}%', style: const TextStyle(fontSize: 11))
          : queued
              ? const Text('Đang chờ', style: TextStyle(fontSize: 11))
              : null,
      trailing: IconButton(
        tooltip: 'Chạy lại từ bước này',
        icon: const Icon(Icons.replay, size: 20),
        onPressed: running ? null : () => _rerunFrom(s),
      ),
    );
  }

  // ---------------- Tùy chọn ----------------

  Widget _optionsCard() {
    final o = pr.options;
    final music = (o['music'] ?? '').toString();
    double val(String k, double d) => (o[k] as num?)?.toDouble() ?? d;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('Tùy chọn', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 14),
          DropdownButtonFormField<String>(
            initialValue: sourceLangs.containsKey(o['source_lang']) ? o['source_lang'] : 'auto',
            decoration: const InputDecoration(labelText: 'Ngôn ngữ gốc'),
            items: sourceLangs.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
            onChanged: running ? null : (v) => _opt('source_lang', v),
          ),
          const SizedBox(height: 12),
          TextFormField(
            initialValue: (o['translate_note'] ?? '').toString(),
            decoration: const InputDecoration(
              labelText: 'Ghi chú khi dịch (tùy chọn)',
              hintText: 'VD: video nấu ăn, xưng "mình - các bạn", giữ nguyên tên riêng',
            ),
            maxLines: 2,
            onChanged: (v) => pr.setOption('translate_note', v),
          ),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: DropdownButtonFormField<String>(
                initialValue: voices.containsKey(o['voice']) ? o['voice'] : voices.keys.first,
                isExpanded: true,
                menuMaxHeight: 420,
                decoration: const InputDecoration(labelText: 'Giọng đọc'),
                items: voices.entries
                    .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value, overflow: TextOverflow.ellipsis)))
                    .toList(),
                onChanged: running ? null : (v) => _opt('voice', v),
              ),
            ),
            const SizedBox(width: 6),
            previewing
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                  )
                : IconButton(
                    tooltip: 'Nghe thử giọng này',
                    icon: const Icon(Icons.play_circle_fill),
                    onPressed: () => _preview((o['voice'] ?? voices.keys.first).toString()),
                  ),
          ]),
          if ((o['voice'] ?? '').toString().startsWith('vieneu:'))
            const Padding(
              padding: EdgeInsets.only(top: 6),
              child: Text('Giọng VieNeu chạy trên máy: miễn phí, không giới hạn, lần đầu nạp mất ~30 giây.',
                  style: TextStyle(fontSize: 12, color: Colors.white38)),
            ),
          _slider('Tốc độ đọc tối đa khi câu dài', val('max_speed', 1.35), 1.0, 1.6, 'max_speed',
              (v) => '${v.toStringAsFixed(2)}x'),
          _slider('Âm lượng giọng Việt', val('voice_volume', 1.0), 0.3, 2.0, 'voice_volume', _pct),
          const Divider(height: 24),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Giữ tiếng động/nhạc gốc'),
            subtitle: const Text('Chỉ xóa giọng nói gốc', style: TextStyle(fontSize: 12)),
            value: o['keep_background'] != false,
            onChanged: running ? null : (v) => _opt('keep_background', v),
          ),
          if (o['keep_background'] != false)
            _slider('Âm lượng nền gốc', val('bg_volume', 1.0), 0, 1.5, 'bg_volume', _pct),
          const Divider(height: 24),
          Row(children: [
            const Icon(Icons.music_note, size: 20),
            const SizedBox(width: 8),
            Expanded(
              child: Text(music.isEmpty ? 'Chưa chọn nhạc nền' : p.basename(music),
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: music.isEmpty ? Colors.white38 : null)),
            ),
            if (music.isNotEmpty)
              IconButton(icon: const Icon(Icons.close, size: 18), onPressed: running ? null : () => _opt('music', '')),
            TextButton(
              onPressed: running
                  ? null
                  : () async {
                      final f = await FilePicker.pickFile(type: FileType.audio, dialogTitle: 'Chọn nhạc nền');
                      if (f?.path != null) _opt('music', f!.path);
                    },
              child: const Text('Chọn'),
            ),
          ]),
          if (music.isNotEmpty)
            _slider('Âm lượng nhạc nền (tự nhỏ lại khi có lời)', val('music_volume', 0.15), 0, 0.6, 'music_volume', _pct),
          const Divider(height: 24),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Chèn phụ đề tiếng Việt vào video'),
            value: o['burn_subs'] != false,
            onChanged: running ? null : (v) => _opt('burn_subs', v),
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Che phụ đề gốc in trên video'),
            subtitle: const Text('Phủ dải màu lên chữ gốc, phụ đề Việt nằm trên dải', style: TextStyle(fontSize: 12)),
            value: o['cover_subs'] == true,
            onChanged: running || detecting
                ? null
                : (v) {
                    _opt('cover_subs', v);
                    if (v && val('cover_bottom', 0) <= val('cover_top', 0)) _detectCover();
                  },
          ),
          if (o['cover_subs'] == true) _coverEditor(),
          const SizedBox(height: 4),
          const Text('Đổi tùy chọn giọng/âm lượng/nhạc xong, bấm "Lồng tiếng lại & xuất" để áp dụng.',
              style: TextStyle(fontSize: 12, color: Colors.white38)),
        ]),
      ),
    );
  }

  String _pct(double v) => '${(v * 100).round()}%';

  bool previewing = false;

  Future<void> _preview(String voice) async {
    setState(() => previewing = true);
    final ev = await Engine.once(['preview', '--voice', voice],
        engineOverride: settings.get('engine_dir'), timeout: const Duration(seconds: 150));
    if (!mounted) return;
    setState(() => previewing = false);
    if (ev.type == 'done') {
      Process.run('explorer', [ev.data['file'].toString()]);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Không nghe thử được: ${ev.msg}')));
    }
  }

  // ---------------- Che phụ đề gốc ----------------

  static const coverColors = {
    'FFD400': ('Vàng', '000000'),
    'FFFFFF': ('Trắng', '000000'),
    '000000': ('Đen', 'FFFFFF'),
    'E53935': ('Đỏ', 'FFFFFF'),
  };
  bool detecting = false;

  Future<void> _detectCover() async {
    setState(() => detecting = true);
    final ev = await Engine.once(['cover', '--project', pr.dir], engineOverride: settings.get('engine_dir'));
    pr.reload();
    if (!mounted) return;
    setState(() => detecting = false);
    final m = ev.type == 'error'
        ? 'Lỗi dò phụ đề: ${ev.msg}'
        : ev.data['found'] == true
            ? 'Đã tìm thấy vị trí phụ đề gốc'
            : 'Không chắc vị trí phụ đề gốc, hãy kéo chỉnh vùng che';
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(m)));
  }

  Color _hex(String h) => Color(int.parse('FF${h.padLeft(6, '0')}', radix: 16));

  Widget _coverEditor() {
    final o = pr.options;
    final top = (o['cover_top'] as num?)?.toDouble() ?? 0;
    final bottom = (o['cover_bottom'] as num?)?.toDouble() ?? 0;
    final color = (o['cover_color'] ?? 'FFD400').toString();
    final textColor = (o['cover_text_color'] ?? '000000').toString();
    final preview = File(p.join(pr.dir, 'cover_preview.jpg'));
    final img = preview.existsSync() ? preview : File(pr.thumb);
    final w = (pr.meta['width'] as num?)?.toDouble() ?? 16;
    final h = (pr.meta['height'] as num?)?.toDouble() ?? 9;

    if (detecting) {
      return const Padding(
        padding: EdgeInsets.all(16),
        child: Row(children: [
          SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
          SizedBox(width: 12),
          Text('Đang dò vị trí phụ đề gốc...'),
        ]),
      );
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      const SizedBox(height: 6),
      Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 300),
          child: AspectRatio(
            aspectRatio: w / h,
            child: LayoutBuilder(builder: (_, c) {
              return ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: Stack(fit: StackFit.expand, children: [
                  if (img.existsSync())
                    Image.memory(img.readAsBytesSync(), fit: BoxFit.fill, gaplessPlayback: true)
                  else
                    Container(color: Colors.black),
                  if (bottom > top)
                    Positioned(
                      left: 0,
                      right: 0,
                      top: top * c.maxHeight,
                      height: (bottom - top) * c.maxHeight,
                      child: Container(
                        color: _hex(color),
                        alignment: Alignment.center,
                        child: FittedBox(
                          child: Padding(
                            padding: const EdgeInsets.symmetric(horizontal: 4),
                            child: Text('Phụ đề tiếng Việt',
                                style: TextStyle(color: _hex(textColor), fontWeight: FontWeight.bold)),
                          ),
                        ),
                      ),
                    ),
                ]),
              );
            }),
          ),
        ),
      ),
      const SizedBox(height: 8),
      const Text('Vị trí dải che (kéo 2 đầu)', style: TextStyle(fontSize: 13)),
      RangeSlider(
        values: RangeValues(top.clamp(0, 1), bottom.clamp(top.clamp(0, 1), 1)),
        divisions: 200,
        labels: RangeLabels('${(top * 100).round()}%', '${(bottom * 100).round()}%'),
        onChanged: running
            ? null
            : (r) => setState(() {
                  o['cover_top'] = double.parse(r.start.toStringAsFixed(4));
                  o['cover_bottom'] = double.parse(r.end.toStringAsFixed(4));
                }),
        onChangeEnd: running
            ? null
            : (r) {
                pr.setOption('cover_top', double.parse(r.start.toStringAsFixed(4)));
                _opt('cover_bottom', double.parse(r.end.toStringAsFixed(4)));
              },
      ),
      Row(children: [
        for (final e in coverColors.entries)
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: Tooltip(
              message: e.value.$1,
              child: InkWell(
                customBorder: const CircleBorder(),
                onTap: running
                    ? null
                    : () {
                        pr.setOption('cover_text_color', e.value.$2);
                        _opt('cover_color', e.key);
                      },
                child: Container(
                  width: 26,
                  height: 26,
                  decoration: BoxDecoration(
                    color: _hex(e.key),
                    shape: BoxShape.circle,
                    border: Border.all(
                        color: color == e.key ? Theme.of(context).colorScheme.primary : Colors.white30,
                        width: color == e.key ? 3 : 1),
                  ),
                ),
              ),
            ),
          ),
        const Spacer(),
        TextButton.icon(
          onPressed: running ? null : _detectCover,
          icon: const Icon(Icons.center_focus_strong, size: 18),
          label: const Text('Tự dò lại'),
        ),
      ]),
    ]);
  }

  Widget _slider(String label, double value, double min, double max, String key, String Function(double) fmt) {
    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: Text(label, style: const TextStyle(fontSize: 13))),
          Text(fmt(value), style: const TextStyle(fontSize: 13, color: Colors.white70)),
        ]),
        Slider(
          value: value.clamp(min, max),
          min: min,
          max: max,
          divisions: ((max - min) * 20).round(),
          onChanged: running ? null : (v) => setState(() => (pr.options)[key] = double.parse(v.toStringAsFixed(2))),
          onChangeEnd: running ? null : (v) => _opt(key, double.parse(v.toStringAsFixed(2))),
        ),
      ]),
    );
  }

  // ---------------- Phụ đề ----------------

  Widget _segmentsCard() {
    final overIdx = <int>[];
    for (var i = 0; i < segs.length; i++) {
      if (segs[i]['fit'] == 'over' || (segs[i]['vi'] ?? '').toString().length > _slot(i) * charsPerSec * 1.3) {
        overIdx.add(i);
      }
    }
    final shown = onlyOver ? overIdx : List.generate(segs.length, (i) => i);
    final canRedub = !running && pr.done('translate');
    return Card(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 12, 8),
          child: Row(children: [
            Text('Phụ đề (${segs.length} câu)', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(width: 12),
            if (overIdx.isNotEmpty)
              FilterChip(
                label: Text('${overIdx.length} câu quá dài'),
                avatar: const Icon(Icons.warning_amber, size: 16, color: Colors.orangeAccent),
                selected: onlyOver,
                onSelected: (v) => setState(() => onlyOver = v),
              ),
            const Spacer(),
            FilledButton.tonalIcon(
              onPressed: canRedub ? () => _run(['run', '--from', 'tts']) : null,
              icon: const Icon(Icons.record_voice_over),
              label: const Text('Lồng tiếng lại & xuất'),
            ),
          ]),
        ),
        const Divider(height: 1),
        Expanded(
          child: segs.isEmpty
              ? Center(
                  child: Text(running ? 'Đang xử lý...' : 'Bấm "Bắt đầu tự động" để nhận dạng và dịch phụ đề',
                      style: const TextStyle(color: Colors.white38)),
                )
              : ListView.separated(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  itemCount: shown.length,
                  separatorBuilder: (_, _) => const Divider(height: 1, indent: 12, endIndent: 12),
                  itemBuilder: (_, k) => _segRow(shown[k], overIdx.contains(shown[k])),
                ),
        ),
      ]),
    );
  }

  Widget _segRow(int i, bool over) {
    final s = segs[i];
    final id = s['id'] as int;
    final maxChars = (_slot(i) * charsPerSec).round();
    final len = (s['vi'] ?? '').toString().length;
    final tts = File(p.join(pr.dir, 'tts', '${id.toString().padLeft(4, '0')}.mp3'));
    return Container(
      color: over ? Colors.orange.withValues(alpha: 0.07) : null,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        SizedBox(
          width: 78,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('#$id', style: const TextStyle(fontSize: 12, color: Colors.white38)),
            Text(fmtTime(s['start'] as num), style: const TextStyle(fontSize: 12, fontFeatures: [FontFeature.tabularFigures()])),
            Text(fmtTime(s['end'] as num),
                style: const TextStyle(fontSize: 12, color: Colors.white54, fontFeatures: [FontFeature.tabularFigures()])),
          ]),
        ),
        Expanded(
          flex: 4,
          child: Padding(
            padding: const EdgeInsets.only(top: 6, right: 12),
            child: SelectableText(s['text']?.toString() ?? '', style: const TextStyle(color: Colors.white60)),
          ),
        ),
        Expanded(
          flex: 5,
          child: TextField(
            controller: ctrls[id],
            readOnly: running,
            minLines: 1,
            maxLines: 4,
            style: const TextStyle(fontSize: 14),
            decoration: const InputDecoration(hintText: 'Chưa dịch'),
            onChanged: (v) => _onEdit(s, v),
          ),
        ),
        SizedBox(
          width: 70,
          child: Column(children: [
            const SizedBox(height: 4),
            Tooltip(
              message: 'Số ký tự / tối đa nên dùng để đọc kịp trong khung giờ',
              child: Text('$len/$maxChars',
                  style: TextStyle(fontSize: 12, color: len > maxChars * 1.3 ? Colors.orangeAccent : Colors.white54)),
            ),
            if (tts.existsSync())
              IconButton(
                tooltip: 'Nghe câu lồng tiếng',
                visualDensity: VisualDensity.compact,
                icon: Icon(Icons.volume_up, size: 18, color: s['fit'] == 'over' ? Colors.orangeAccent : null),
                onPressed: () => Process.run('explorer', [tts.path]),
              ),
          ]),
        ),
      ]),
    );
  }

  Widget _logPanel() {
    final logs = job?.logs ?? const <String>[];
    return Card(
      child: ExpansionTile(
        dense: true,
        title: Text(logs.isEmpty ? 'Nhật ký (bấm để xem chi tiết)' : 'Nhật ký · ${logs.last}',
            maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 13)),
        children: [
          SizedBox(
            height: 160,
            child: ListView.builder(
              controller: logScroll,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              itemCount: logs.length,
              itemBuilder: (_, i) => SelectableText(logs[i],
                  style: const TextStyle(fontSize: 12, fontFamily: 'Consolas', color: Colors.white70)),
            ),
          ),
        ],
      ),
    );
  }
}
