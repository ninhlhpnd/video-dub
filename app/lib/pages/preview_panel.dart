import 'dart:async';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart';
import 'package:path/path.dart' as p;

import '../models.dart';

const _gap = 0.05; // giống GAP trong engine/vdub/tts.py

/// Xem trước kiểu CapCut: phát video gốc + từng câu lồng tiếng đúng thời điểm + nền gốc đã tách + nhạc nền,
/// vẽ phụ đề lên hình, và thanh thời gian cho kéo từng câu để đổi giây bắt đầu — không cần xuất video.
class PreviewPanel extends StatefulWidget {
  final Project project;
  final List<Map<String, dynamic>> segs;

  /// Người dùng kéo câu trên thanh thời gian tới giây [start].
  final void Function(Map<String, dynamic> seg, double start) onMove;

  const PreviewPanel({super.key, required this.project, required this.segs, required this.onMove});

  @override
  State<PreviewPanel> createState() => _PreviewPanelState();
}

class _Slot {
  final Map<String, dynamic> seg;
  final double start, end; // thời điểm giọng đọc thực sự phát (câu lố giờ đẩy câu sau lùi lại, như lúc xuất)
  final File? clip;
  _Slot(this.seg, this.start, this.end, this.clip);
}

class _PreviewPanelState extends State<PreviewPanel> {
  final video = Player();
  late final vc = VideoController(video);
  final bg = Player();
  final voice = Player();
  final music = Player();
  final subs = <StreamSubscription>[];
  final tlScroll = ScrollController();

  Project get pr => widget.project;
  double pos = 0;
  bool playing = false;
  bool useBg = false, useMusic = false;
  int? voiceId; // câu đang phát giọng
  double zoom = 0; // px/giây; 0 = vừa khung
  DateTime _lastSync = DateTime.now();

  @override
  void initState() {
    super.initState();
    _open();
  }

  Future<void> _open() async {
    final o = pr.options;
    double vol(String k, double d) => (((o[k] as num?)?.toDouble() ?? d) * 100).clamp(0, 100).toDouble();
    await video.open(Media(pr.video), play: false);
    final bgFile = File(p.join(pr.dir, 'background.wav'));
    useBg = wantsSeparate(o) && bgFile.existsSync();
    if (useBg) {
      await bg.open(Media(bgFile.path), play: false);
      await bg.setVolume(vol('bg_volume', 1.0));
      await video.setVolume(0);
    } else {
      // review không tách nền: giữ tiếng gốc nhỏ; lồng tiếng không giữ nền: tắt tiếng gốc
      await video.setVolume(pr.isReview ? vol('orig_volume', 0.3) : 0);
    }
    final m = (o['music'] ?? '').toString();
    useMusic = m.isNotEmpty && File(m).existsSync();
    if (useMusic) {
      await music.open(Media(m), play: false);
      await music.setVolume(vol('music_volume', 0.15));
    }
    await voice.setVolume(vol('voice_volume', 1.0));
    subs.add(video.stream.position.listen(_onPos));
    subs.add(video.stream.playing.listen((v) => setState(() => playing = v)));
    subs.add(video.stream.completed.listen((done) {
      if (done) _pauseAll();
    }));
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    for (final s in subs) {
      s.cancel();
    }
    for (final pl in [video, bg, voice, music]) {
      pl.dispose();
    }
    tlScroll.dispose();
    super.dispose();
  }

  /// Lịch phát giọng: giống engine (assemble) — câu bắt đầu ở start, nếu câu trước chưa đọc xong thì lùi lại.
  List<_Slot> get _slots {
    final out = <_Slot>[];
    var cursor = 0.0;
    for (final s in widget.segs) {
      final text = (s['vi'] ?? '').toString().trim();
      if (text.isEmpty) continue;
      final id = s['id'] as int;
      final f = File(p.join(pr.dir, 'tts', '${id.toString().padLeft(4, '0')}.mp3'));
      final st = (s['start'] as num).toDouble();
      final ttsDur = (s['tts_dur'] as num?)?.toDouble() ?? 0;
      final has = f.existsSync() && ttsDur > 0;
      final start = has ? math.max(st, cursor) : st;
      final len = has ? ttsDur : math.max(0.5, (s['end'] as num).toDouble() - st);
      out.add(_Slot(s, start, start + len, has ? f : null));
      if (has) cursor = start + len + _gap;
    }
    return out;
  }

  void _onPos(Duration d) {
    final t = d.inMilliseconds / 1000;
    if (!mounted) return;
    setState(() => pos = t);
    if (!playing) return;
    // giữ nền/nhạc khớp với hình
    if (DateTime.now().difference(_lastSync).inMilliseconds > 2000) {
      _lastSync = DateTime.now();
      if (useBg && (bg.state.position.inMilliseconds / 1000 - t).abs() > 0.25) bg.seek(d);
    }
    // bật câu lồng tiếng tương ứng
    final slot = _slots.where((s) => s.clip != null && t >= s.start && t < s.end - 0.05).firstOrNull;
    final id = slot?.seg['id'] as int?;
    if (id != null && id != voiceId) {
      voiceId = id;
      final off = t - slot!.start;
      voice.open(Media(slot.clip!.path), play: true).then((_) {
        if (off > 0.2) voice.seek(Duration(milliseconds: (off * 1000).round()));
      });
    } else if (id == null && voiceId != null) {
      voiceId = null;
    }
    _follow(t);
  }

  void _follow(double t) {
    if (zoom == 0 || !tlScroll.hasClients) return;
    final x = t * zoom;
    final view = tlScroll.position.viewportDimension;
    if (x < tlScroll.offset || x > tlScroll.offset + view - 40) {
      tlScroll.jumpTo((x - 40).clamp(0, tlScroll.position.maxScrollExtent));
    }
  }

  Future<void> _pauseAll() async {
    await Future.wait([video.pause(), bg.pause(), music.pause(), voice.pause()]);
    voiceId = null;
  }

  Future<void> _toggle() async {
    if (playing) return _pauseAll();
    if (pos >= pr.duration - 0.2) await _seek(0);
    final d = Duration(milliseconds: (pos * 1000).round());
    if (useBg) await bg.seek(d);
    if (useMusic) await music.seek(d);
    await video.play();
    if (useBg) bg.play();
    if (useMusic) music.play();
  }

  Future<void> _seek(double t) async {
    t = t.clamp(0, pr.duration);
    final d = Duration(milliseconds: (t * 1000).round());
    setState(() => pos = t);
    voiceId = null;
    await voice.stop();
    await video.seek(d);
    if (useBg) await bg.seek(d);
    if (useMusic) await music.seek(d);
  }

  @override
  Widget build(BuildContext context) {
    final slots = _slots;
    final cur = slots.where((s) => pos >= s.start && pos < s.end).firstOrNull;
    final ratio = pr.meta['width'] / pr.meta['height'];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(10),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Expanded(
            child: Center(
              child: AspectRatio(
                aspectRatio: (ratio as num).toDouble(),
                child: LayoutBuilder(builder: (_, c) => Stack(fit: StackFit.expand, children: [
                      Video(controller: vc, controls: NoVideoControls, fill: Colors.black),
                      ..._overlays(c.maxWidth, c.maxHeight, cur),
                    ])),
              ),
            ),
          ),
          const SizedBox(height: 6),
          _controls(),
          _timeline(slots),
        ]),
      ),
    );
  }

  /// Phụ đề và dải che vẽ đè lên hình (gần giống video xuất ra).
  List<Widget> _overlays(double w, double h, _Slot? cur) {
    final o = pr.options;
    final text = (cur?.seg['vi'] ?? '').toString();
    final cover = o['cover_subs'] == true &&
        ((o['cover_bottom'] as num?) ?? 0) > ((o['cover_top'] as num?) ?? 0);
    final fs = math.max(10.0, math.min(w, h) * 0.05);
    final out = <Widget>[];
    if (cover) {
      final top = (o['cover_top'] as num).toDouble() * h, bottom = (o['cover_bottom'] as num).toDouble() * h;
      final color = Color(int.parse('FF${(o['cover_color'] ?? 'FFD400').toString().replaceAll('#', '')}', radix: 16));
      final tc = Color(int.parse('FF${(o['cover_text_color'] ?? '000000').toString().replaceAll('#', '')}', radix: 16));
      out.add(Positioned(
        left: 0,
        right: 0,
        top: top,
        height: bottom - top,
        child: Container(
          color: color,
          alignment: Alignment.center,
          padding: EdgeInsets.symmetric(horizontal: w * 0.05),
          child: o['burn_subs'] != false && text.isNotEmpty
              ? Text(text,
                  textAlign: TextAlign.center,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: tc, fontWeight: FontWeight.bold, fontSize: math.min(fs, (bottom - top) / 2.6)))
              : null,
        ),
      ));
    } else if (o['burn_subs'] != false && text.isNotEmpty) {
      out.add(Positioned(
        left: w * 0.06,
        right: w * 0.06,
        bottom: h * 0.06,
        child: Text(text,
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: fs, shadows: const [
              Shadow(blurRadius: 3, color: Colors.black),
              Shadow(offset: Offset(1, 1), blurRadius: 2, color: Colors.black),
            ])),
      ));
    }
    final channel = (o['channel_name'] ?? '').toString().trim();
    if (o['channel_watermark'] == true && channel.isNotEmpty) {
      final pos = (o['channel_pos'] ?? 'top-right').toString();
      out.add(Positioned(
        top: pos.startsWith('top') ? h * 0.035 : null,
        bottom: pos.startsWith('bottom') ? h * 0.035 : null,
        left: pos.endsWith('left') ? w * 0.04 : null,
        right: pos.endsWith('right') ? w * 0.04 : null,
        child: Text(channel,
            style: TextStyle(color: Colors.white.withValues(alpha: 0.75), fontWeight: FontWeight.bold,
                fontSize: math.min(w, h) * 0.04, shadows: const [Shadow(blurRadius: 2, color: Colors.black)])),
      ));
    }
    return out;
  }

  Widget _controls() {
    return Row(children: [
      IconButton.filledTonal(
        onPressed: _toggle,
        icon: Icon(playing ? Icons.pause : Icons.play_arrow),
        tooltip: playing ? 'Tạm dừng' : 'Phát xem trước',
      ),
      const SizedBox(width: 8),
      Text('${fmtTime(pos)} / ${fmtTime(pr.duration)}',
          style: const TextStyle(fontFeatures: [FontFeature.tabularFigures()], fontSize: 13)),
      const Spacer(),
      const Icon(Icons.zoom_out, size: 18, color: Colors.white54),
      SizedBox(
        width: 140,
        child: Slider(
          value: zoom,
          min: 0,
          max: 200,
          onChanged: (v) => setState(() => zoom = v < 8 ? 0 : v),
        ),
      ),
      const Icon(Icons.zoom_in, size: 18, color: Colors.white54),
    ]);
  }

  Widget _timeline(List<_Slot> slots) {
    return LayoutBuilder(builder: (_, c) {
      final dur = math.max(0.1, pr.duration);
      final pps = zoom == 0 ? (c.maxWidth - 2) / dur : zoom;
      final width = math.max(c.maxWidth, dur * pps + 2);
      void seekAt(double dx) => _seek(dx / pps);
      return SizedBox(
        height: 92,
        child: Scrollbar(
          controller: tlScroll,
          child: SingleChildScrollView(
            controller: tlScroll,
            scrollDirection: Axis.horizontal,
            child: SizedBox(
              width: width,
              child: Stack(children: [
                // thước thời gian: bấm/kéo để tua
                Positioned(
                  left: 0,
                  right: 0,
                  top: 0,
                  height: 22,
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTapDown: (e) => seekAt(e.localPosition.dx),
                    onHorizontalDragUpdate: (e) => seekAt(e.localPosition.dx),
                    child: CustomPaint(painter: _Ruler(dur, pps)),
                  ),
                ),
                // nền rãnh lời
                Positioned(
                  left: 0,
                  right: 0,
                  top: 28,
                  height: 52,
                  child: GestureDetector(
                    onTapDown: (e) => seekAt(e.localPosition.dx),
                    child: Container(
                        decoration: BoxDecoration(color: Colors.white10, borderRadius: BorderRadius.circular(6))),
                  ),
                ),
                for (final s in slots) _block(s, pps),
                // đầu phát
                Positioned(
                  left: pos * pps - 1,
                  top: 0,
                  bottom: 0,
                  width: 2,
                  child: IgnorePointer(child: Container(color: Colors.redAccent)),
                ),
              ]),
            ),
          ),
        ),
      );
    });
  }

  Widget _block(_Slot s, double pps) {
    final over = s.seg['fit'] == 'over';
    final pushed = s.start - (s.seg['start'] as num).toDouble() > 0.15; // bị câu trước đẩy lùi
    double? dragFrom;
    var acc = 0.0;
    return Positioned(
      left: s.start * pps,
      top: 30,
      width: math.max(8, (s.end - s.start) * pps),
      height: 48,
      child: Tooltip(
        message: '${fmtTime(s.start)} → ${fmtTime(s.end)}\n${s.seg['vi']}\n(kéo để đổi giây bắt đầu)',
        waitDuration: const Duration(milliseconds: 600),
        child: MouseRegion(
          cursor: SystemMouseCursors.resizeLeftRight,
          child: GestureDetector(
            onTap: () => _seek(s.start),
            onHorizontalDragStart: (_) {
              dragFrom = (s.seg['start'] as num).toDouble();
              acc = 0;
            },
            onHorizontalDragUpdate: (e) {
              acc += e.delta.dx;
              final t = ((dragFrom ?? 0) + acc / pps).clamp(0.0, math.max(0.0, pr.duration - 0.3));
              widget.onMove(s.seg, (t * 10).round() / 10);
              setState(() {});
            },
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 3),
              decoration: BoxDecoration(
                color: (over ? Colors.deepOrange : const Color(0xFF6C4DF6)).withValues(alpha: 0.85),
                borderRadius: BorderRadius.circular(5),
                border: Border.all(color: pushed ? Colors.amber : Colors.white24, width: pushed ? 1.5 : 1),
              ),
              child: Text(
                (s.seg['vi'] ?? '').toString(),
                maxLines: 2,
                overflow: TextOverflow.clip,
                style: const TextStyle(fontSize: 11, height: 1.2, color: Colors.white),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Ruler extends CustomPainter {
  final double dur, pps;
  _Ruler(this.dur, this.pps);

  @override
  void paint(Canvas canvas, Size size) {
    final line = Paint()..color = Colors.white30;
    // khoảng chia: đủ thưa để chữ không đè nhau
    final step = [1, 2, 5, 10, 15, 30, 60, 120, 300].firstWhere((s) => s * pps >= 48, orElse: () => 600);
    for (var t = 0; t <= dur; t += step) {
      final x = t * pps;
      canvas.drawLine(Offset(x, size.height - 8), Offset(x, size.height), line);
      final tp = TextPainter(
        text: TextSpan(text: fmtTime(t, ms: false), style: const TextStyle(fontSize: 10, color: Colors.white54)),
        textDirection: TextDirection.ltr,
      )..layout();
      tp.paint(canvas, Offset(x + 2, 0));
    }
  }

  @override
  bool shouldRepaint(_Ruler old) => old.dur != dur || old.pps != pps;
}
