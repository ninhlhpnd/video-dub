import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:path/path.dart' as p;

import '../engine.dart';
import '../jobs.dart';
import '../main.dart';
import '../models.dart';
import 'story_page.dart';

const seriesGenres = [
  'Tiên hiệp, tu tiên',
  'Huyền huyễn, kỳ ảo',
  'Kiếm hiệp, võ hiệp',
  'Đô thị, đời thường',
  'Trinh thám, bí ẩn',
  'Ngôn tình',
  'Lịch sử hư cấu Việt Nam',
  'Khoa học viễn tưởng',
  'Kinh dị nhẹ, linh dị',
  'Xuyên không, trọng sinh',
];

const _cps = 14.5; // ký tự/giây khi đọc (giống engine)

/// Một bộ truyện dài trên đĩa: `<workspace>/_series/<id>/series.json` + `chapters/NNNN.json`
class Series {
  final String dir;
  Map<String, dynamic> data;
  Series(this.dir, this.data);

  static String root(String workspace) => p.join(workspace, '_series');

  static Series? tryLoad(String dir) {
    try {
      final f = File(p.join(dir, 'series.json'));
      return Series(dir, jsonDecode(f.readAsStringSync()) as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  static List<Series> listIn(String workspace) {
    final d = Directory(root(workspace));
    if (!d.existsSync()) return [];
    final out = d.listSync().whereType<Directory>().map((e) => tryLoad(e.path)).whereType<Series>().toList();
    out.sort((a, b) => b.created.compareTo(a.created));
    return out;
  }

  String get title => data['title']?.toString() ?? 'Bộ truyện';
  String get created => data['created']?.toString() ?? '';
  Map<String, dynamic> get opt => (data['options'] as Map).cast<String, dynamic>();
  Map<String, dynamic>? get bible => (data['bible'] as Map?)?.cast<String, dynamic>();
  List<Map> get arcs => ((data['arcs'] as List?) ?? []).cast<Map>();
  List<Map> get episodes => ((data['episodes'] as List?) ?? []).cast<Map>();
  Map get web => (data['web'] as Map?) ?? {};
  Map get memory => (data['memory'] as Map?) ?? {};
  int get total => (opt['chapters'] as num?)?.toInt() ?? 0;
  int get written => (data['written'] as num?)?.toInt() ?? 0;
  double get cost => (data['cost'] as num?)?.toDouble() ?? 0;
  String get cover => p.join(dir, 'cover.jpg');
  int get nextEpisodeFrom => episodes.isEmpty ? 1 : (episodes.last['to'] as num).toInt() + 1;

  File chFile(int n) => File(p.join(dir, 'chapters', '${n.toString().padLeft(4, '0')}.json'));

  Map<String, dynamic>? chapter(int n) {
    final f = chFile(n);
    if (!f.existsSync()) return null;
    return jsonDecode(f.readAsStringSync()) as Map<String, dynamic>;
  }

  void saveChapter(Map<String, dynamic> ch) {
    final f = chFile((ch['n'] as num).toInt());
    final tmp = File('${f.path}.app');
    tmp.writeAsStringSync(const JsonEncoder.withIndent(' ').convert(ch));
    tmp.renameSync(f.path);
  }

  void save() {
    final f = File(p.join(dir, 'series.json'));
    final tmp = File('${f.path}.app');
    tmp.writeAsStringSync(const JsonEncoder.withIndent(' ').convert(data));
    tmp.renameSync(f.path);
  }

  void setOption(String k, dynamic v) {
    final fresh = tryLoad(dir);
    if (fresh != null) data = fresh.data;
    (data['options'] as Map)[k] = v;
    save();
  }
}

String _hours(int chars) {
  final m = chars / _cps / 60;
  return m < 90 ? '${m.round()} phút' : '${(m / 60).toStringAsFixed(1)} giờ';
}

// =====================================================================
// Danh sách bộ truyện
// =====================================================================

class SeriesListPage extends StatefulWidget {
  const SeriesListPage({super.key});

  @override
  State<SeriesListPage> createState() => _SeriesListPageState();
}

class _SeriesListPageState extends State<SeriesListPage> {
  List<Series> list = [];

  @override
  void initState() {
    super.initState();
    _refresh();
    jobs.addListener(_refresh);
  }

  @override
  void dispose() {
    jobs.removeListener(_refresh);
    super.dispose();
  }

  void _refresh() {
    if (mounted) setState(() => list = Series.listIn(settings.workspace));
  }

  Future<void> _create() async {
    final opt = await showDialog<Map<String, dynamic>>(context: context, builder: (_) => const _NewSeriesDialog());
    if (opt == null) return;
    final ev = await Engine.once(['series_new', '--workspace', settings.workspace, '--options', jsonEncode(opt)],
        engineOverride: settings.get('engine_dir'));
    if (!mounted) return;
    if (ev.type == 'error') {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Không tạo được: ${ev.msg}')));
      return;
    }
    final dir = ev.data['project'].toString();
    await Navigator.push(context, MaterialPageRoute(builder: (_) => SeriesPage(dir: dir, autoPlan: true)));
    _refresh();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Truyện dài nhiều chương'),
        actions: [
          FilledButton.icon(onPressed: _create, icon: const Icon(Icons.add), label: const Text('Bộ truyện mới')),
          const SizedBox(width: 12),
        ],
      ),
      body: list.isEmpty
          ? Center(
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                const Icon(Icons.menu_book, size: 56, color: Colors.white24),
                const SizedBox(height: 12),
                const Text('Chưa có bộ truyện nào', style: TextStyle(fontSize: 16)),
                const SizedBox(height: 6),
                const SizedBox(
                  width: 520,
                  child: Text(
                    'AI lập sổ tay thế giới và nhân vật, chia quyển, rồi viết từng chương nối tiếp nhau (tới cả nghìn '
                    'chương). Mỗi lần làm video, app lấy các chương tiếp theo thành một tập 15-20 phút.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.white54),
                  ),
                ),
                const SizedBox(height: 16),
                FilledButton.icon(onPressed: _create, icon: const Icon(Icons.add), label: const Text('Tạo bộ truyện')),
              ]),
            )
          : GridView.builder(
              padding: const EdgeInsets.all(20),
              gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                  maxCrossAxisExtent: 340, mainAxisSpacing: 14, crossAxisSpacing: 14, childAspectRatio: 1.35),
              itemCount: list.length,
              itemBuilder: (_, i) => _card(list[i]),
            ),
    );
  }

  Widget _card(Series s) {
    final j = jobs.of(s.dir);
    final c = Theme.of(context).colorScheme;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () async {
          await Navigator.push(context, MaterialPageRoute(builder: (_) => SeriesPage(dir: s.dir)));
          _refresh();
        },
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(Icons.auto_stories, color: c.primary),
              const SizedBox(width: 8),
              Expanded(
                child: Text(s.title,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
              ),
            ]),
            const SizedBox(height: 6),
            Text((s.opt['genre'] ?? '').toString(),
                maxLines: 1, overflow: TextOverflow.ellipsis, style: const TextStyle(color: Colors.white54)),
            const Spacer(),
            Text('${s.written}/${s.total} chương · ${s.episodes.length} tập video',
                style: const TextStyle(fontSize: 13)),
            const SizedBox(height: 6),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(value: s.total == 0 ? 0 : s.written / s.total, minHeight: 6),
            ),
            const SizedBox(height: 6),
            Text(
              j?.running == true ? j!.msg : (s.web['url'] != null ? 'Đã đăng lên web' : ''),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12, color: j?.running == true ? c.primary : Colors.white38),
            ),
          ]),
        ),
      ),
    );
  }
}

class _NewSeriesDialog extends StatefulWidget {
  const _NewSeriesDialog();

  @override
  State<_NewSeriesDialog> createState() => _NewSeriesDialogState();
}

class _NewSeriesDialogState extends State<_NewSeriesDialog> {
  String genre = seriesGenres.first;
  final idea = TextEditingController(), tone = TextEditingController(), note = TextEditingController();
  final chapters = TextEditingController(text: '1000');
  double chars = 3000, minutes = 17;

  @override
  Widget build(BuildContext context) {
    final n = int.tryParse(chapters.text) ?? 0;
    return AlertDialog(
      title: const Text('Bộ truyện mới'),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Autocomplete<String>(
              initialValue: TextEditingValue(text: genre),
              optionsBuilder: (v) => seriesGenres.where((g) => g.toLowerCase().contains(v.text.toLowerCase())),
              onSelected: (v) => genre = v,
              fieldViewBuilder: (_, ctrl, focus, _) => TextField(
                controller: ctrl,
                focusNode: focus,
                decoration: const InputDecoration(labelText: 'Thể loại (chọn hoặc tự gõ)'),
                onChanged: (v) => genre = v,
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: idea,
              minLines: 2,
              maxLines: 5,
              decoration: const InputDecoration(
                  labelText: 'Ý tưởng chính', hintText: 'Để trống thì AI tự nghĩ. VD: cậu bé chăn trâu nhặt được…'),
            ),
            const SizedBox(height: 12),
            TextField(
                controller: tone,
                decoration: const InputDecoration(labelText: 'Giọng văn', hintText: 'VD: hào hùng, có chút hài')),
            const SizedBox(height: 12),
            TextField(
                controller: note,
                maxLines: 3,
                minLines: 1,
                decoration: const InputDecoration(labelText: 'Yêu cầu thêm', hintText: 'VD: không có tình cảm nam nữ')),
            const SizedBox(height: 16),
            Row(children: [
              SizedBox(
                width: 140,
                child: TextField(
                  controller: chapters,
                  keyboardType: TextInputType.number,
                  inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                  decoration: const InputDecoration(labelText: 'Số chương'),
                  onChanged: (_) => setState(() {}),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Text(
                  n > 0 ? 'Khoảng ${_hours((n * chars).round())} đọc, ~${(n * chars / _cps / 60 / minutes).ceil()} tập video' : '',
                  style: const TextStyle(color: Colors.white60, fontSize: 13),
                ),
              ),
            ]),
            const SizedBox(height: 12),
            Text('Độ dài mỗi chương: ${chars.round()} ký tự (${chars < 2400 ? 'ngắn' : chars <= 3600 ? '≈ 1 trang A4' : 'dài'})'),
            Slider(value: chars, min: 1500, max: 6000, divisions: 18, onChanged: (v) => setState(() => chars = v)),
            Text('Mỗi tập video: ${minutes.round()} phút (≈ ${(minutes * 60 * _cps / chars).toStringAsFixed(1)} chương)'),
            Slider(value: minutes, min: 10, max: 25, divisions: 15, onChanged: (v) => setState(() => minutes = v)),
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Hủy')),
        FilledButton(
          onPressed: n < 3
              ? null
              : () => Navigator.pop(context, {
                    'genre': genre.trim(),
                    'idea': idea.text.trim(),
                    'tone': tone.text.trim(),
                    'note': note.text.trim(),
                    'chapters': n,
                    'chapter_chars': chars.round(),
                    'episode_minutes': minutes.round(),
                  }),
          child: const Text('Tạo & lập sổ tay'),
        ),
      ],
    );
  }
}

// =====================================================================
// Một bộ truyện
// =====================================================================

class SeriesPage extends StatefulWidget {
  final String dir;
  final bool autoPlan;
  const SeriesPage({super.key, required this.dir, this.autoPlan = false});

  @override
  State<SeriesPage> createState() => _SeriesPageState();
}

class _SeriesPageState extends State<SeriesPage> with SingleTickerProviderStateMixin {
  late Series s = Series.tryLoad(widget.dir)!;
  late final tabs = TabController(length: 4, vsync: this);
  List<Map<String, dynamic>> chapters = []; // {n, title, len, edited}
  int? selected;
  Map<String, dynamic>? cur; // chương đang mở
  int rev = 0;
  int writeCount = 10;
  bool dirty = false;
  bool _wasRunning = false;
  Timer? _saveTimer, _ticker;
  DateTime _lastLoad = DateTime(2000);

  bool get running => jobs.isRunning(s.dir);
  bool get _paid => settings.get('story_writer') == 'claude';

  @override
  void initState() {
    super.initState();
    _load();
    _wasRunning = running;
    jobs.addListener(_onJobs);
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (running && mounted) setState(() {});
    });
    if (widget.autoPlan && s.bible == null) WidgetsBinding.instance.addPostFrameCallback((_) => _plan());
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
    s = Series.tryLoad(widget.dir) ?? s;
    final out = <Map<String, dynamic>>[];
    for (var n = 1;; n++) {
      final c = s.chapter(n);
      if (c == null) break;
      out.add({'n': n, 'title': c['title'], 'len': c['text'].toString().length, 'edited': c['edited'] == true});
    }
    chapters = out;
    if (selected != null && selected! > chapters.length) {
      selected = null;
      cur = null;
    }
    _lastLoad = DateTime.now();
    rev++;
  }

  void _onJobs() {
    if (!mounted) return;
    final j = jobs.of(s.dir);
    final r = j?.running == true;
    // đang viết: đọc lại tối đa 3 giây một lần (bộ truyện lớn có thể nặng vài MB)
    if (r != _wasRunning || (r && DateTime.now().difference(_lastLoad).inSeconds >= 3)) {
      final finished = _wasRunning && !r;
      _wasRunning = r;
      final keepRev = rev;
      _load();
      if (r && !finished) rev = keepRev; // không dựng lại ô đang sửa giữa lúc AI viết
      if (finished && j?.error == null && j?.plan.firstOrNull == 'series_episode') _openLastEpisode();
    }
    setState(() {});
  }

  void _edited() {
    dirty = true;
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 700), _flush);
  }

  void _flush() {
    if (!dirty || running) return;
    s.save();
    dirty = false;
  }

  Future<bool> _needKey() async {
    final w = settings.get('story_writer');
    if (w == 'claude_cli') return false;
    if (settings.get(w == 'claude' ? 'claude_api_key' : 'gemini_api_key').isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('Chưa nhập ${w == 'claude' ? 'Claude' : 'Gemini'} API key: mở ⚙ Cài đặt ở màn hình chính')));
      return true;
    }
    return false;
  }

  Future<void> _job(List<String> args, String step) async {
    if (running) return;
    _saveTimer?.cancel();
    _flush();
    _wasRunning = true;
    await jobs.startDir(s.dir, args, [step]);
  }

  Future<void> _plan() async {
    if (await _needKey()) return;
    if (s.bible != null) {
      final ok = await _confirm('Lập lại sổ tay?', 'Toàn bộ thế giới, nhân vật, các quyển và ${s.written} chương đã viết sẽ bị xóa.');
      if (!ok) return;
    }
    await _job(['series_plan'], 'series_plan');
  }

  Future<void> _write() async {
    if (await _needKey()) return;
    await _job(['series_write', '--count', '$writeCount'], 'series_write');
  }

  Future<void> _episode() async {
    if (s.nextEpisodeFrom > s.written) {
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Chưa có chương ${s.nextEpisodeFrom}: hãy viết thêm chương trước khi làm tập mới')));
      return;
    }
    await _job([
      'series_episode',
      '--workspace',
      settings.workspace,
      '--minutes',
      '${(s.opt['episode_minutes'] as num?) ?? 17}'
    ], 'series_episode');
  }

  Future<void> _openLastEpisode() async {
    if (s.episodes.isEmpty) return;
    final pr = Project.tryLoad(s.episodes.last['project'].toString());
    if (pr == null || !mounted) return;
    await Navigator.push(context, MaterialPageRoute(builder: (_) => StoryPage(project: pr)));
    if (mounted) setState(_load);
  }

  Future<void> _publish() async {
    if (settings.get('web_url').isEmpty || settings.get('web_api_key').isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Chưa nhập địa chỉ web và API key: mở ⚙ Cài đặt → Web đọc truyện')));
      return;
    }
    await _job(['series_publish'], 'series_publish');
  }

  Future<bool> _confirm(String title, String body) async =>
      await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text(title),
          content: Text(body),
          actions: [
            TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Hủy')),
            FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Đồng ý')),
          ],
        ),
      ) ==
      true;

  Future<void> _rewind(int n) async {
    final ok = await _confirm('Viết lại từ chương $n?',
        'Chương $n đến ${s.written} (${s.written - n + 1} chương) sẽ bị xóa; trí nhớ nhân vật quay về sau chương ${n - 1}. '
        'Sau đó bấm "Viết tiếp" để AI viết lại.');
    if (!ok) return;
    final ev = await Engine.once(['series_rewind', '--project', s.dir, '--n', '$n'],
        engineOverride: settings.get('engine_dir'));
    if (!mounted) return;
    if (ev.type == 'error') {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(ev.msg)));
    }
    setState(() {
      selected = null;
      cur = null;
      _load();
    });
  }

  @override
  Widget build(BuildContext context) {
    final url = s.web['url']?.toString();
    return Scaffold(
      appBar: AppBar(
        title: Text(s.title, overflow: TextOverflow.ellipsis),
        actions: [
          if (url != null && url.isNotEmpty)
            TextButton.icon(
                onPressed: () => Process.run('explorer', [url]),
                icon: const Icon(Icons.public),
                label: const Text('Xem trên web')),
          IconButton(
              tooltip: 'Mở thư mục', onPressed: () => Process.run('explorer', [s.dir]), icon: const Icon(Icons.folder_open)),
          const SizedBox(width: 12),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          SizedBox(
            width: 370,
            child: ListView(children: [
              _progressCard(),
              const SizedBox(height: 14),
              _videoCard(),
              const SizedBox(height: 14),
              _webCard(),
            ]),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Card(
              child: Column(children: [
                TabBar(controller: tabs, tabs: [
                  const Tab(text: 'Sổ tay'),
                  const Tab(text: 'Các quyển'),
                  Tab(text: 'Chương (${chapters.length})'),
                  const Tab(text: 'Trí nhớ AI'),
                ]),
                Expanded(
                  child: TabBarView(controller: tabs, children: [_bibleTab(), _arcsTab(), _chaptersTab(), _memoryTab()]),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }

  // ---------------- cột trái ----------------

  Widget _card(String title, List<Widget> children) => Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 10),
            ...children,
          ]),
        ),
      );

  Widget _progressCard() {
    final j = jobs.of(s.dir);
    final words = chapters.fold<int>(0, (a, c) => a + (c['len'] as int));
    final writer = {'claude': 'Claude API', 'claude_cli': 'Claude Code (gói thuê bao)'}[settings.get('story_writer')] ??
        'Gemini Flash (miễn phí)';
    return _card('Viết truyện', [
      Text('${s.written} / ${s.total} chương', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
      const SizedBox(height: 6),
      ClipRRect(
        borderRadius: BorderRadius.circular(4),
        child: LinearProgressIndicator(value: s.total == 0 ? 0 : s.written / s.total, minHeight: 8),
      ),
      const SizedBox(height: 6),
      Text('${s.arcs.length} quyển · ${_hours(words)} đọc', style: const TextStyle(color: Colors.white60, fontSize: 13)),
      const SizedBox(height: 12),
      if (running && j != null) ...[
        LinearProgressIndicator(value: j.pct > 0 ? j.pct / 100 : null, minHeight: 5),
        const SizedBox(height: 6),
        Text('${j.msg}\nĐã chạy ${fmtDuration(j.elapsed)}', style: const TextStyle(fontSize: 12, color: Colors.white70)),
        const SizedBox(height: 8),
        OutlinedButton.icon(
            onPressed: () => jobs.stop(s.dir), icon: const Icon(Icons.stop), label: const Text('Dừng (giữ phần đã viết)')),
      ] else if (s.bible == null)
        FilledButton.icon(onPressed: _plan, icon: const Icon(Icons.auto_awesome), label: const Text('Lập sổ tay bộ truyện'))
      else if (s.written < s.total) ...[
        Row(children: [
          const Text('Viết thêm'),
          const SizedBox(width: 10),
          Expanded(
            child: SegmentedButton<int>(
              showSelectedIcon: false,
              segments: [for (final n in const [1, 10, 30, 100]) ButtonSegment(value: n, label: Text('$n'))],
              selected: {writeCount},
              onSelectionChanged: (v) => setState(() => writeCount = v.first),
            ),
          ),
        ]),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: _write,
          icon: const Icon(Icons.edit_note),
          label: Text('Viết chương ${s.written + 1}–${(s.written + writeCount).clamp(1, s.total)}'),
        ),
      ] else
        const Text('Đã viết xong cả bộ', textAlign: TextAlign.center, style: TextStyle(color: Colors.greenAccent)),
      if (!running && j?.error != null) ...[
        const SizedBox(height: 8),
        SelectableText(j!.error!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
      ],
      const SizedBox(height: 10),
      Text('Người viết: $writer${_paid || s.cost > 0 ? ' · đã dùng khoảng \$${s.cost.toStringAsFixed(2)}' : ''}',
          style: const TextStyle(fontSize: 12, color: Colors.amberAccent)),
      if (s.bible != null && !running) ...[
        const SizedBox(height: 4),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton(onPressed: _plan, child: const Text('Lập lại sổ tay từ đầu')),
        ),
      ],
    ]);
  }

  Widget _videoCard() {
    final o = s.opt;
    final mins = ((o['episode_minutes'] as num?) ?? 17).toDouble();
    final from = s.nextEpisodeFrom;
    return _card('Video theo tập', [
      Text('Mỗi tập: ${mins.round()} phút', style: const TextStyle(fontSize: 13)),
      Slider(
        value: mins.clamp(10, 25),
        min: 10,
        max: 25,
        divisions: 15,
        onChanged: running ? null : (v) => setState(() => o['episode_minutes'] = v.round()),
        onChangeEnd: running ? null : (v) => s.setOption('episode_minutes', v.round()),
      ),
      DropdownButtonFormField<String>(
        initialValue: voices.containsKey(o['voice']) ? o['voice'] : 'vieneu:Thiện Minh',
        isExpanded: true,
        menuMaxHeight: 420,
        decoration: const InputDecoration(labelText: 'Giọng kể (cố định cho cả bộ)'),
        items: voices.entries
            .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value, overflow: TextOverflow.ellipsis)))
            .toList(),
        onChanged: running ? null : (v) => s.setOption('voice', v),
      ),
      const SizedBox(height: 12),
      FilledButton.icon(
        onPressed: running || s.bible == null ? null : _episode,
        icon: const Icon(Icons.movie_creation),
        label: Text(from > s.written ? 'Cần viết thêm chương để làm tập mới' : 'Tạo tập ${s.episodes.length + 1} (từ chương $from)'),
      ),
      const SizedBox(height: 4),
      const Text('Tập mới có lời nhắc tập trước ở đầu và hé lộ tập sau ở cuối; nhân vật và phong cách tranh giữ nguyên cả bộ.',
          style: TextStyle(fontSize: 12, color: Colors.white54)),
      if (s.episodes.isNotEmpty) const Divider(height: 24),
      for (final e in s.episodes.reversed)
        ListTile(
          dense: true,
          contentPadding: EdgeInsets.zero,
          leading: Icon(
              File(p.join(e['project'].toString(), 'output.mp4')).existsSync() ? Icons.check_circle : Icons.movie_outlined,
              color: File(p.join(e['project'].toString(), 'output.mp4')).existsSync() ? Colors.greenAccent : null),
          title: Text('Tập ${e['n']} · chương ${e['from']}–${e['to']}'),
          subtitle: Text('${e['minutes']} phút · ${e['created']}'),
          trailing: const Icon(Icons.chevron_right),
          onTap: () async {
            final pr = Project.tryLoad(e['project'].toString());
            if (pr == null) {
              ScaffoldMessenger.of(context)
                  .showSnackBar(const SnackBar(content: Text('Project của tập này đã bị xóa')));
              return;
            }
            await Navigator.push(context, MaterialPageRoute(builder: (_) => StoryPage(project: pr)));
            setState(_load);
          },
        ),
    ]);
  }

  Widget _webCard() {
    final sent = ((s.web['published'] as Map?) ?? {}).length;
    final cover = File(s.cover);
    return _card('Đăng lên web đọc truyện', [
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Container(
          width: 72,
          height: 104,
          decoration: BoxDecoration(color: Colors.black26, borderRadius: BorderRadius.circular(6)),
          clipBehavior: Clip.antiAlias,
          child: cover.existsSync()
              ? Image.file(cover, fit: BoxFit.cover, key: ValueKey(cover.lastModifiedSync()))
              : const Icon(Icons.image_outlined, color: Colors.white24),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(sent == 0 ? 'Chưa đăng chương nào' : 'Đã đăng $sent/${s.written} chương',
                style: const TextStyle(fontSize: 13)),
            const SizedBox(height: 6),
            OutlinedButton.icon(
              icon: const Icon(Icons.image, size: 18),
              label: const Text('Chọn ảnh bìa'),
              onPressed: () async {
                final pick = await FilePicker.pickFile(type: FileType.image, dialogTitle: 'Ảnh bìa truyện (dọc 2:3)');
                if (pick?.path == null) return;
                File(pick!.path!).copySync(s.cover);
                setState(() {});
              },
            ),
          ]),
        ),
      ]),
      SwitchListTile(
        contentPadding: EdgeInsets.zero,
        title: const Text('Tự đăng sau mỗi lượt viết'),
        value: s.opt['auto_publish'] == true,
        onChanged: running ? null : (v) => setState(() => s.setOption('auto_publish', v)),
      ),
      FilledButton.tonalIcon(
        onPressed: running || s.written == 0 ? null : _publish,
        icon: const Icon(Icons.cloud_upload),
        label: const Text('Đăng / cập nhật chương mới'),
      ),
    ]);
  }

  // ---------------- tab ----------------

  Widget _empty(String t) => Center(
      child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(t, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white38))));

  Widget _edit(Map m, String k, String label, {int min = 1, int max = 6}) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          key: ValueKey('$rev-${identityHashCode(m)}-$k'),
          initialValue: (m[k] ?? '').toString(),
          readOnly: running,
          minLines: min,
          maxLines: max,
          decoration: InputDecoration(labelText: label),
          onChanged: (v) {
            m[k] = v;
            if (identical(m, s.data['bible']) && k == 'title') s.data['title'] = v;
            _edited();
          },
        ),
      );

  Widget _bibleTab() {
    final b = s.data['bible'] as Map?;
    if (b == null) {
      return _empty(running ? 'AI đang lập sổ tay bộ truyện…' : 'Bấm "Lập sổ tay bộ truyện" để AI thiết kế thế giới và nhân vật.');
    }
    final chars = [...(b['characters'] as List), ...((b['extra_characters'] as List?) ?? [])].cast<Map>();
    return ListView(padding: const EdgeInsets.all(16), children: [
      const Text('Sửa ở đây thì các chương viết sau sẽ bám theo bản đã sửa.',
          style: TextStyle(fontSize: 12, color: Colors.white54)),
      const SizedBox(height: 12),
      _edit(b, 'title', 'Tên bộ truyện'),
      _edit(b, 'logline', 'Tóm tắt', max: 4),
      _edit(b, 'theme', 'Chủ đề'),
      _edit(b, 'setting', 'Thế giới, bối cảnh', min: 3, max: 14),
      _edit(b, 'rules', 'Quy luật thế giới / hệ thống sức mạnh', min: 3, max: 14),
      _edit(b, 'style', 'Giọng văn, ngôi kể, xưng hô', max: 6),
      _edit(b, 'ending', 'Kết cục định hướng', max: 6),
      _edit(b, 'art_style', 'Phong cách tranh minh họa (tiếng Anh)'),
      const SizedBox(height: 8),
      Text('Nhân vật (${chars.length})', style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 10),
      for (final c in chars)
        Card(
          color: const Color(0xFF262633),
          margin: const EdgeInsets.only(bottom: 10),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 0),
            child: Column(children: [
              Row(children: [
                Expanded(child: _edit(c, 'name', 'Tên')),
                const SizedBox(width: 10),
                Expanded(flex: 2, child: _edit(c, 'role', 'Vai trò')),
              ]),
              if (c.containsKey('personality')) _edit(c, 'personality', 'Tính cách', max: 3),
              if (c.containsKey('goal')) _edit(c, 'goal', 'Mong muốn', max: 3),
              if (c.containsKey('secret')) _edit(c, 'secret', 'Bí mật', max: 3),
              if (c.containsKey('arc')) _edit(c, 'arc', 'Hành trình', max: 3),
              _edit(c, 'look_en', 'Ngoại hình để vẽ (tiếng Anh)'),
            ]),
          ),
        ),
    ]);
  }

  Widget _arcsTab() {
    if (s.arcs.isEmpty) return _empty('Các quyển sẽ hiện ở đây sau khi lập sổ tay.');
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: s.arcs.length,
      itemBuilder: (_, i) {
        final a = s.arcs[i];
        final st = (a['start'] as num).toInt(), en = (a['end'] as num).toInt();
        final done = s.written >= en, doing = s.written >= st - 1 && !done;
        final beats = ((a['beats'] as List?) ?? []).cast<Map>();
        return Card(
          color: const Color(0xFF262633),
          margin: const EdgeInsets.only(bottom: 10),
          child: ExpansionTile(
            leading: Icon(done ? Icons.check_circle : doing ? Icons.edit : Icons.circle_outlined,
                color: done ? Colors.greenAccent : doing ? Theme.of(context).colorScheme.primary : Colors.white30),
            title: Text('Quyển ${i + 1}: ${a['title']}'),
            subtitle: Text('Chương $st–$en${beats.isNotEmpty ? ' · đã lập dàn ý chi tiết' : ''}',
                style: const TextStyle(fontSize: 12)),
            childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            children: [
              _edit(a, 'summary', 'Tóm tắt quyển', min: 2, max: 10),
              _edit(a, 'goal', 'Mục tiêu', max: 3),
              _edit(a, 'climax', 'Cao trào', max: 3),
              if ((a['recap'] ?? '').toString().isNotEmpty) _edit(a, 'recap', 'Đã xảy ra (AI tóm tắt sau khi viết xong)', max: 10),
              if (beats.isNotEmpty) ...[
                const Align(
                    alignment: Alignment.centerLeft,
                    child: Text('Dàn ý từng chương (sửa được với các chương chưa viết)',
                        style: TextStyle(fontWeight: FontWeight.w600))),
                const SizedBox(height: 8),
                for (final (k, b) in beats.indexed)
                  Opacity(
                    opacity: st + k <= s.written ? 0.5 : 1,
                    child: _edit(b, 'beats', 'Chương ${st + k}: ${b['title']}', max: 5),
                  ),
              ],
            ],
          ),
        );
      },
    );
  }

  Widget _chaptersTab() {
    if (chapters.isEmpty) return _empty('Chưa có chương nào. Bấm "Viết chương" ở bên trái.');
    return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SizedBox(
        width: 280,
        child: ListView.builder(
          itemCount: chapters.length,
          itemBuilder: (_, i) {
            final c = chapters[chapters.length - 1 - i]; // mới nhất ở trên
            final n = c['n'] as int;
            return ListTile(
              dense: true,
              selected: selected == n,
              title: Text('$n. ${c['title']}', maxLines: 1, overflow: TextOverflow.ellipsis),
              subtitle: Text('${c['len']} ký tự${c['edited'] == true ? ' · đã sửa' : ''}'),
              onTap: () => setState(() {
                _saveChapter();
                selected = n;
                cur = s.chapter(n);
              }),
            );
          },
        ),
      ),
      const VerticalDivider(width: 1),
      Expanded(
        child: cur == null
            ? _empty('Chọn một chương để đọc hoặc sửa')
            : ListView(
                key: ValueKey('ch-${cur!['n']}-$rev'),
                padding: const EdgeInsets.all(16),
                children: [
                  Row(children: [
                    Expanded(
                      child: Text('Chương ${cur!['n']} · quyển ${(cur!['arc'] as num? ?? 0) + 1}',
                          style: const TextStyle(color: Colors.white54)),
                    ),
                    TextButton.icon(
                      onPressed: running ? null : () => _rewind(cur!['n'] as int),
                      icon: const Icon(Icons.restart_alt, size: 18),
                      label: const Text('Viết lại từ chương này'),
                    ),
                  ]),
                  const SizedBox(height: 8),
                  _chEdit('title', 'Tên chương', 1, 2),
                  _chEdit('summary', 'Tóm tắt (AI dùng để nhớ)', 2, 5),
                  _chEdit('text', 'Nội dung', 12, 80),
                ],
              ),
      ),
    ]);
  }

  Widget _chEdit(String k, String label, int min, int max) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: TextFormField(
          initialValue: (cur![k] ?? '').toString(),
          readOnly: running,
          minLines: min,
          maxLines: max,
          style: k == 'text' ? const TextStyle(height: 1.55, fontSize: 15) : null,
          decoration: InputDecoration(labelText: label),
          onChanged: (v) {
            cur![k] = v;
            if (k == 'text') cur!['edited'] = true; // engine tóm tắt lại trước khi viết chương sau
            _saveTimer?.cancel();
            _saveTimer = Timer(const Duration(milliseconds: 700), _saveChapter);
          },
        ),
      );

  void _saveChapter() {
    final c = cur;
    if (c == null || running) return;
    final disk = s.chapter(c['n'] as int);
    if (disk == null) return;
    if (disk['text'] == c['text'] && disk['title'] == c['title'] && disk['summary'] == c['summary']) return;
    s.saveChapter(c);
    final i = chapters.indexWhere((x) => x['n'] == c['n']);
    if (i >= 0) {
      chapters[i] = {...chapters[i], 'title': c['title'], 'len': c['text'].toString().length, 'edited': c['edited'] == true};
    }
  }

  Widget _memoryTab() {
    final states = ((s.memory['states'] as Map?) ?? {}).cast<String, dynamic>();
    final threads = ((s.memory['threads'] as List?) ?? []).cast<String>();
    if (states.isEmpty && threads.isEmpty) {
      return _empty('Sau mỗi chương, AI ghi lại trạng thái nhân vật và các nút thắt chưa mở để giữ mạch truyện. '
          'Chúng sẽ hiện ở đây.');
    }
    return ListView(padding: const EdgeInsets.all(16), children: [
      const Text('Đây là những gì AI "nhớ" khi viết chương tiếp theo. Sửa hoặc xóa nếu thấy sai.',
          style: TextStyle(fontSize: 12, color: Colors.white54)),
      const SizedBox(height: 12),
      Text('Trạng thái nhân vật (${states.length})', style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 10),
      for (final e in states.entries) _edit(s.memory['states'] as Map, e.key, e.key, max: 3),
      const SizedBox(height: 8),
      Text('Nút thắt đang mở (${threads.length})', style: const TextStyle(fontWeight: FontWeight.w600)),
      const SizedBox(height: 6),
      for (final (i, t) in threads.indexed)
        ListTile(
          dense: true,
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.link, size: 18),
          title: Text(t),
          trailing: IconButton(
            tooltip: 'Bỏ (đã giải quyết)',
            icon: const Icon(Icons.close, size: 18),
            onPressed: running
                ? null
                : () => setState(() {
                      (s.memory['threads'] as List).removeAt(i);
                      _edited();
                    }),
          ),
        ),
    ]);
  }
}
