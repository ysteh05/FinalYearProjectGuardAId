import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guardaid_mobile/src/app.dart';
import 'package:guardaid_mobile/src/controller.dart';
import 'package:guardaid_mobile/src/models.dart';
import 'package:guardaid_mobile/src/notification_service.dart';
import 'package:guardaid_mobile/src/repository.dart';
import 'package:guardaid_mobile/src/screens/alert_detail_screen.dart';
import 'package:guardaid_mobile/src/screens/home_shell.dart';
import 'package:guardaid_mobile/src/screens/settings_screen.dart';
import 'package:guardaid_mobile/src/theme.dart';

Widget _smallPhoneHost(Widget child) {
  return _phoneHost(child, 1.3);
}

Widget _phoneHost(Widget child, double textScale) {
  return MaterialApp(
    theme: buildGuardAidTheme(),
    home: MediaQuery(
      data: const MediaQueryData(
        size: Size(320, 640),
      ).copyWith(textScaler: TextScaler.linear(textScale)),
      child: child,
    ),
  );
}

Future<GuardAidController> _loadedDemoController(
  WidgetTester tester, {
  DemoGuardAidRepository? repository,
}) async {
  final controller = await tester.runAsync<GuardAidController>(() async {
    final selectedRepository = repository ?? DemoGuardAidRepository();
    final loadedController = GuardAidController(selectedRepository);
    loadedController.currentUser = await selectedRepository.signIn(
      'admin@guardaid.demo',
      'GuardAId123!',
    );
    await loadedController.refresh();
    return loadedController;
  });
  return controller!;
}

void _useSmallPhoneView(WidgetTester tester) {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(320, 640);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
}

class _NotificationDemoRepository extends DemoGuardAidRepository {
  final StreamController<String> _openedAlertController =
      StreamController<String>.broadcast();

  @override
  Stream<String> get openedAlertIds => _openedAlertController.stream;

  void emitOpenedAlert(String alertId) => _openedAlertController.add(alertId);

  Future<void> close() => _openedAlertController.close();
}

class _NoBroadcastRecipientsRepository extends DemoGuardAidRepository {
  @override
  Future<List<BroadcastRoleOption>> loadBroadcastRoleOptions(
    String alertId,
  ) async => const [
    BroadcastRoleOption(
      id: 'role_teacher',
      name: 'Teacher',
      sensitivity: SensitivityMode.certain,
      activeUserCount: 0,
      eligibleDeviceUserCount: 0,
      defaultSelected: true,
    ),
  ];
}

class _RoutineUpdateOnlyRepository extends DemoGuardAidRepository {
  @override
  Future<WorkflowSubmissionResult> submitWorkflowAction(
    String alertId,
    String action, {
    String? comment,
    List<String> recipientRoleIds = const [],
    required String idempotencyKey,
    required int expectedRevision,
  }) async {
    final saved = await super.submitWorkflowAction(
      alertId,
      action,
      comment: comment,
      recipientRoleIds: recipientRoleIds,
      idempotencyKey: idempotencyKey,
      expectedRevision: expectedRevision,
    );
    return WorkflowSubmissionResult(
      broadcastEligibleUsers: saved.broadcastEligibleUsers,
      notificationsQueued: 1,
      broadcastDispatchesQueued: 0,
      decisionUpdateDispatchesQueued: 1,
    );
  }
}

class _ConcurrentWorkflowRepository extends DemoGuardAidRepository {
  bool _injectConcurrentAction = true;

  @override
  Future<WorkflowSubmissionResult> submitWorkflowAction(
    String alertId,
    String action, {
    String? comment,
    List<String> recipientRoleIds = const [],
    required String idempotencyKey,
    required int expectedRevision,
  }) async {
    if (_injectConcurrentAction) {
      _injectConcurrentAction = false;
      await super.submitWorkflowAction(
        alertId,
        'confirm_and_handle',
        idempotencyKey: 'another-user-handled-first',
        expectedRevision: expectedRevision,
      );
    }
    return super.submitWorkflowAction(
      alertId,
      action,
      comment: comment,
      recipientRoleIds: recipientRoleIds,
      idempotencyKey: idempotencyKey,
      expectedRevision: expectedRevision,
    );
  }
}

void main() {
  test(
    'stale workflow action refreshes the latest alert before reporting conflict',
    () async {
      final repository = _ConcurrentWorkflowRepository();
      final controller = GuardAidController(repository);
      controller.currentUser = await repository.signIn(
        'admin@guardaid.demo',
        'GuardAId123!',
      );
      await controller.refresh();

      final result = await controller.submitWorkflowAction(
        'alert_weapon_01',
        'dismiss_false_alarm',
      );

      expect(result, isNull);
      final refreshed = controller.alerts.firstWhere(
        (alert) => alert.id == 'alert_weapon_01',
      );
      expect(refreshed.workflowStatus, AlertWorkflowStatus.inProgress);
      expect(refreshed.handlerName, isNotEmpty);
      expect(controller.error, contains('changed while you were viewing it'));
      controller.dispose();
    },
  );

  test('Demo reminder phases use deterministic cumulative stages', () async {
    final start = DateTime.utc(2026, 10, 7, 9);
    DemoGuardAidRepository newRepository({
      required Duration firstReminderDelay,
      required Duration allUsersReminderDelay,
    }) => DemoGuardAidRepository(
      firstReminderDelay: firstReminderDelay,
      allUsersReminderDelay: allUsersReminderDelay,
      clock: () => start,
    );

    final undecided = newRepository(
      // The seeded Demo alert begins three minutes before `start`.
      firstReminderDelay: const Duration(minutes: 4),
      allUsersReminderDelay: const Duration(minutes: 5),
    );
    await undecided.signIn('admin@guardaid.demo', 'GuardAId123!');
    expect(
      await undecided.processDueReminders(
        now: start.add(const Duration(seconds: 59)),
      ),
      0,
    );
    expect(
      await undecided.processDueReminders(
        now: start.add(const Duration(seconds: 60)),
      ),
      1,
    );
    var notices = await undecided.loadNotifications();
    expect(notices.first.title, 'Alert reminder · Demo');
    expect(notices.first.message, contains('sensitivity audience'));
    expect(
      await undecided.processDueReminders(
        now: start.add(const Duration(seconds: 90)),
      ),
      0,
    );
    expect(
      await undecided.processDueReminders(
        now: start.add(const Duration(seconds: 120)),
      ),
      1,
    );
    notices = await undecided.loadNotifications();
    expect(notices.first.title, 'All-user alert reminder · Demo');
    expect(notices.first.message, contains('All active demo users'));
    expect(
      await undecided.processDueReminders(
        now: start.add(const Duration(minutes: 6)),
      ),
      0,
    );

    final broadcast = newRepository(
      firstReminderDelay: const Duration(seconds: 60),
      allUsersReminderDelay: const Duration(seconds: 120),
    );
    await broadcast.signIn('admin@guardaid.demo', 'GuardAId123!');
    final alert = (await broadcast.loadAlerts()).firstWhere(
      (item) => item.id == 'alert_weapon_01',
    );
    final role = (await broadcast.loadBroadcastRoleOptions(alert.id))
        .firstWhere(
          (option) =>
              option.defaultSelected && option.eligibleDeviceUserCount > 0,
        );
    await broadcast.submitWorkflowAction(
      alert.id,
      'confirm_only',
      recipientRoleIds: [role.id],
      idempotencyKey: 'demo-broadcast-phase',
      expectedRevision: alert.workflowRevision,
    );
    // Confirm-only replaces Phase A with Phase B at the confirmation time.
    expect(
      await broadcast.processDueReminders(
        now: start.add(const Duration(seconds: 60)),
      ),
      1,
    );
    notices = await broadcast.loadNotifications();
    expect(notices.first.title, 'Alert reminder · Demo');
    expect(notices.first.message, contains('selected Broadcast Roles'));
    expect(
      await broadcast.processDueReminders(
        now: start.add(const Duration(seconds: 120)),
      ),
      1,
    );
    notices = await broadcast.loadNotifications();
    expect(notices.first.title, 'All-user alert reminder · Demo');
    expect(notices.first.message, contains('All active demo users'));
    expect(
      await broadcast.processDueReminders(
        now: start.add(const Duration(minutes: 6)),
      ),
      0,
    );

    final handled = newRepository(
      firstReminderDelay: const Duration(seconds: 60),
      allUsersReminderDelay: const Duration(seconds: 120),
    );
    await handled.signIn('admin@guardaid.demo', 'GuardAId123!');
    final handledAlert = (await handled.loadAlerts()).firstWhere(
      (item) => item.id == 'alert_weapon_01',
    );
    await handled.submitWorkflowAction(
      handledAlert.id,
      'confirm_and_handle',
      idempotencyKey: 'demo-handle-stops-phase-a',
      expectedRevision: handledAlert.workflowRevision,
    );
    expect(
      await handled.processDueReminders(
        now: start.add(const Duration(minutes: 6)),
      ),
      0,
    );
  });

  test('nested and double-encoded VLM JSON becomes readable prose', () {
    const summary = 'A staff member guides a student away from the corridor.';
    final nested = jsonEncode(
      jsonEncode({
        'result': {
          'scene_summary': {
            'description': summary,
            'visible_evidence': ['One person steps away.'],
            'uncertainty': 'low',
          },
        },
      }),
    );

    expect(readableSceneSummary(nested), summary);
    expect(readableSceneSummary('{"malformed": ['), unavailableSceneSummary);
    expect(
      readableSceneSummary(
        '{"description":"A person steps away from the doorway.",'
        '"visible_evidence":[',
      ),
      'A person steps away from the doorway.',
    );
    expect(
      readableSceneSummary(
        r'''{"description":"A student\'s bag falls near the doorway.",'''
        '"visible_evidence":[',
      ),
      "A student's bag falls near the doorway.",
    );
    expect(
      readableSceneSummary(
        '{"description":"A student falls near the doorway. A staff member approaches the student',
      ),
      'A student falls near the doorway. A staff member approaches the…',
    );
  });

  test('typed scene summary parser bounds legacy and current records', () {
    final longDescription = List.filled(30, 'visible').join(' ');
    final summary = AiSceneSummary.fromValue(
      jsonEncode(
        jsonEncode({
          'result': {
            'description': longDescription,
            'visible_evidence': [
              'First visible observation is retained.',
              'Second visible observation is retained.',
              'Third observation is discarded.',
            ],
            'uncertainty': 'invalid',
          },
        }),
      ),
    );

    expect(summary, isNotNull);
    expect(summary!.description.split(' ').length, lessThanOrEqualTo(12));
    expect(summary.description.length, lessThanOrEqualTo(120));
    expect(summary.visibleEvidence, hasLength(2));
    expect(
      summary.visibleEvidence.every(
        (item) => item.split(' ').length <= 10 && item.length <= 120,
      ),
      isTrue,
    );
    expect(summary.uncertainty, 'high');
    expect(
      AiSceneSummary.fromValue('A plain legacy observation.')?.description,
      'A plain legacy observation.',
    );
    expect(AiSceneSummary.fromValue('{"description":'), isNull);
  });

  test('notification channels keep urgent and routine paths separate', () {
    expect(
      GuardAidNotificationService.urgentChannel.id,
      'guardaid_urgent_alerts_v7',
    );
    expect(
      GuardAidNotificationService.urgentSoundResource,
      'guardaid_urgent_beep_v7',
    );
    expect(
      GuardAidNotificationService.reminderChannel.id,
      'guardaid_urgent_repeat_v1',
    );
    expect(
      GuardAidNotificationService.reminderSoundResource,
      'guardaid_urgent_repeat_3beep_v1',
    );
    expect(
      GuardAidNotificationService.escalationChannel.id,
      'guardaid_urgent_escalated_v1',
    );
    expect(
      GuardAidNotificationService.escalationSoundResource,
      'guardaid_urgent_escalated_4beep_v1',
    );
    expect(
      GuardAidNotificationService.urgentChannel.importance,
      Importance.max,
    );
    expect(
      GuardAidNotificationService.reminderChannel.importance,
      Importance.max,
    );
    expect(
      GuardAidNotificationService.escalationChannel.importance,
      Importance.max,
    );
    expect(
      GuardAidNotificationService.updatesChannel.id,
      'guardaid_updates_v1',
    );
    expect(
      GuardAidNotificationService.updatesChannel.importance,
      Importance.defaultImportance,
    );
  });

  test('only pre-sign-in initial alerts are made silent', () {
    final signInStartedAt = DateTime.utc(2026, 10, 12, 10);
    expect(
      GuardAidNotificationService.shouldSilenceInitialAlert({
        'source': 'alert_created',
        'alertDetectedAt': '2026-10-12T09:55:00Z',
      }, signInStartedAt: signInStartedAt),
      isTrue,
    );
    expect(
      GuardAidNotificationService.shouldSilenceInitialAlert({
        'source': 'alert_created',
        'alertDetectedAt': '2026-10-12T10:00:01Z',
      }, signInStartedAt: signInStartedAt),
      isFalse,
    );
    expect(
      GuardAidNotificationService.shouldSilenceInitialAlert({
        'source': 'alert_reminder',
        'alertDetectedAt': '2026-10-12T09:55:00Z',
      }, signInStartedAt: signInStartedAt),
      isFalse,
    );
    expect(
      GuardAidNotificationService.shouldSilenceInitialAlert(
        {'source': 'alert_created'},
        signInStartedAt: signInStartedAt,
        notificationSentAt: DateTime.utc(2026, 10, 12, 9, 55),
      ),
      isTrue,
    );
  });

  test('notification channel routing follows stage and protects updates', () {
    String selectedChannel(Map<String, dynamic> data) =>
        GuardAidNotificationService.channelForMessageData(data).id;

    expect(
      selectedChannel({'type': 'weapon', 'notificationStage': 'initial_alert'}),
      GuardAidNotificationService.urgentChannel.id,
    );
    expect(
      selectedChannel({
        'type': 'violence',
        'source': 'alert_broadcast',
        'notificationStage': 'initial_broadcast',
        'channelId': GuardAidNotificationService.urgentChannel.id,
      }),
      GuardAidNotificationService.urgentChannel.id,
    );
    expect(
      selectedChannel({
        'type': 'violence',
        'notificationStage': 'first_repeat',
        'channelId': GuardAidNotificationService.urgentChannel.id,
      }),
      GuardAidNotificationService.reminderChannel.id,
    );
    expect(
      selectedChannel({
        'type': 'physical_contact_bullying',
        'notificationStage': 'all_users_escalation',
      }),
      GuardAidNotificationService.escalationChannel.id,
    );
    expect(
      selectedChannel({
        'type': 'violence',
        'source': 'alert_decision',
        'channelId': GuardAidNotificationService.escalationChannel.id,
      }),
      GuardAidNotificationService.updatesChannel.id,
    );
    expect(
      selectedChannel({'source': 'system_status'}),
      GuardAidNotificationService.updatesChannel.id,
    );
  });

  testWidgets('GuardAId opens the English login screen', (tester) async {
    _useSmallPhoneView(tester);
    await tester.pumpWidget(const GuardAidApp());
    await tester.pump(const Duration(milliseconds: 950));

    expect(find.text('GuardAId'), findsOneWidget);
    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.text('Sign in'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('main mobile shell has no narrow-screen overflow', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(
        ListenableBuilder(
          listenable: controller,
          builder: (_, _) => HomeShell(controller: controller),
        ),
      ),
    );
    await tester.pump();

    expect(find.text('Alert Centre'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'notification tap opens the authorised alert review after refresh',
    (tester) async {
      _useSmallPhoneView(tester);
      final repository = _NotificationDemoRepository();
      final controller = GuardAidController(repository);
      addTearDown(() {
        controller.dispose();
        return repository.close();
      });
      await tester.runAsync(() async {
        controller.currentUser = await repository.signIn(
          'admin@guardaid.demo',
          'GuardAId123!',
        );
        await controller.refresh();
      });

      await tester.pumpWidget(
        _smallPhoneHost(
          ListenableBuilder(
            listenable: controller,
            builder: (_, _) => HomeShell(controller: controller),
          ),
        ),
      );
      await tester.pump();
      await tester.runAsync(() async {
        repository.emitOpenedAlert('alert_violence_01');
        await Future<void>.delayed(const Duration(milliseconds: 200));
      });
      // Use bounded frames: the Alert Review evidence player may have a live
      // ticker, so pumpAndSettle would wait forever even after navigation.
      for (var index = 0; index < 8; index++) {
        await tester.pump(const Duration(milliseconds: 100));
      }
      expect(find.text('Alert review'), findsOneWidget);
      expect(find.text('Block B Corridor'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('alert detail has no narrow-screen overflow', (tester) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(controller: controller, alertId: 'alert_bullying_01'),
      ),
    );
    await tester.pump();

    expect(find.text('Physical-contact bullying'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'weapon evidence swipes and opens as a zoomable full-screen photo',
    (tester) async {
      _useSmallPhoneView(tester);
      final controller = await _loadedDemoController(tester);
      addTearDown(controller.dispose);
      controller.alerts = [
        GuardAlert(
          id: 'weapon_gallery_test',
          cameraName: 'Main Gate',
          location: 'Campus',
          type: AlertType.weapon,
          status: AlertStatus.open,
          confidence: 0.92,
          sensitivity: SensitivityMode.sensitive,
          detectedAt: DateTime.utc(2026, 10, 12),
          descriptionStatus: 'disabled',
          evidenceStatus: 'available',
          evidence: const [
            EvidenceRecord(
              id: 'weapon_frame_1',
              mediaType: 'weapon_screenshot',
              url: 'https://example.invalid/frame-1.jpg',
              startSeconds: 0.5,
            ),
            EvidenceRecord(
              id: 'weapon_frame_2',
              mediaType: 'weapon_screenshot',
              url: 'https://example.invalid/frame-2.jpg',
              startSeconds: 1.5,
            ),
          ],
        ),
      ];

      await tester.pumpWidget(
        _smallPhoneHost(
          AlertDetailScreen(
            controller: controller,
            alertId: 'weapon_gallery_test',
          ),
        ),
      );
      await tester.pump();
      await tester.scrollUntilVisible(
        find.text('1 / 2'),
        220,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('1 / 2'), findsOneWidget);
      expect(find.text('Weapon evidence · 0.5s'), findsOneWidget);
      await tester.drag(find.byType(PageView), const Offset(-260, 0));
      await tester.pumpAndSettle();
      expect(find.text('2 / 2'), findsOneWidget);
      expect(find.text('Weapon evidence · 1.5s'), findsOneWidget);

      final photo = find.descendant(
        of: find.byType(PageView),
        matching: find.byType(InkWell),
      );
      await tester.ensureVisible(photo);
      await tester.pumpAndSettle();
      await tester.tap(photo);
      await tester.pumpAndSettle();
      expect(find.textContaining('Pinch to zoom'), findsOneWidget);
      expect(find.byType(InteractiveViewer), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('alert detail summary has no overflow at 2x font scale', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _phoneHost(
        AlertDetailScreen(controller: controller, alertId: 'alert_violence_01'),
        2.0,
      ),
    );
    await tester.pumpAndSettle();
    await tester.drag(find.byType(Scrollable).last, const Offset(0, -500));
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
  });

  testWidgets('settings omits non-functional application tiles', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(SettingsScreen(controller: controller)),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Sign out'),
      260,
      scrollable: find.byType(Scrollable).first,
    );

    expect(find.text('Application'), findsNothing);
    expect(find.text('Evidence access'), findsNothing);
    expect(find.text('Prototype build'), findsNothing);
    expect(find.text('Sign out'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('alert review attributes the latest decision to its actor', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(controller: controller, alertId: 'alert_resolved_01'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Latest decision'), findsOneWidget);
    expect(find.text('Resolved by Alex Morgan'), findsOneWidget);
    expect(
      find.text('Resolved after on-site staff intervention.'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('latest decision summary uses the full available row', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(controller: controller, alertId: 'alert_resolved_01'),
      ),
    );
    await tester.pumpAndSettle();

    final summarySize = tester.getSize(
      find.byKey(const ValueKey('latest-decision-card')),
    );
    expect(summarySize.width, greaterThan(220));
    expect(tester.takeException(), isNull);
  });

  testWidgets('dismiss and confirm-only accept an empty optional comment', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final dismissController = await _loadedDemoController(tester);
    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(
          controller: dismissController,
          alertId: 'alert_weapon_01',
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Dismiss as false alarm'),
      240,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.ensureVisible(find.text('Dismiss as false alarm'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Dismiss as false alarm'));
    await tester.pumpAndSettle();
    expect(find.text('Comment (optional)'), findsOneWidget);
    await tester.tap(find.text('Dismiss alert'));
    await tester.pumpAndSettle();
    expect(
      dismissController.alerts
          .firstWhere((item) => item.id == 'alert_weapon_01')
          .workflowStatus,
      AlertWorkflowStatus.dismissed,
    );
    await tester.pumpWidget(const SizedBox.shrink());
    dismissController.dispose();
    await tester.pumpAndSettle();

    final confirmController = await _loadedDemoController(tester);
    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(
          controller: confirmController,
          alertId: 'alert_weapon_01',
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Confirm incident'),
      240,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Confirm incident').first);
    await tester.pumpAndSettle();
    expect(find.text('Comment (optional)'), findsOneWidget);
    expect(
      find.text('Broadcast roles (at least one required)'),
      findsOneWidget,
    );
    expect(
      find.widgetWithText(CheckboxListTile, 'Security Personnel'),
      findsOneWidget,
    );
    final submitConfirm = find
        .widgetWithText(FilledButton, 'Confirm incident')
        .last;
    await tester.ensureVisible(submitConfirm);
    await tester.pumpAndSettle();
    await tester.tap(submitConfirm);
    await tester.pumpAndSettle();
    final confirmed = confirmController.alerts.firstWhere(
      (item) => item.id == 'alert_weapon_01',
    );
    expect(
      confirmed.workflowStatus,
      AlertWorkflowStatus.confirmedAwaitingHandler,
    );
    expect(confirmed.broadcastRoleIds, isNotEmpty);
    expect(confirmed.actions.last.note, isNull);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    confirmController.dispose();
    await tester.pumpAndSettle();
  });

  testWidgets('confirm-and-handle can later resolve with an optional comment', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);
    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(controller: controller, alertId: 'alert_weapon_01'),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Confirm incident'),
      240,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Confirm incident').first);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Confirm and handle it myself'));
    await tester.tap(find.text('Confirm and handle it myself'));
    await tester.pumpAndSettle();
    final submitConfirm = find
        .widgetWithText(FilledButton, 'Confirm incident')
        .last;
    await tester.ensureVisible(submitConfirm);
    await tester.pumpAndSettle();
    await tester.tap(submitConfirm);
    await tester.pumpAndSettle();
    var incident = controller.alerts.firstWhere(
      (item) => item.id == 'alert_weapon_01',
    );
    expect(incident.workflowStatus, AlertWorkflowStatus.inProgress);
    expect(incident.handlerUserId, controller.currentUser?.id);

    await tester.scrollUntilVisible(
      find.text('Mark incident resolved'),
      240,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.ensureVisible(find.text('Mark incident resolved'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Mark incident resolved'));
    await tester.pumpAndSettle();
    expect(find.text('Comment (optional)'), findsOneWidget);
    await tester.tap(find.text('Mark resolved'));
    await tester.pumpAndSettle();
    incident = controller.alerts.firstWhere(
      (item) => item.id == 'alert_weapon_01',
    );
    expect(incident.workflowStatus, AlertWorkflowStatus.resolved);
    expect(incident.actions.last.note, isNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('confirmed resolved flow can record a call simulation in-app', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);
    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(controller: controller, alertId: 'alert_weapon_01'),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Confirm incident'),
      240,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Confirm incident').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Confirm · already resolved'));
    await tester.pumpAndSettle();
    final simulatedCallOption = find.text('Simulate emergency call');
    await tester.ensureVisible(simulatedCallOption);
    await tester.pumpAndSettle();
    await tester.tap(simulatedCallOption);
    await tester.pumpAndSettle();
    final submitConfirm = find
        .widgetWithText(FilledButton, 'Confirm incident')
        .last;
    await tester.ensureVisible(submitConfirm);
    await tester.pumpAndSettle();
    await tester.tap(submitConfirm);
    await tester.pumpAndSettle();

    expect(find.text('Simulation complete'), findsOneWidget);
    expect(
      find.text(
        'Simulated emergency call has been placed. No real call was made.',
      ),
      findsOneWidget,
    );
    final saved = controller.alerts.firstWhere(
      (item) => item.id == 'alert_weapon_01',
    );
    expect(saved.workflowStatus, AlertWorkflowStatus.resolved);
    expect(
      saved.actions.where((item) => item.action == 'simulated_emergency_call'),
      hasLength(1),
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'confirm-only can proceed without an empty-recipient warning or acknowledgement',
    (tester) async {
      _useSmallPhoneView(tester);
      final repository = _NoBroadcastRecipientsRepository();
      final controller = await _loadedDemoController(
        tester,
        repository: repository,
      );
      addTearDown(controller.dispose);
      final initialUpdateCount = controller.notifications.length;
      await tester.pumpWidget(
        _smallPhoneHost(
          Scaffold(
            body: AlertDetailScreen(
              controller: controller,
              alertId: 'alert_weapon_01',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Confirm incident'),
        240,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Confirm incident').first);
      await tester.pumpAndSettle();

      expect(
        find.textContaining('No other active user in the selected roles'),
        findsNothing,
      );
      expect(
        find.text('Continue without an immediate push notification'),
        findsNothing,
      );
      expect(
        find.textContaining('eligible notification recipients'),
        findsNothing,
      );
      final confirmButton = find
          .widgetWithText(FilledButton, 'Confirm incident')
          .last;
      await tester.ensureVisible(confirmButton);
      await tester.pumpAndSettle();
      await tester.tap(confirmButton);
      await tester.pumpAndSettle();

      expect(
        controller.alerts
            .firstWhere((item) => item.id == 'alert_weapon_01')
            .workflowStatus,
        AlertWorkflowStatus.confirmedAwaitingHandler,
      );
      expect(controller.notifications.length, initialUpdateCount);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'confirm-only success copy uses the Broadcast-specific queue count',
    (tester) async {
      _useSmallPhoneView(tester);
      final controller = await _loadedDemoController(
        tester,
        repository: _RoutineUpdateOnlyRepository(),
      );
      addTearDown(controller.dispose);
      await tester.pumpWidget(
        _smallPhoneHost(
          Scaffold(
            body: AlertDetailScreen(
              controller: controller,
              alertId: 'alert_weapon_01',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Confirm incident'),
        240,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Confirm incident').first);
      await tester.pumpAndSettle();
      final confirmButton = find
          .widgetWithText(FilledButton, 'Confirm incident')
          .last;
      await tester.ensureVisible(confirmButton);
      await tester.pumpAndSettle();
      await tester.tap(confirmButton);
      await tester.pumpAndSettle();

      expect(
        find.text(
          'Incident confirmed. The selected-role response phase is active.',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'Incident confirmed. A Broadcast notification was queued for the selected roles.',
        ),
        findsNothing,
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'confirm-only empty role validation stays visible inside the confirmation sheet',
    (tester) async {
      _useSmallPhoneView(tester);
      final controller = await _loadedDemoController(
        tester,
        repository: _NoBroadcastRecipientsRepository(),
      );
      addTearDown(controller.dispose);
      await tester.pumpWidget(
        _smallPhoneHost(
          Scaffold(
            body: AlertDetailScreen(
              controller: controller,
              alertId: 'alert_weapon_01',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Confirm incident'),
        240,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Confirm incident').first);
      await tester.pumpAndSettle();

      final teacherRole = find.widgetWithText(CheckboxListTile, 'Teacher');
      await tester.ensureVisible(teacherRole);
      await tester.pumpAndSettle();
      await tester.tap(teacherRole);
      await tester.pumpAndSettle();
      final confirmButton = find
          .widgetWithText(FilledButton, 'Confirm incident')
          .last;
      await tester.ensureVisible(confirmButton);
      await tester.pumpAndSettle();
      await tester.tap(confirmButton);
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('broadcast-role-validation-error')),
        findsOneWidget,
      );
      expect(find.text('Select at least one Broadcast Role.'), findsOneWidget);
      expect(find.text('Confirm only · request a handler'), findsOneWidget);
      expect(find.byType(SnackBar), findsNothing);
      expect(
        controller.alerts
            .firstWhere((item) => item.id == 'alert_weapon_01')
            .workflowStatus,
        AlertWorkflowStatus.awaitingConfirmation,
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('alert review separates no-decision and simulated-call states', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(
      tester,
      repository: _NoBroadcastRecipientsRepository(),
    );
    addTearDown(controller.dispose);
    controller.alerts = [
      GuardAlert(
        id: 'alert_no_decision',
        cameraName: 'North Gate',
        location: 'North Gate',
        type: AlertType.violence,
        status: AlertStatus.open,
        confidence: 0.8,
        sensitivity: SensitivityMode.moderate,
        detectedAt: DateTime.utc(2026, 9, 13),
        descriptionStatus: 'ready',
        actions: const [
          AlertActionRecord(
            action: 'simulated_emergency_call',
            performedBy: 'Alex Morgan',
          ),
        ],
      ),
    ];

    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(controller: controller, alertId: 'alert_no_decision'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('No decision recorded yet'), findsOneWidget);
    expect(find.text('Simulated activity'), findsOneWidget);
    expect(
      find.text('Simulated emergency call by Alex Morgan'),
      findsOneWidget,
    );
    await tester.scrollUntilVisible(
      find.text('Dismiss as false alarm'),
      400,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.text('Dismiss as false alarm'), findsOneWidget);
    expect(find.text('Confirm incident'), findsOneWidget);
    expect(find.text('Resolve and notify school'), findsNothing);
    expect(find.text('Escalate as emergency'), findsNothing);
    expect(find.text('Simulate emergency call'), findsNothing);
    expect(find.text('Latest decision'), findsNothing);

    await tester.ensureVisible(find.text('Confirm incident'));
    await tester.tap(find.text('Confirm incident'));
    await tester.pumpAndSettle();
    expect(find.text('Simulate emergency call'), findsOneWidget);
    expect(
      find.text('No dialler opens and no real call is made.'),
      findsOneWidget,
    );
    Navigator.of(tester.element(find.text('Confirm incident').last)).pop();
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('Home and Alert Detail show readable scene summaries', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);
    const summary = 'A staff member guides a student away from the corridor.';
    controller.alerts = [
      GuardAlert(
        id: 'alert_json_summary',
        cameraName: 'Block B Corridor',
        location: 'Block B · Level 2',
        type: AlertType.violence,
        status: AlertStatus.open,
        confidence: 0.84,
        sensitivity: SensitivityMode.moderate,
        detectedAt: DateTime.utc(2026, 9, 13),
        descriptionStatus: 'ready',
        sceneDescription: jsonEncode(
          jsonEncode({
            'result': {'description': summary},
          }),
        ),
      ),
    ];
    expect(
      AiSceneSummary.fromValue(
        null,
        legacy: controller.alerts.single.sceneDescription,
      )?.description,
      summary,
    );

    await tester.pumpWidget(_smallPhoneHost(HomeShell(controller: controller)));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.textContaining(summary),
      300,
      scrollable: find.byType(Scrollable).last,
    );
    await tester.pumpAndSettle();
    expect(find.textContaining(summary), findsOneWidget);
    expect(find.textContaining('"description"'), findsNothing);

    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(
          controller: controller,
          alertId: 'alert_json_summary',
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.textContaining(summary),
      500,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.textContaining(summary), findsOneWidget);
    expect(find.textContaining('"description"'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('malformed scene summary shows the safe unavailable state', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);
    controller.alerts = [
      GuardAlert(
        id: 'alert_malformed_summary',
        cameraName: 'Block C Camera',
        location: 'Block C',
        type: AlertType.violence,
        status: AlertStatus.open,
        confidence: 0.7,
        sensitivity: SensitivityMode.moderate,
        detectedAt: DateTime.utc(2026, 9, 13),
        descriptionStatus: 'failed',
        sceneDescription: '{"description":',
      ),
    ];

    await tester.pumpWidget(
      _smallPhoneHost(
        AlertDetailScreen(
          controller: controller,
          alertId: 'alert_malformed_summary',
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text(unavailableSceneSummary),
      300,
      scrollable: find.byType(Scrollable).last,
    );

    expect(find.text(unavailableSceneSummary), findsOneWidget);
    expect(
      unavailableSceneSummary,
      'AI scene summary is unavailable. Review the video evidence.',
    );
    expect(
      find.textContaining('Scene description could not be formatted'),
      findsNothing,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'custom role deletion requires confirmation and warns assigned users',
    (tester) async {
      _useSmallPhoneView(tester);
      final controller = await _loadedDemoController(tester);
      addTearDown(controller.dispose);
      final created = await controller.createRole(
        name: 'Temporary reviewers',
        description: 'Short-term review access.',
        sensitivity: SensitivityMode.moderate,
        permissions: const RolePermissions(),
      );
      expect(created, isTrue);
      final role = controller.roles.singleWhere(
        (item) => item.name == 'Temporary reviewers',
      );
      expect(
        await controller.updateUserAssignment(
          'user_pending',
          role.id,
          UserStatus.active,
        ),
        isTrue,
      );

      await tester.pumpWidget(
        _smallPhoneHost(RoleManagementScreen(controller: controller)),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Delete role'),
        240,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Delete role'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('1 user currently have this role'),
        findsOneWidget,
      );
      expect(find.text('Cancel'), findsOneWidget);

      await tester.tap(find.widgetWithText(FilledButton, 'Delete role'));
      await tester.pumpAndSettle();
      expect(controller.roles.any((item) => item.id == role.id), isFalse);
      expect(
        controller.users
            .singleWhere((item) => item.id == 'user_pending')
            .status,
        UserStatus.pending,
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('role administration has no narrow-screen overflow', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(RoleManagementScreen(controller: controller)),
    );
    await tester.pump();

    expect(find.text('School Administrator'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('user administration has no narrow-screen overflow', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(UserAssignmentScreen(controller: controller)),
    );
    await tester.pump();

    expect(find.text('Alex Morgan'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Demo Mode can add an active user without Firebase or email', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _smallPhoneHost(UserAssignmentScreen(controller: controller)),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Add user'));
    await tester.pumpAndSettle();

    expect(
      find.textContaining('Demo mode: this only simulates adding a user.'),
      findsOneWidget,
    );
    await tester.enterText(find.byType(TextFormField).at(0), 'Morgan Chen');
    await tester.enterText(
      find.byType(TextFormField).at(1),
      'morgan.chen@example.test',
    );
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Teacher · Certain').last);
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Create user'),
      220,
      scrollable: find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first,
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('Create user').first);
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Demo mode only: no Firebase account or email'),
      findsOneWidget,
    );
    await tester.tap(find.text('Create user').last);
    await tester.pumpAndSettle();

    final created = controller.users.singleWhere(
      (user) => user.email == 'morgan.chen@example.test',
    );
    expect(created.status, UserStatus.active);
    expect(created.roleId, 'role_teacher');
    expect(
      find.textContaining('Demo user added. No Firebase account or email'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  test(
    'Demo account deactivation retains role and reactivation restores access',
    () async {
      final repository = DemoGuardAidRepository();
      final created = await repository.createUser(
        displayName: 'Morgan Chen',
        email: 'morgan.chen@example.test',
        roleId: 'role_teacher',
        linkExisting: false,
      );

      await repository.deactivateUser(created.user.id);
      var saved = (await repository.loadUsers()).singleWhere(
        (user) => user.id == created.user.id,
      );
      expect(saved.status, UserStatus.disabled);
      expect(saved.roleId, 'role_teacher');

      await repository.updateUserAssignment(
        saved.id,
        roleId: saved.roleId,
        status: UserStatus.active,
      );
      saved = (await repository.loadUsers()).singleWhere(
        (user) => user.id == created.user.id,
      );
      expect(saved.status, UserStatus.active);
      expect(saved.roleId, 'role_teacher');

      final duplicate = await repository.createUser(
        displayName: 'Another Name',
        email: 'morgan.chen@example.test',
        roleId: 'role_security_personnel',
        linkExisting: false,
      );
      expect(duplicate.alreadyExists, isTrue);
      expect(duplicate.user.id, created.user.id);
      expect(
        (await repository.loadUsers())
            .where((user) => user.email == 'morgan.chen@example.test')
            .length,
        1,
      );
    },
  );

  testWidgets('Add user form remains usable with enlarged text', (
    tester,
  ) async {
    _useSmallPhoneView(tester);
    final controller = await _loadedDemoController(tester);
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _phoneHost(AddUserScreen(controller: controller), 2.0),
    );
    await tester.pumpAndSettle();

    await tester.drag(find.byType(ListView).first, const Offset(0, -380));
    await tester.pumpAndSettle();

    expect(find.text('Add user'), findsOneWidget);
    expect(find.byType(ListView), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
