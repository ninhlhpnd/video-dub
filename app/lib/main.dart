import 'package:flutter/material.dart';

import 'engine.dart';
import 'pages/home_page.dart';

final settings = AppSettings();

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await settings.load();
  runApp(const VideoDubApp());
}

class VideoDubApp extends StatelessWidget {
  const VideoDubApp({super.key});

  @override
  Widget build(BuildContext context) {
    final scheme = ColorScheme.fromSeed(seedColor: const Color(0xFF6C4DF6), brightness: Brightness.dark);
    return MaterialApp(
      title: 'Video Dub - Lồng tiếng Việt',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: scheme,
        useMaterial3: true,
        scaffoldBackgroundColor: const Color(0xFF15151C),
        cardTheme: const CardThemeData(color: Color(0xFF1F1F29), margin: EdgeInsets.zero),
        inputDecorationTheme: const InputDecorationTheme(border: OutlineInputBorder(), isDense: true),
      ),
      home: const HomePage(),
    );
  }
}
