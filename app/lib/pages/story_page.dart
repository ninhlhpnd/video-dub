import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

import '../engine.dart';
import '../jobs.dart';
import '../main.dart';
import '../models.dart';
import 'music_dialog.dart';
import 'upload_dialog.dart';

const storyGenres = [
  'Truyện đời thường cảm động',
  'Tình cảm gia đình',
  'Trinh thám, bí ẩn',
  'Truyện ma nhẹ nhàng, kỳ ảo dân gian',
  'Cổ tích mới cho thiếu nhi',
  'Phiêu lưu',
  'Khoa học viễn tưởng',
  'Lịch sử hư cấu Việt Nam',
  'Truyện hài',
];

const storyImageSources = {
  'auto': 'Tự động: Gemini, hết lượt thì vẽ trên máy',
  'local': 'Vẽ trên máy (card NVIDIA, miễn phí)',
  'gemini': 'Chỉ Gemini',
};

/// Màn hình Truyện kể: AI viết → duyệt → vẽ ảnh → đọc → ghép video → đăng.
class StoryPage extends StatefulWidget {
  final Project project;
  const StoryPage({super.key, required this.project});

  @override
  State<StoryPage> createState() => _StoryPageState();
}

class _StoryPageState extends State<StoryPage> with SingleTickerProviderStateMixin {
  late final Project pr = widget.project;
  late final tabs = TabController(length: 4, vsync: this);
  Map<String, dynamic> st = {};
  int rev = 0; // tăng khi đọc lại từ đĩa để các ô nhập hiện nội dung mới
  String? _lastStep;
  bool _wasRunning = false;
  Timer? _saveTimer, _ticker;
  bool dirty = false;

  bool get _claude => settings.get('story_writer') == 'claude';
  bool get running => jobs.isRunning(pr.dir);
  File get _file => File(p.join(pr.dir, 'story.json'));

  @override
  void initState() {
    super.initState();
    _load();
    _wasRunning = running;
    _lastStep = jobs.of(pr.dir)?.step;
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
    _flush();
    tabs.dispose();
    super.dispose();
  }

  void _load() {
    if (_file.existsSync()) st = jsonDecode(_file.readAsStringSync()) as Map<String, dynamic>;
    rev++;
  }

  void _onJobs() {
    if (!mounted) return;
    final j = jobs.of(pr.dir);
    if (j != null && (j.step != _lastStep || j.running != _wasRunning)) {
      if (_wasRunning && !j.running && j.error == null && j.plan.isNotEmpty) {
        // mở tab của bước vừa xong để duyệt
        const tabOf = {'outline': 0, 'write': 1, 'polish': 1, 'scenes': 2, 'images': 2, 'voice': 3, 'compose': 3};
        tabs.animateTo(tabOf[j.plan.last] ?? 0);
      }
      _lastStep = j.step;
      _wasRunning = j.running;
      pr.reload();
      _load();
    }
    setState(() {});
  }

  /// Sửa tay: lưu story.json và đánh dấu các bước sau cần chạy lại.
  void _edited(List<String> stale) {
    dirty = true;
    pr.clearSteps(stale);
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 600), _flush);
    setState(() {});
  }

  void _flush() {
    if (!dirty || running) return;
    final tmp = File('${_file.path}.app');
    tmp.writeAsStringSync(const JsonEncoder.withIndent(' ').convert(st));
    tmp.renameSync(_file.path);
    dirty = false;
  }

  void _opt(String k, dynamic v, [List<String> stale = const []]) {
    pr.setOption(k, v);
    if (stale.isNotEmpty) pr.clearSteps(stale);
    setState(() {});
  }

  Future<void> _run(String from, {String? until}) async {
    if (running) return;
    if (const ['outline', 'write', 'polish', 'scenes'].contains(from) &&
        settings.get('story_writer') != 'claude_cli' &&
        settings.get(_claude ? 'claude_api_key' : 'gemini_api_key').isEmpty) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(
              content: Text('Chưa nhập ${_claude ? 'Claude' : 'Gemini'} API key: mở ⚙ Cài đặt ở màn hình chính')));
      return;
    }
    _saveTimer?.cancel();
    _flush();
    final steps = pr.activeSteps.skipWhile((s) => s != from).toList();
    final plan = until == null ? steps : steps.sublist(0, steps.indexOf(until) + 1);
    _wasRunning = true;
    await jobs.start(pr, ['run', '--from', from, if (until != null) ...['--until', until]], plan);
  }

  Future<void> _rerun(String s) async {
    const paid = {
      'outline': 'Dàn ý và toàn bộ nội dung hiện tại sẽ bị thay thế.',
      'write': 'Nội dung các chương (kể cả phần bạn đã sửa) sẽ bị viết lại.',
      'polish': 'AI sẽ đọc lại và viết lại các chương còn lỗi.',
      'scenes': 'Danh sách cảnh, tiêu đề và mô tả sẽ bị thay thế.',
    };
    if (pr.done(s) && paid.containsKey(s)) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text('Chạy lại "${stepNames[s]}"?'),
          content: Text('${paid[s]}${_claude ? ' Bước này tốn tiền Claude API.' : ''}'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Hủy')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Chạy lại')),
          ],
        ),
      );
      if (ok != true) return;
    }
    await _run(s, until: s);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(pr.name, overflow: TextOverflow.ellipsis),
        actions: [
          TextButton.icon(
            onPressed: pr.hasOutput ? () => Process.run('explorer', [pr.output]) : null,
            icon: const Icon(Icons.smart_display),
            label: const Text('Xem video'),
          ),
          IconButton(
              tooltip: 'Mở thư mục', onPressed: () => Process.run('explorer', [pr.dir]), icon: const Icon(Icons.folder_open)),
          FilledButton.icon(
            onPressed: pr.hasOutput && !running
                ? () async {
                    _flush();
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
        child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          SizedBox(width: 360, child: ListView(children: [_steps(), const SizedBox(height: 14), _options()])),
          const SizedBox(width: 14),
          Expanded(
            child: Card(
              child: Column(children: [
                TabBar(controller: tabs, tabs: const [
                  Tab(text: '1. Dàn ý'),
                  Tab(text: '2. Nội dung'),
                  Tab(text: '3. Hình ảnh'),
                  Tab(text: '4. Tiêu đề & mô tả'),
                ]),
                Expanded(
                  child: TabBarView(
                    key: ValueKey(rev),
                    controller: tabs,
                    children: [_outlineTab(), _textTab(), _imagesTab(), _metaTab()],
                  ),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }

  // ---------------- Các bước ----------------

  Widget _steps() {
    final steps = pr.activeSteps;
    final next = steps.where((s) => !pr.done(s)).firstOrNull;
    final j = jobs.of(pr.dir);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('Quy trình', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          for (final s in steps)
            ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: running && j?.step == s
                  ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.5))
                  : Icon(pr.done(s) ? Icons.check_circle : Icons.radio_button_unchecked,
                      color: pr.done(s) ? Colors.greenAccent : Colors.white38),
              title: Text(stepNames[s]!),
              trailing: IconButton(
                tooltip: 'Chạy (lại) riêng bước này',
                icon: const Icon(Icons.replay, size: 20),
                onPressed: running ? null : () => _rerun(s),
              ),
            ),
          const SizedBox(height: 8),
          if (running && j != null) ...[
            LinearProgressIndicator(value: j.pct > 0 ? j.pct / 100 : null, minHeight: 6),
            const SizedBox(height: 6),
            Text('${j.msg}\nĐã chạy ${fmtDuration(j.elapsed)}',
                style: const TextStyle(fontSize: 12, color: Colors.white70)),
            const SizedBox(height: 8),
            OutlinedButton.icon(
                onPressed: () => jobs.stop(pr.dir), icon: const Icon(Icons.stop), label: const Text('Dừng')),
          ] else if (next == null)
            const Text('Đã hoàn tất', textAlign: TextAlign.center, style: TextStyle(color: Colors.greenAccent))
          else ...[
            FilledButton.icon(
              onPressed: () => _run(next, until: next),
              icon: const Icon(Icons.play_arrow),
              label: Text('Duyệt xong → ${stepNames[next]}'),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: () => _run(next),
              icon: const Icon(Icons.fast_forward),
              label: const Text('Chạy hết tới cuối, không dừng duyệt'),
            ),
          ],
          if (!running && j?.error != null) ...[
            const SizedBox(height: 8),
            SelectableText(j!.error!, style: const TextStyle(color: Colors.redAccent)),
          ],
          const SizedBox(height: 8),
          Text(
              _claude || ((st['cost'] as num?) ?? 0) > 0
                  ? 'Chi phí Claude đã dùng: khoảng \$${((st['cost'] as num?) ?? 0).toStringAsFixed(2)}'
                  : 'Người viết: Gemini Flash (miễn phí)',
              style: const TextStyle(fontSize: 12, color: Colors.amberAccent)),
        ]),
      ),
    );
  }

  Widget _options() {
    final o = pr.options;
    final music = (o['music'] ?? '').toString();
    double val(String k, double d) => (o[k] as num?)?.toDouble() ?? d;
    Widget text(String k, String label, String hint, {int lines = 1}) => Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: TextFormField(
            initialValue: (o[k] ?? '').toString(),
            minLines: lines,
            maxLines: lines + 3,
            decoration: InputDecoration(labelText: label, hintText: hint),
            onChanged: running ? null : (v) => pr.setOption(k, v),
          ),
        );
    Widget slider(String k, String label, double d, double min, double max, int div, String Function(double) fmt,
            List<String> stale) =>
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('$label: ${fmt(val(k, d))}', style: const TextStyle(fontSize: 13)),
          Slider(
            value: val(k, d).clamp(min, max),
            min: min,
            max: max,
            divisions: div,
            onChanged: running ? null : (v) => setState(() => o[k] = v),
            onChangeEnd: running ? null : (v) => _opt(k, double.parse(v.toStringAsFixed(2)), stale),
          ),
        ]);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Text('Tùy chọn', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          text('story_topic', 'Ý tưởng / chủ đề', 'Để trống thì AI tự nghĩ', lines: 2),
          text('story_genre', 'Thể loại', storyGenres.first),
          text('story_tone', 'Giọng văn', 'VD: ấm áp, chậm rãi, có chút hài'),
          text('story_note', 'Yêu cầu thêm', 'VD: bối cảnh miền Tây, kết có hậu, dấu ấn riêng của kênh', lines: 2),
          slider('story_minutes', 'Độ dài', 15, 3, 40, 37, (v) => '${v.round()} phút', const []),
          slider('story_images', 'Số ảnh minh họa', 15, 4, 60, 56, (v) => '${v.round()}', const []),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Biên tập lại cả truyện'),
            subtitle: const Text('AI đọc lại, tìm lỗi và sửa chương yếu (tốn thêm lượt gọi, truyện hay hơn)',
                style: TextStyle(fontSize: 12)),
            value: o['story_polish'] != false,
            onChanged: running ? null : (v) => _opt('story_polish', v),
          ),
          const Divider(),
          DropdownButtonFormField<String>(
            initialValue: storyImageSources.containsKey(o['story_image_source']) ? o['story_image_source'] : 'auto',
            isExpanded: true,
            decoration: const InputDecoration(labelText: 'Nguồn ảnh'),
            items: storyImageSources.entries
                .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value, overflow: TextOverflow.ellipsis)))
                .toList(),
            onChanged: running ? null : (v) => _opt('story_image_source', v),
          ),
          const SizedBox(height: 12),
          Row(children: [
            Expanded(
              child: DropdownButtonFormField<String>(
                initialValue: voices.containsKey(o['voice']) ? o['voice'] : 'vieneu:Thiện Minh',
                isExpanded: true,
                menuMaxHeight: 420,
                decoration: const InputDecoration(labelText: 'Giọng kể'),
                items: voices.entries
                    .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value, overflow: TextOverflow.ellipsis)))
                    .toList(),
                onChanged: running ? null : (v) => _opt('voice', v, const ['voice', 'compose']),
              ),
            ),
            IconButton(
              tooltip: 'Nghe thử',
              icon: const Icon(Icons.play_circle_fill),
              onPressed: () async {
                final ev = await Engine.once(['preview', '--voice', (o['voice'] ?? 'vieneu:Thiện Minh').toString()],
                    engineOverride: settings.get('engine_dir'), timeout: const Duration(seconds: 150));
                if (ev.type == 'done') Process.run('explorer', [ev.data['file'].toString()]);
              },
            ),
          ]),
          const SizedBox(height: 8),
          slider('story_speed', 'Tốc độ đọc', 1.0, 0.8, 1.3, 10, (v) => '${v.toStringAsFixed(2)}x',
              const ['voice', 'compose']),
          Row(children: [
            const Icon(Icons.music_note, size: 20),
            Expanded(
                child: Text(music.isEmpty ? 'Chưa chọn nhạc nền' : p.basename(music), overflow: TextOverflow.ellipsis)),
            if (music.isNotEmpty)
              IconButton(
                  icon: const Icon(Icons.close, size: 18),
                  onPressed: running ? null : () => _opt('music', '', const ['compose'])),
            TextButton(
              onPressed: running
                  ? null
                  : () async {
                      final f = await showDialog<String>(context: context, builder: (_) => MusicDialog(current: music));
                      if (f != null) _opt('music', f, const ['compose']);
                    },
              child: const Text('Chọn'),
            ),
          ]),
          if (music.isNotEmpty)
            slider('music_volume', 'Âm lượng nhạc', 0.1, 0, 0.5, 25, (v) => '${(v * 100).round()}%', const ['compose']),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Chèn phụ đề'),
            value: o['burn_subs'] != false,
            onChanged: running ? null : (v) => _opt('burn_subs', v, const ['compose']),
          ),
        ]),
      ),
    );
  }

  // ---------------- Các tab duyệt ----------------

  Widget _empty(String t) => Center(child: Text(t, style: const TextStyle(color: Colors.white38)));

  Widget _edit(Map m, String k, String label, List<String> stale, {int min = 1, int max = 6}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          initialValue: (m[k] ?? '').toString(),
          readOnly: running,
          minLines: min,
          maxLines: max,
          decoration: InputDecoration(labelText: label),
          onChanged: (v) {
            m[k] = v;
            _edited(stale);
          },
        ),
      );

  static const _after = {
    'outline': ['write', 'polish', 'scenes', 'images', 'voice', 'compose'],
    'text': ['voice', 'compose'],
  };

  Widget _outlineTab() {
    final o = st['outline'] as Map?;
    if (o == null) return _empty('Điền ý tưởng bên trái rồi bấm nút để AI lập dàn ý.');
    final stale = _after['outline']!;
    return ListView(padding: const EdgeInsets.all(16), children: [
      _edit(o, 'title', 'Tên truyện', stale),
      _edit(o, 'logline', 'Tóm tắt một câu', stale),
      _edit(o, 'setting', 'Bối cảnh', stale),
      _edit(o, 'art_style', 'Phong cách tranh (tiếng Anh)', const ['images', 'compose']),
      const Text('Nhân vật', style: TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 10),
      for (final c in (o['characters'] as List).cast<Map>()) ...[
        _edit(c, 'name', 'Tên', stale),
        _edit(c, 'role', 'Vai trò', stale),
        _edit(c, 'look_en', 'Ngoại hình để vẽ (tiếng Anh)', const ['scenes', 'images', 'compose']),
        const Divider(),
      ],
      for (final (i, c) in (o['chapters'] as List).cast<Map>().indexed) ...[
        _edit(c, 'title', 'Chương ${i + 1}', stale),
        _edit(c, 'summary', 'Nội dung', stale, min: 3, max: 10),
      ],
    ]);
  }

  Widget _textTab() {
    final ch = (st['chapters'] as List?)?.cast<Map>() ?? [];
    if (ch.isEmpty) return _empty('Nội dung các chương sẽ hiện ở đây sau bước "Viết truyện".');
    final rv = st['review'] as Map?;
    return ListView(padding: const EdgeInsets.all(16), children: [
      if (rv != null)
        Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: Text('Biên tập viên AI: ${rv['verdict']} (đã sửa ${rv['fixed']} điểm)',
              style: const TextStyle(color: Colors.amberAccent, fontSize: 13)),
        ),
      for (final (i, c) in ch.indexed) ...[
        Text('Chương ${i + 1} · ${c['text'].toString().length} ký tự', style: const TextStyle(color: Colors.white54)),
        const SizedBox(height: 8),
        _edit(c, 'title', 'Tên chương', const []),
        _edit(c, 'text', 'Nội dung', _after['text']!, min: 8, max: 60),
      ],
    ]);
  }

  Widget _imagesTab() {
    final sc = (st['scenes'] as List?)?.cast<Map>() ?? [];
    if (sc.isEmpty) return _empty('Danh sách cảnh sẽ hiện ở đây sau bước "Chia cảnh".');
    return GridView.builder(
      padding: const EdgeInsets.all(16),
      gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
          maxCrossAxisExtent: 360, mainAxisSpacing: 14, crossAxisSpacing: 14, childAspectRatio: 0.92),
      itemCount: sc.length,
      itemBuilder: (_, i) {
        final s = sc[i];
        final f = File((s['image'] ?? '').toString());
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          AspectRatio(
            aspectRatio: 16 / 9,
            child: s['image'] != null && f.existsSync()
                ? Image.file(f, fit: BoxFit.cover, cacheWidth: 640)
                : Container(color: Colors.black26, child: const Icon(Icons.image_outlined, color: Colors.white24)),
          ),
          Row(children: [
            Text('Cảnh ${i + 1} · chương ${s['chapter']}', style: const TextStyle(fontSize: 12, color: Colors.white60)),
            const Spacer(),
            IconButton(
              tooltip: 'Vẽ lại ảnh khác (bấm rồi chạy bước Vẽ ảnh)',
              icon: const Icon(Icons.refresh, size: 18),
              onPressed: running
                  ? null
                  : () {
                      s['seed'] = (s['seed'] as num).toInt() + 97;
                      s.remove('custom');
                      s.remove('image');
                      _edited(const ['images', 'compose']);
                    },
            ),
            IconButton(
              tooltip: 'Dùng ảnh trên máy',
              icon: const Icon(Icons.folder_open, size: 18),
              onPressed: running
                  ? null
                  : () async {
                      final pick = await FilePicker.pickFile(type: FileType.image, dialogTitle: 'Chọn ảnh');
                      if (pick?.path == null) return;
                      s['image'] = pick!.path;
                      s['custom'] = true;
                      _edited(const ['images', 'compose']);
                    },
            ),
          ]),
          Expanded(
            child: TextFormField(
              initialValue: s['prompt'].toString(),
              readOnly: running,
              expands: true,
              minLines: null,
              maxLines: null,
              style: const TextStyle(fontSize: 12),
              onChanged: (v) {
                s['prompt'] = v;
                _edited(const ['images', 'compose']);
              },
            ),
          ),
        ]);
      },
    );
  }

  Widget _metaTab() {
    final y = st['youtube'] as Map?;
    if (y == null) return _empty('Tiêu đề, mô tả, hashtag sẽ hiện ở đây sau bước "Chia cảnh".');
    Widget list(String k, String label) => Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: TextFormField(
            initialValue: (y[k] as List).join(k == 'tags' ? ', ' : ' '),
            readOnly: running,
            maxLines: 3,
            minLines: 1,
            decoration: InputDecoration(labelText: label),
            onChanged: (v) {
              y[k] = v.split(k == 'tags' ? ',' : RegExp(r'\s+')).map((e) => e.trim()).where((e) => e.isNotEmpty).toList();
              _edited(const []);
            },
          ),
        );
    return ListView(padding: const EdgeInsets.all(16), children: [
      Wrap(spacing: 8, runSpacing: 8, children: [
        for (final t in (y['titles'] as List))
          ChoiceChip(
            label: Text(t.toString()),
            selected: y['title'] == t,
            onSelected: running
                ? null
                : (_) {
                    y['title'] = t;
                    rev++;
                    _edited(const []);
                  },
          ),
      ]),
      const SizedBox(height: 14),
      _edit(y, 'title', 'Tiêu đề', const []),
      _edit(y, 'description', 'Mô tả', const [], min: 4, max: 12),
      list('hashtags', 'Hashtag (cách nhau dấu cách)'),
      list('tags', 'Từ khóa (cách nhau dấu phẩy)'),
      const Text(
          'Khi đăng, app tự thêm vào mô tả: mốc thời gian từng chương, nguồn nhạc, và câu ghi rõ có dùng AI.',
          style: TextStyle(fontSize: 12, color: Colors.white54)),
    ]);
  }
}
