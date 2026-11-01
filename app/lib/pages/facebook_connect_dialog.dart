import 'dart:io';

import 'package:flutter/material.dart';

import '../engine.dart';
import '../main.dart';

const _devApps = 'https://developers.facebook.com/apps/';
const _explorer = 'https://developers.facebook.com/tools/explorer/';

/// Hướng dẫn + kết nối Fanpage: đổi User Token ngắn hạn sang Page Token không hết hạn.
/// Trả về {id, name, token, app_id, app_secret} của Trang được chọn.
class FacebookConnectDialog extends StatefulWidget {
  final String appId;
  final String appSecret;
  const FacebookConnectDialog({super.key, required this.appId, required this.appSecret});

  @override
  State<FacebookConnectDialog> createState() => _FacebookConnectDialogState();
}

class _FacebookConnectDialogState extends State<FacebookConnectDialog> {
  late final appId = TextEditingController(text: widget.appId);
  late final secret = TextEditingController(text: widget.appSecret);
  final token = TextEditingController();
  bool loading = false;
  String? error;
  List<Map<String, dynamic>> pages = [];
  String? selected;

  @override
  void dispose() {
    appId.dispose();
    secret.dispose();
    token.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    if (appId.text.trim().isEmpty || secret.text.trim().isEmpty || token.text.trim().isEmpty) {
      setState(() => error = 'Cần điền đủ App ID, App Secret và User Token');
      return;
    }
    setState(() {
      loading = true;
      error = null;
      pages = [];
    });
    final ev = await Engine.once([
      'fb_pages',
      '--app-id', appId.text.trim(),
      '--app-secret', secret.text.trim(),
      '--user-token', token.text.trim(),
    ], engineOverride: settings.get('engine_dir'));
    if (!mounted) return;
    setState(() {
      loading = false;
      if (ev.type == 'error') {
        error = ev.msg;
      } else {
        pages = (ev.data['pages'] as List).cast<Map<String, dynamic>>();
        selected = pages.first['id'];
      }
    });
  }

  Widget _step(String n, String title, List<Widget> body) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          CircleAvatar(radius: 12, child: Text(n, style: const TextStyle(fontSize: 12))),
          const SizedBox(width: 10),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
              const SizedBox(height: 4),
              ...body,
            ]),
          ),
        ]),
      );

  Widget _txt(String t) =>
      Padding(padding: const EdgeInsets.only(bottom: 4), child: Text(t, style: const TextStyle(fontSize: 13, color: Colors.white70)));

  Widget _link(String label, String url) => Align(
        alignment: Alignment.centerLeft,
        child: TextButton.icon(
          style: TextButton.styleFrom(padding: EdgeInsets.zero, visualDensity: VisualDensity.compact),
          onPressed: () => Process.run('explorer', [url]),
          icon: const Icon(Icons.open_in_new, size: 16),
          label: Text(label),
        ),
      );

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Kết nối Fanpage'),
      content: SizedBox(
        width: 620,
        child: SingleChildScrollView(
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, mainAxisSize: MainAxisSize.min, children: [
            _step('1', 'Tạo app trên Facebook Developers (làm 1 lần)', [
              _txt('Đăng nhập bằng tài khoản Facebook đang quản trị Fanpage → "Tạo ứng dụng" → '
                  'chọn trường hợp sử dụng "Quản lý mọi thứ trên Trang" (Manage everything on your Page) → đặt tên tùy ý.'),
              _link('Mở Facebook Developers', _devApps),
            ]),
            _step('2', 'Lấy App ID và App Secret', [
              _txt('Trong app vừa tạo: Cài đặt ứng dụng → Thông tin cơ bản → copy "ID ứng dụng" và '
                  '"Khóa bí mật của ứng dụng" (bấm Hiển thị).'),
              Row(children: [
                Expanded(child: TextField(controller: appId, decoration: const InputDecoration(labelText: 'App ID'))),
                const SizedBox(width: 10),
                Expanded(
                  child: TextField(
                      controller: secret, obscureText: true, decoration: const InputDecoration(labelText: 'App Secret')),
                ),
              ]),
            ]),
            _step('3', 'Tạo User Token trong Graph API Explorer', [
              _txt('Chọn đúng app ở ô "Meta App" → ô "User or Page" chọn "Get User Access Token" → '
                  'thêm 3 quyền: pages_show_list, pages_read_engagement, pages_manage_posts → '
                  'bấm "Generate Access Token" → đăng nhập, TICK CHỌN Fanpage của bạn, đồng ý → copy token.'),
              _link('Mở Graph API Explorer', _explorer),
              TextField(
                controller: token,
                maxLines: 2,
                decoration: const InputDecoration(labelText: 'Dán User Token vào đây'),
              ),
            ]),
            _step('4', 'Chọn Trang', [
              Align(
                alignment: Alignment.centerLeft,
                child: FilledButton.tonalIcon(
                  onPressed: loading ? null : _load,
                  icon: loading
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.search),
                  label: const Text('Lấy danh sách Trang'),
                ),
              ),
              if (error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: SelectableText(error!, style: const TextStyle(color: Colors.redAccent)),
                ),
              RadioGroup<String>(
                groupValue: selected,
                onChanged: (v) => setState(() => selected = v),
                child: Column(children: [
                  for (final pg in pages)
                    RadioListTile<String>(
                      value: pg['id'],
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      title: Text(pg['name']),
                      subtitle: Text(pg['can_post'] == true
                          ? 'ID ${pg['id']}'
                          : 'ID ${pg['id']} · ⚠ tài khoản này không có quyền đăng bài lên Trang'),
                    ),
                ]),
              ),
            ]),
            const Text(
              'Token của Trang lấy theo cách này không hết hạn. Nếu đăng xong mà người khác không thấy video, '
              'vào app trên Facebook Developers và chuyển app sang chế độ Live (đã phát hành).',
              style: TextStyle(fontSize: 12, color: Colors.white38),
            ),
          ]),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Hủy')),
        FilledButton(
          onPressed: selected == null
              ? null
              : () {
                  final pg = pages.firstWhere((e) => e['id'] == selected);
                  Navigator.pop(context, {
                    ...pg,
                    'app_id': appId.text.trim(),
                    'app_secret': secret.text.trim(),
                  });
                },
          child: const Text('Dùng Trang này'),
        ),
      ],
    );
  }
}
