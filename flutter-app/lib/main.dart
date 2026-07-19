import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'config/theme.dart';
import 'services/api_service.dart';
import 'services/storage_service.dart';
import 'screens/splash_screen.dart';
import 'screens/registration_screen.dart';
import 'screens/main_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  await StorageService().init();

  final storage = StorageService();
  final api = ApiService();

  if (storage.telegramId != null) {
    api.setUserId(storage.telegramId!);
  } else if (storage.userId != null) {
    api.setUserId(storage.userId!);
  }

  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
    systemNavigationBarColor: AppTheme.bg,
    systemNavigationBarIconBrightness: Brightness.light,
  ));

  SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
    DeviceOrientation.portraitDown,
  ]);

  runApp(const BenzinRyadomApp());
}

class BenzinRyadomApp extends StatelessWidget {
  const BenzinRyadomApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Бензин рядом',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.darkTheme,
      home: const _AppFlow(),
    );
  }
}

class _AppFlow extends StatefulWidget {
  const _AppFlow();

  @override
  State<_AppFlow> createState() => _AppFlowState();
}

class _AppFlowState extends State<_AppFlow> {
  bool _splashDone = false;
  bool _guestMode = false;

  @override
  Widget build(BuildContext context) {
    if (!_splashDone) {
      return SplashScreen(onComplete: () {
        setState(() => _splashDone = true);
      });
    }

    final storage = StorageService();
    if (storage.userId != null || _guestMode) {
      return const MainScreen();
    }

    return RegistrationScreen(
      onSkip: () {
        setState(() => _guestMode = true);
      },
    );
  }
}
