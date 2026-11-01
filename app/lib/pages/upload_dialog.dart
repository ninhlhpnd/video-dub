import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../engine.dart';
import '../main.dart';
import '../models.dart';

/// Tải video lên YouTube / Trang Facebook. Hẹn giờ do chính nền tảng thực hiện nên không cần để máy bật.
class UploadDialog extends StatefulWidget {
  final Project project;
  const UploadDialog({super.key, required this.project});

  @override
  State<UploadDialog> createState() => _UploadDialogState();
}

class _UploadDialogState extends State<UploadDialog> {
  late final title = TextEditingController(text: widget.project.name);
  final desc = TextEditingController();
  final tags = TextEditingController();
  bool yt = settings.get('youtube_client_secret').isNotEmpty;
  bool fb = settings.get('facebook_page_id').isNotEmpty;
  bool schedule = true;
  String privacy = 'public';
  DateTime when = DateTime.now().add(const Duration(hours: 1)).copyWith(minute: 0, second: 0, millisecond: 0);

  bool busy = false;
  final status = <String, String>{};
  final pct = <String, double>{};
  final engine = Engine();

  @override
  void dispose() {
    title.dispose();
    desc.dispose();
    tags.dispose();
    super.dispose();
  }

  Future<void> _pickWhen() async {
    final d = await showDatePicker(
      context: context,
      initialDate: when,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 180)),
    );
    if (d == null || !mounted) return;
    final t = await showTimePicker(context: context, initialTime: TimeOfDay.fromDateTime(when));
    if (t == null) return;
    setState(() => when = DateTime(d.year, d.month, d.day, t.hour, t.minute));
  }

  Future<void> _upload() async {
    if (title.text.trim().isEmpty) return;
    if (schedule && when.isBefore(DateTime.now().add(const Duration(minutes: 15)))) {
      setState(() => status['_'] = 'Giờ hẹn phải cách hiện tại ít nhất 15 phút');
      return;
    }
    setState(() {
      busy = true;
      status.clear();
    });
    for (final platform in [if (yt) 'youtube', if (fb) 'facebook']) {
      status[platform] = 'Đang chuẩn bị...';
      final args = [
        platform,
        '--video', widget.project.output,
        '--title', title.text.trim(),
        '--description', desc.text.trim(),
        '--tags', tags.text.trim(),
        '--privacy', privacy,
        if (schedule) ...['--publish-at', when.toIso8601String()],
      ];
      await for (final ev in engine.run(args, engineOverride: settings.get('engine_dir'))) {
        if (ev.type == 'progress') {
          pct[platform] = ev.pct;
          status[platform] = ev.msg;
        } else if (ev.type == 'log') {
          status[platform] = ev.msg;
        } else if (ev.type == 'error') {
          status[platform] = '✖ ${ev.msg}';
        } else if (ev.type == 'done') {
          final url = ev.data['url'].toString();
          status[platform] = '✔ $url';
          widget.project.addUpload({
            'platform': platform,
            'url': url,
            'title': title.text.trim(),
            'publish_at': schedule ? when.toIso8601String() : '',
            'uploaded': DateTime.now().toIso8601String(),
          });
        }
        if (mounted) setState(() {});
      }
    }
    if (mounted) setState(() => busy = false);
  }

  String _fmt(DateTime d) =>
      '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}  '
      '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')}/${d.year}';

  @override
  Widget build(BuildContext context) {
    final history = widget.project.uploads;
    return AlertDialog(
      title: const Text('Đăng video'),
      content: SizedBox(
        width: 560,
        child: SingleChildScrollView(
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
            Row(children: [
              Expanded(
                child: CheckboxListTile(
                  value: yt,
                  title: const Text('YouTube'),
                  subtitle: settings.get('youtube_client_secret').isEmpty ? const Text('Chưa cài đặt') : null,
                  onChanged: busy || settings.get('youtube_client_secret').isEmpty ? null : (v) => setState(() => yt = v!),
                ),
              ),
              Expanded(
                child: CheckboxListTile(
                  value: fb,
                  title: const Text('Trang Facebook'),
                  subtitle: settings.get('facebook_page_id').isEmpty ? const Text('Chưa cài đặt') : null,
                  onChanged: busy || settings.get('facebook_page_id').isEmpty ? null : (v) => setState(() => fb = v!),
                ),
              ),
            ]),
            const SizedBox(height: 8),
            TextField(
              controller: title,
              enabled: !busy,
              maxLength: 100,
              decoration: const InputDecoration(labelText: 'Tiêu đề'),
            ),
            TextField(
              controller: desc,
              enabled: !busy,
              minLines: 3,
              maxLines: 6,
              decoration: const InputDecoration(labelText: 'Mô tả'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: tags,
              enabled: !busy,
              decoration: const InputDecoration(labelText: 'Tag YouTube (cách nhau dấu phẩy)'),
            ),
            const SizedBox(height: 12),
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment(value: true, icon: Icon(Icons.schedule), label: Text('Hẹn giờ đăng')),
                ButtonSegment(value: false, icon: Icon(Icons.send), label: Text('Đăng ngay')),
              ],
              selected: {schedule},
              onSelectionChanged: busy ? null : (s) => setState(() => schedule = s.first),
            ),
            const SizedBox(height: 12),
            if (schedule)
              OutlinedButton.icon(
                onPressed: busy ? null : _pickWhen,
                icon: const Icon(Icons.event),
                label: Text('Công bố lúc ${_fmt(when)}'),
              )
            else if (yt)
              DropdownButtonFormField<String>(
                initialValue: privacy,
                decoration: const InputDecoration(labelText: 'Chế độ YouTube'),
                items: const [
                  DropdownMenuItem(value: 'public', child: Text('Công khai')),
                  DropdownMenuItem(value: 'unlisted', child: Text('Không công khai (có link mới xem được)')),
                  DropdownMenuItem(value: 'private', child: Text('Riêng tư')),
                ],
                onChanged: busy ? null : (v) => setState(() => privacy = v!),
              ),
            if (schedule)
              const Padding(
                padding: EdgeInsets.only(top: 6),
                child: Text('Video được tải lên ngay, YouTube/Facebook tự công bố đúng giờ — không cần để máy bật.',
                    style: TextStyle(fontSize: 12, color: Colors.white54)),
              ),
            for (final e in status.entries) ...[
              const SizedBox(height: 12),
              if (e.key != '_') Text(stepNames[e.key] ?? e.key, style: const TextStyle(fontWeight: FontWeight.w600)),
              if (busy && pct.containsKey(e.key) && !e.value.startsWith('✔') && !e.value.startsWith('✖'))
                LinearProgressIndicator(value: pct[e.key]! / 100),
              SelectableText(e.value,
                  style: TextStyle(
                      fontSize: 13,
                      color: e.value.startsWith('✖') || e.key == '_'
                          ? Colors.redAccent
                          : e.value.startsWith('✔')
                              ? Colors.greenAccent
                              : Colors.white70)),
            ],
            if (history.isNotEmpty) ...[
              const Divider(height: 28),
              const Text('Đã đăng trước đó', style: TextStyle(fontWeight: FontWeight.w600)),
              for (final h in history.reversed)
                ListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(h['platform'] == 'youtube' ? Icons.smart_display : Icons.facebook),
                  title: SelectableText(h['url'].toString()),
                  subtitle: Text((h['publish_at'] ?? '').toString().isEmpty
                      ? 'Đăng ngay'
                      : 'Hẹn: ${_fmt(DateTime.parse(h['publish_at']))}'),
                  trailing: IconButton(
                    icon: const Icon(Icons.copy, size: 18),
                    onPressed: () => Clipboard.setData(ClipboardData(text: h['url'].toString())),
                  ),
                ),
            ],
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: busy ? engine.stop : () => Navigator.pop(context), child: Text(busy ? 'Hủy' : 'Đóng')),
        FilledButton.icon(
          onPressed: busy || (!yt && !fb) ? null : _upload,
          icon: const Icon(Icons.cloud_upload),
          label: Text(schedule ? 'Tải lên & hẹn giờ' : 'Đăng ngay'),
        ),
      ],
    );
  }
}
