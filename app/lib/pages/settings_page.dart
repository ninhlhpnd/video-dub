import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

import '../engine.dart';
import '../main.dart';
import 'facebook_connect_dialog.dart';

const whisperModels = {
  'large-v3-turbo': 'large-v3-turbo (khuyên dùng: nhanh, chính xác)',
  'large-v3': 'large-v3 (chính xác nhất, chậm hơn)',
  'medium': 'medium (nhẹ hơn)',
  'small': 'small (máy yếu)',
};

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key});

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  final ctrls = <String, TextEditingController>{};
  String whisper = settings.get('whisper_model', 'large-v3-turbo');
  String sysInfo = 'Đang kiểm tra...';
  bool showKey = false;

  TextEditingController c(String key, [String def = '']) =>
      ctrls.putIfAbsent(key, () => TextEditingController(text: settings.get(key, def)));

  @override
  void initState() {
    super.initState();
    c('workspace').text = settings.workspace;
    Engine.once(['info'], engineOverride: settings.get('engine_dir')).then((ev) {
      if (!mounted) return;
      setState(() => sysInfo = ev.type == 'error'
          ? 'Lỗi engine: ${ev.msg}'
          : ev.data['cuda'] == true
              ? 'GPU: ${ev.data['gpu']} — chạy nhanh'
              : 'Không có GPU NVIDIA — chạy bằng CPU (chậm hơn)');
    });
  }

  @override
  void dispose() {
    for (final x in ctrls.values) {
      x.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    for (final e in ctrls.entries) {
      settings.data[e.key] = e.value.text.trim();
    }
    settings.data['whisper_model'] = whisper;
    await settings.save();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Đã lưu cài đặt')));
      Navigator.pop(context);
    }
  }

  @override
  Widget build(BuildContext context) {
    final engineDir = Engine.findEngineDir(settings.get('engine_dir'));
    final ytToken = engineDir == null ? null : File(p.join(p.dirname(engineDir), 'tools', 'youtube_token.json'));
    return Scaffold(
      appBar: AppBar(
        title: const Text('Cài đặt'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: FilledButton.icon(onPressed: _save, icon: const Icon(Icons.save), label: const Text('Lưu')),
          ),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: ListView(
            padding: const EdgeInsets.all(24),
            children: [
              _section('Chung', [
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.memory),
                  title: Text(sysInfo),
                  subtitle: Text('Engine: ${engineDir ?? "KHÔNG TÌM THẤY"}'),
                ),
                _field('workspace', 'Thư mục lưu project', suffix: IconButton(
                  icon: const Icon(Icons.folder_open),
                  onPressed: () async {
                    final d = await FilePicker.getDirectoryPath();
                    if (d != null) setState(() => c('workspace').text = d);
                  },
                )),
              ]),
              _section('Dịch (Google Gemini)', [
                _hint('Lấy key miễn phí tại aistudio.google.com → Get API key.'),
                _field('gemini_api_key', 'Gemini API key',
                    obscure: !showKey,
                    suffix: IconButton(
                      icon: Icon(showKey ? Icons.visibility_off : Icons.visibility),
                      onPressed: () => setState(() => showKey = !showKey),
                    )),
                _field('gemini_model', 'Model Gemini', def: 'gemini-flash-latest'),
              ]),
              _section('Nhận dạng giọng nói (Whisper)', [
                DropdownButtonFormField<String>(
                  initialValue: whisperModels.containsKey(whisper) ? whisper : 'large-v3-turbo',
                  decoration: const InputDecoration(labelText: 'Model'),
                  items: whisperModels.entries
                      .map((e) => DropdownMenuItem(value: e.key, child: Text(e.value)))
                      .toList(),
                  onChanged: (v) => setState(() => whisper = v!),
                ),
              ]),
              _section('YouTube', [
                _hint('Google Cloud Console → tạo project → bật "YouTube Data API v3" → OAuth consent screen '
                    '(thêm email của bạn vào Test users) → Credentials → OAuth client ID loại "Desktop app" → tải file JSON.\n'
                    'Lưu ý: khi app chưa được Google duyệt (audit), video đăng qua API sẽ bị giữ ở chế độ riêng tư.'),
                _field('youtube_client_secret', 'File client_secret.json', suffix: IconButton(
                  icon: const Icon(Icons.file_open),
                  onPressed: () async {
                    final f = await FilePicker.pickFile(type: FileType.custom, allowedExtensions: ['json']);
                    if (f?.path != null) setState(() => c('youtube_client_secret').text = f!.path!);
                  },
                )),
                if (ytToken != null && ytToken.existsSync())
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton.icon(
                      icon: const Icon(Icons.logout),
                      label: const Text('Đã đăng nhập YouTube — đăng xuất'),
                      onPressed: () => setState(() => ytToken.deleteSync()),
                    ),
                  ),
              ]),
              _section('Facebook (Trang/Fanpage)', [
                _hint('Chỉ đăng được lên Trang, không đăng lên trang cá nhân. '
                    'Cần tạo 1 app (miễn phí) trên developers.facebook.com để lấy App ID và App Secret.'),
                _field('facebook_app_id', 'App ID'),
                _field('facebook_app_secret', 'App Secret', obscure: true),
                Row(children: [
                  Icon(c('facebook_page_id').text.isEmpty ? Icons.link_off : Icons.check_circle,
                      color: c('facebook_page_id').text.isEmpty ? Colors.white38 : Colors.greenAccent),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(c('facebook_page_id').text.isEmpty
                        ? 'Chưa kết nối Trang nào'
                        : 'Đã kết nối: ${settings.get('facebook_page_name', c('facebook_page_id').text)}'),
                  ),
                  FilledButton.tonalIcon(
                    onPressed: _connectFacebook,
                    icon: const Icon(Icons.facebook),
                    label: Text(c('facebook_page_id').text.isEmpty ? 'Kết nối Fanpage' : 'Đổi Trang'),
                  ),
                ]),
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: const Text('Nhập tay (nâng cao)', style: TextStyle(fontSize: 13)),
                  children: [
                    _field('facebook_page_id', 'Page ID'),
                    const SizedBox(height: 12),
                    _field('facebook_page_token', 'Page Access Token', obscure: true),
                    const SizedBox(height: 12),
                    _field('graph_version', 'Graph API version', def: 'v23.0'),
                  ],
                ),
              ]),
              _section('Nâng cao', [
                _field('engine_dir', 'Thư mục engine (để trống = tự tìm)'),
              ]),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _connectFacebook() async {
    // lưu version trước để engine dùng đúng
    settings.data['graph_version'] = c('graph_version', 'v23.0').text.trim();
    await settings.save();
    if (!mounted) return;
    final page = await showDialog<Map<String, dynamic>>(
      context: context,
      barrierDismissible: false,
      builder: (_) => FacebookConnectDialog(
          appId: c('facebook_app_id').text.trim(), appSecret: c('facebook_app_secret').text.trim()),
    );
    if (page == null) return;
    setState(() {
      c('facebook_app_id').text = page['app_id'];
      c('facebook_app_secret').text = page['app_secret'];
      c('facebook_page_id').text = page['id'];
      c('facebook_page_token').text = page['token'];
    });
    settings.data['facebook_page_name'] = page['name'];
    settings.data['facebook_app_id'] = page['app_id'];
    settings.data['facebook_app_secret'] = page['app_secret'];
    settings.data['facebook_page_id'] = page['id'];
    settings.data['facebook_page_token'] = page['token'];
    await settings.save();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('Đã kết nối Trang "${page['name']}"')));
    }
  }

  Widget _section(String title, List<Widget> children) => Padding(
        padding: const EdgeInsets.only(bottom: 18),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(title, style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 12),
              for (final w in children) Padding(padding: const EdgeInsets.only(bottom: 12), child: w),
            ]),
          ),
        ),
      );

  Widget _hint(String t) => Text(t, style: const TextStyle(color: Colors.white54, fontSize: 13));

  Widget _field(String key, String label, {String def = '', bool obscure = false, Widget? suffix}) => TextField(
        controller: c(key, def),
        obscureText: obscure,
        decoration: InputDecoration(labelText: label, suffixIcon: suffix),
      );
}
