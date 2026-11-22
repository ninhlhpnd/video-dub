import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

import '../models.dart';

/// Danh sách chữ Trung tìm thấy trên video: sửa bản dịch và chọn cách xử lý từng dòng.
class OverlayDialog extends StatefulWidget {
  final Project project;
  const OverlayDialog({super.key, required this.project});

  @override
  State<OverlayDialog> createState() => _OverlayDialogState();
}

class _OverlayDialogState extends State<OverlayDialog> {
  late final items = widget.project.loadOverlays();
  late final ctrls = [for (final it in items) TextEditingController(text: (it['vi'] ?? '').toString())];

  @override
  void dispose() {
    for (final c in ctrls) {
      c.dispose();
    }
    super.dispose();
  }

  void _setAll(String kind, String action) => setState(() {
        for (final it in items) {
          if (it['kind'] == kind) it['action'] = action;
        }
      });

  void _save() {
    for (var i = 0; i < items.length; i++) {
      items[i]['vi'] = ctrls[i].text.trim();
    }
    widget.project.saveOverlays(items);
    Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) {
    final thumb = File(p.join(widget.project.dir, 'thumb.jpg'));
    return AlertDialog(
      title: Text('Chữ trên video (${items.length} dòng)'),
      content: SizedBox(
        width: 860,
        height: 560,
        child: items.isEmpty
            ? const Center(child: Text('Không tìm thấy chữ Trung nào trên video.'))
            : Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Wrap(spacing: 8, runSpacing: 6, crossAxisAlignment: WrapCrossAlignment.center, children: [
                  const Text('Đặt nhanh:', style: TextStyle(color: Colors.white60)),
                  ActionChip(label: const Text('Phụ đề gốc → Xóa'), onPressed: () => _setAll('sub', 'remove')),
                  ActionChip(label: const Text('Tiêu đề/nhãn → Dịch'), onPressed: () => _setAll('text', 'translate')),
                  ActionChip(label: const Text('Tiêu đề/nhãn → Tên kênh'), onPressed: () => _setAll('text', 'channel')),
                  ActionChip(label: const Text('Tiêu đề/nhãn → Xóa'), onPressed: () => _setAll('text', 'remove')),
                ]),
                const SizedBox(height: 10),
                Expanded(
                  child: ListView.separated(
                    itemCount: items.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (_, i) => _row(i, thumb),
                  ),
                ),
              ]),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Hủy')),
        FilledButton.icon(onPressed: _save, icon: const Icon(Icons.save), label: const Text('Lưu')),
      ],
    );
  }

  Widget _row(int i, File thumb) {
    final it = items[i];
    final x = (it['x'] as num).toDouble(), y = (it['y'] as num).toDouble();
    final w = (it['w'] as num).toDouble(), h = (it['h'] as num).toDouble();
    final action = (it['action'] ?? 'translate').toString();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        // ảnh thu nhỏ khung hình, đánh dấu vị trí dòng chữ
        SizedBox(
          width: 54,
          height: 96,
          child: Stack(fit: StackFit.expand, children: [
            thumb.existsSync() ? Image.file(thumb, fit: BoxFit.fill) : Container(color: Colors.black26),
            LayoutBuilder(
              builder: (_, c) => Stack(children: [
                Positioned(
                  left: x * c.maxWidth,
                  top: y * c.maxHeight,
                  width: (w * c.maxWidth).clamp(2, c.maxWidth),
                  height: (h * c.maxHeight).clamp(2, c.maxHeight),
                  child: Container(decoration: BoxDecoration(border: Border.all(color: Colors.redAccent, width: 1.5))),
                ),
              ]),
            ),
          ]),
        ),
        const SizedBox(width: 12),
        SizedBox(
          width: 90,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${fmtTime(it['start'] as num)}\n${fmtTime(it['end'] as num)}',
                style: const TextStyle(fontSize: 12, color: Colors.white60)),
            const SizedBox(height: 4),
            Text(it['kind'] == 'sub' ? 'Phụ đề' : 'Tiêu đề/nhãn',
                style: TextStyle(fontSize: 11, color: it['kind'] == 'sub' ? Colors.lightBlueAccent : Colors.amberAccent)),
          ]),
        ),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            SelectableText(it['zh'].toString(), style: const TextStyle(fontSize: 15)),
            const SizedBox(height: 6),
            TextField(
              controller: ctrls[i],
              enabled: action == 'translate',
              decoration: InputDecoration(
                hintText: action == 'translate' ? 'Chữ tiếng Việt thay vào' : overlayActions[action],
              ),
            ),
          ]),
        ),
        const SizedBox(width: 12),
        SizedBox(
          width: 190,
          child: DropdownButtonFormField<String>(
            initialValue: overlayActions.containsKey(action) ? action : 'translate',
            isExpanded: true,
            items: overlayActions.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
            onChanged: (v) => setState(() => it['action'] = v),
          ),
        ),
      ]),
    );
  }
}
