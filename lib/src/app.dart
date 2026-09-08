import 'package:flutter/material.dart';
import 'package:firebase_core/firebase_core.dart';

import '../firebase_options.dart';
import 'controller.dart';
import 'firebase_api_repository.dart';
import 'models.dart';
import 'repository.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'theme.dart';

class GuardAidApp extends StatefulWidget {
  const GuardAidApp({super.key});

  @override
  State<GuardAidApp> createState() => _GuardAidAppState();
}

class _GuardAidAppState extends State<GuardAidApp> {
  static const liveMode = bool.fromEnvironment('GUARDAID_LIVE_MODE');
  static const backendUrl = String.fromEnvironment(
    'GUARDAID_BACKEND_URL',
    defaultValue: 'http://localhost:8020',
  );

  GuardAidController? controller;
  Object? initialisationError;
  bool showSplash = true;

  @override
  void initState() {
    super.initState();
    _initialiseRepository();
    Future<void>.delayed(const Duration(milliseconds: 900), () {
      if (mounted) setState(() => showSplash = false);
    });
  }

  Future<void> _initialiseRepository() async {
    try {
      final GuardAidRepository repository;
      if (liveMode) {
        await Firebase.initializeApp(
          options: DefaultFirebaseOptions.currentPlatform,
        );
        repository = FirebaseApiGuardAidRepository(baseUrl: backendUrl);
      } else {
        repository = DemoGuardAidRepository();
      }
      if (mounted) setState(() => controller = GuardAidController(repository));
    } catch (error) {
      if (mounted) setState(() => initialisationError = error);
    }
  }

  @override
  void dispose() {
    controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'GuardAId',
      debugShowCheckedModeBanner: false,
      theme: buildGuardAidTheme(),
      home: initialisationError != null
          ? _ConfigurationErrorScreen(error: initialisationError!)
          : controller == null || showSplash
          ? const _SplashScreen()
          : ListenableBuilder(
              listenable: controller!,
              builder: (context, _) {
                final current = controller!;
                if (!current.isSignedIn) {
                  return LoginScreen(controller: current);
                }
                if (current.currentUser?.status == UserStatus.pending) {
                  return PendingApprovalScreen(controller: current);
                }
                if (current.currentUser?.status == UserStatus.disabled) {
                  return AccessDisabledScreen(controller: current);
                }
                return HomeShell(controller: current);
              },
            ),
    );
  }
}

class _ConfigurationErrorScreen extends StatelessWidget {
  const _ConfigurationErrorScreen({required this.error});

  final Object error;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(28),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 480),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.settings_rounded,
                        size: 56,
                        color: GuardAidColors.violence,
                      ),
                      const SizedBox(height: 16),
                      const Text(
                        'Live mode is not configured',
                        style: TextStyle(
                          fontSize: 23,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                      const SizedBox(height: 10),
                      const Text(
                        'Complete the Firebase Android setup and provide the GuardAId backend URL before starting live mode.',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          color: GuardAidColors.muted,
                          height: 1.45,
                        ),
                      ),
                      const SizedBox(height: 12),
                      Text(
                        error.toString(),
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          fontSize: 12,
                          color: GuardAidColors.muted,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SplashScreen extends StatelessWidget {
  const _SplashScreen();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      backgroundColor: GuardAidColors.navy,
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.shield_rounded, size: 76, color: Colors.white),
            SizedBox(height: 18),
            Text(
              'GuardAId',
              style: TextStyle(
                color: Colors.white,
                fontSize: 36,
                fontWeight: FontWeight.w900,
                letterSpacing: -1,
              ),
            ),
            SizedBox(height: 8),
            Text(
              'School Safety Response',
              style: TextStyle(color: Color(0xFFBFD4E0), fontSize: 15),
            ),
          ],
        ),
      ),
    );
  }
}
