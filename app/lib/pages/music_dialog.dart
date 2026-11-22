import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:media_kit/media_kit.dart';

import '../engine.dart';
import '../main.dart';

/// Chọn nhạc nền: thư viện nhạc miễn phí bản quyền (Kevin MacLeod, CC BY) có nghe thử, hoặc file trên máy.
/// Trả về đường dẫn file nhạc đã chọn.
class MusicDialog extends StatefulWidget {
  final String current;
  const MusicDialog({super.key, required this.current});

  @override
  State<MusicDialog> createState() => _MusicDialogState();
}

class _MusicDialogState extends State<MusicDialog> {
  final player = Player();
  List<Map<String, dynamic>> tracks = [];
  String? error;
  bool loading = true;
  String? playing;

  @override
  void initState() {
    super.initState();
    // lần đầu engine tải thư viện nhạc về (~95MB), các lần sau có sẵn
    Engine.once(['music'], engineOverride: settings.get('engine_dir'), timeout: const Duration(minutes: 10)).then((ev) {
      if (!mounted) return;
      setState(() {
        loading = false;
        if (ev.type == 'done') {
          tracks = (ev.data['tracks'] as List).cast<Map<String, dynamic>>();
        } else {
          error = ev.msg;
        }
      });
    });
  }

  @override
  void dispose() {
    player.dispose();
    super.dispose();
  }

  Future<void> _play(String file) async {
    if (playing == file) {
      await player.stop();
      setState(() => playing = null);
      return;
    }
    setState(() => playing = file);
    await player.open(Media(file), play: true);
  }

  @override
  Widget build(BuildContext context) {
    final moods = <String, List<Map<String, dynamic>>>{};
    for (final t in tracks) {
      moods.putIfAbsent(t['mood'].toString(), () => []).add(t);
    }
    return AlertDialog(
      title: const Text('Chọn nhạc nền'),
      content: SizedBox(
        width: 520,
        height: 520,
        child: loading
            ? const Center(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
                  CircularProgressIndicator(),
                  SizedBox(height: 12),
                  Text('Đang chuẩn bị thư viện nhạc (lần đầu tải ~95MB)...'),
                ]),
              )
            : error != null
                ? Center(child: Text('Không tải được thư viện nhạc: $error'))
                : ListView(children: [
                    const Text(
                      'Nhạc của Kevin MacLeod (incompetech.com), giấy phép CC BY 4.0: dùng được cả video kiếm tiền, '
                      'chỉ cần ghi tên tác giả — app tự thêm vào mô tả khi AI viết caption.',
                      style: TextStyle(fontSize: 12, color: Colors.white54),
                    ),
                    for (final e in moods.entries) ...[
                      Padding(
                        padding: const EdgeInsets.only(top: 14, bottom: 4),
                        child: Text(e.key, style: const TextStyle(fontWeight: FontWeight.w600, color: Colors.amberAccent)),
                      ),
                      for (final t in e.value)
                        ListTile(
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                          selected: t['file'] == widget.current,
                          leading: IconButton(
                            icon: Icon(playing == t['file'] ? Icons.stop_circle : Icons.play_circle),
                            tooltip: 'Nghe thử',
                            onPressed: () => _play(t['file'].toString()),
                          ),
                          title: Text(t['title'].toString()),
                          trailing: FilledButton.tonal(
                            onPressed: () => Navigator.pop(context, t['file'].toString()),
                            child: const Text('Chọn'),
                          ),
                        ),
                    ],
                  ]),
      ),
      actions: [
        TextButton.icon(
          icon: const Icon(Icons.folder_open),
          label: const Text('Chọn file trên máy...'),
          onPressed: () async {
            final f = await FilePicker.pickFile(type: FileType.audio, dialogTitle: 'Chọn nhạc nền');
            if (f?.path != null && context.mounted) Navigator.pop(context, f!.path);
          },
        ),
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Đóng')),
      ],
    );
  }
}
