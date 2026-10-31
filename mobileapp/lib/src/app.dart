import 'dart:async';

import 'package:flutter/material.dart';
import 'package:firebase_core/firebase_core.dart';

import 'controller.dart';
import 'error_messages.dart';
import 'firebase_api_repository.dart';
import 'models.dart';
import 'notification_service.dart';
import 'repository.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'theme.dart';

class GuardAidApp extends StatefulWidget {
  const GuardAidApp({super.key});

  @override
  State<GuardAidApp> createState() => _GuardAidAppState();
}

/// Firebase configuration is supplied at build time and is intentionally not
/// committed. Demo mode does not initialize Firebase, while live mode should
/// pass the six FIREBASE_* values with --dart-define (or provide a local
/// generated file in a developer-only build wrapper).
class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform => const FirebaseOptions(
    apiKey: String.fromEnvironment('FIREBASE_API_KEY'),
    appId: String.fromEnvironment('FIREBASE_APP_ID'),
    messagingSenderId: String.fromEnvironment('FIREBASE_MESSAGING_SENDER_ID'),
    projectId: String.fromEnvironment('FIREBASE_PROJECT_ID'),
    storageBucket: String.fromEnvironment('FIREBASE_STORAGE_BUCKET'),
  );
}

class _GuardAidAppState extends State<GuardAidApp> with WidgetsBindingObserver {
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
    WidgetsBinding.instance.addObserver(this);
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
        await GuardAidNotificationService.initialize();
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
    WidgetsBinding.instance.removeObserver(this);
    controller?.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && controller?.isSignedIn == true) {
      unawaited(controller!.refresh().catchError((Object _) {}));
    }
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'GuardAId',
      debugShowCheckedModeBanner: false,
      theme: buildGuardAidTheme(),
      home: initialisationError != null
          ? ConfigurationErrorScreen(error: initialisationError!)
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

class ConfigurationErrorScreen extends StatefulWidget {
  const ConfigurationErrorScreen({super.key, required this.error});

  final Object error;

  @override
  State<ConfigurationErrorScreen> createState() =>
      _ConfigurationErrorScreenState();
}

class _ConfigurationErrorScreenState extends State<ConfigurationErrorScreen> {
  bool showDetails = false;

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
                        'GuardAId could not start',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          fontSize: 23,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                      const SizedBox(height: 10),
                      Text(
                        describeStartupFailure(widget.error),
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          color: GuardAidColors.muted,
                          height: 1.45,
                        ),
                      ),
                      const SizedBox(height: 12),
                      TextButton(
                        onPressed: () =>
                            setState(() => showDetails = !showDetails),
                        child: Text(
                          showDetails
                              ? 'Hide technical details'
                              : 'Technical details',
                        ),
                      ),
                      if (showDetails) ...[
                        const SizedBox(height: 4),
                        const Text(
                          'Diagnostic detail for an administrator:',
                          style: TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w700,
                            color: GuardAidColors.muted,
                          ),
                        ),
                        const SizedBox(height: 6),
                        SelectableText(
                          '${widget.error}',
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            fontSize: 12,
                            color: GuardAidColors.muted,
                          ),
                        ),
                      ],
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
              style: TextStyle(color: Color(0xFFBFD4E0), fontSize: 15),            ),
          ],
        ),
      ),
    );
  }
}
