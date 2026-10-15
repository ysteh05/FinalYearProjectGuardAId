import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guardaid_mobile/src/controller.dart';
import 'package:guardaid_mobile/src/models.dart';
import 'package:guardaid_mobile/src/repository.dart';
import 'package:guardaid_mobile/src/screens/alerts_screen.dart';
import 'package:guardaid_mobile/src/theme.dart';

GuardAlert _alert(String id, int age) => GuardAlert(
  id: id,
  cameraName: 'Camera $id',
  location: 'Campus corridor',
  type: AlertType.violence,
  status: AlertStatus.open,
  confidence: 0.8,
  sensitivity: SensitivityMode.moderate,
  detectedAt: DateTime.utc(2026, 10, 2).subtract(Duration(minutes: age)),
  descriptionStatus: 'disabled',
);

class _FeedRepository extends DemoGuardAidRepository {
  List<GuardAlert> feed = List.generate(
    20,
    (index) => _alert('feed_$index', index),
  );
  final Set<String> readIds = {};
  int loads = 0;
  bool failRead = false;
  bool failLoad = false;
  Completer<List<GuardAlert>>? blockedLoad;

  @override
  Future<List<GuardAlert>> loadAlerts() async {
    loads++;
    if (failLoad) throw StateError('Feed unavailable');
    if (blockedLoad != null) return blockedLoad!.future;
    return feed
        .map((alert) => alert.copyWith(isRead: readIds.contains(alert.id)))
        .toList();
  }

  @override
  Future<void> markAlertRead(String alertId) async {
    if (failRead) throw StateError('Read request failed');
    readIds.add(alertId);
  }
}

Widget _host(
  GuardAidController controller, {
  bool active = true,
  double scale = 1.3,
}) => MaterialApp(
  theme: buildGuardAidTheme(),
  home: MediaQuery(
    data: MediaQueryData(
      size: const Size(320, 640),
      textScaler: TextScaler.linear(scale),
    ),
    child: Scaffold(
      body: AlertsScreen(controller: controller, isActive: active),
    ),
  ),
);

Future<GuardAidController> _controller(
  WidgetTester tester,
  _FeedRepository repository,
) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(320, 640);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
  final controller = GuardAidController(repository);
  controller.currentUser = (await repository.loadUsers()).first;
  await controller.refresh();
  addTearDown(controller.dispose);
  addTearDown(() => tester.pumpWidget(const SizedBox.shrink()));
  return controller;
}

void main() {
  testWidgets(
    'Alerts defaults to active incidents while All includes history',
    (tester) async {
      final repository = _FeedRepository()
        ..feed = [
          _alert('active_alert', 0),
          _alert('resolved_alert', 1).copyWith(
            status: AlertStatus.resolved,
            workflowStatus: AlertWorkflowStatus.resolved,
          ),
          _alert('dismissed_alert', 2).copyWith(
            status: AlertStatus.dismissed,
            workflowStatus: AlertWorkflowStatus.dismissed,
          ),
        ];
      final controller = await _controller(tester, repository);
      await tester.pumpWidget(_host(controller));
      await tester.pumpAndSettle();

      final activeChip = find.widgetWithText(FilterChip, 'Active');
      final allChip = find.widgetWithText(FilterChip, 'All');
      expect(tester.widget<FilterChip>(activeChip).selected, isTrue);
      final scrollable = find.byType(Scrollable).first;
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('active_alert')),
        260,
        scrollable: scrollable,
      );
      expect(find.byKey(const ValueKey('active_alert')), findsOneWidget);
      expect(find.byKey(const ValueKey('resolved_alert')), findsNothing);
      expect(find.byKey(const ValueKey('dismissed_alert')), findsNothing);

      await tester.ensureVisible(allChip);
      await tester.tap(allChip);
      await tester.pumpAndSettle();
      expect(tester.widget<FilterChip>(allChip).selected, isTrue);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('resolved_alert')),
        260,
        scrollable: scrollable,
      );
      expect(find.byKey(const ValueKey('resolved_alert')), findsOneWidget);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('dismissed_alert')),
        260,
        scrollable: scrollable,
      );
      expect(find.byKey(const ValueKey('dismissed_alert')), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'polling preserves the visible record and queues arrivals at 2x text',
    (tester) async {
      final repository = _FeedRepository();
      final controller = await _controller(tester, repository);
      await tester.pumpWidget(_host(controller, scale: 2));
      await tester.pump();
      final scroll = tester
          .widget<CustomScrollView>(find.byType(CustomScrollView))
          .controller!;
      scroll.jumpTo(2400);
      await tester.pump();
      final offset = scroll.offset;
      final anchors = find.byType(AlertCard).evaluate().where((element) {
        final rect = tester.getRect(find.byKey(element.widget.key!));
        return rect.bottom > 80 && rect.top < 640;
      });
      expect(anchors, isNotEmpty);
      final anchor = find.byKey(anchors.first.widget.key!);
      final originalPosition = tester.getTopLeft(anchor);
      repository.feed = [_alert('new_alert', -1), ...repository.feed];

      await tester.pump(const Duration(seconds: 5));
      await tester.pump();
      expect(repository.loads, 2);
      expect(scroll.offset, offset);
      expect(tester.getTopLeft(anchor), originalPosition);
      expect(find.byKey(const ValueKey('new-alerts-toast')), findsOneWidget);
      expect(find.text('1 new alert · View'), findsOneWidget);
      expect(find.byKey(const ValueKey('new_alert')), findsNothing);
      expect(tester.takeException(), isNull);

      await tester.pump(const Duration(seconds: 4));
      expect(find.byKey(const ValueKey('new-alerts-toast')), findsNothing);
      expect(find.byKey(const ValueKey('new-alerts-entry')), findsOneWidget);
      expect(scroll.offset, offset);
      await tester.tap(find.byKey(const ValueKey('new-alerts-entry')));
      await tester.pumpAndSettle();
      expect(scroll.offset, 0);
      expect(find.byKey(const ValueKey('new-alerts-entry')), findsNothing);
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('new_alert')),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(
        find.byKey(const ValueKey('unread-alert-new_alert')),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('pull to refresh replaces the refresh button', (tester) async {
    final repository = _FeedRepository();
    final controller = await _controller(tester, repository);
    await tester.pumpWidget(_host(controller));
    expect(find.byTooltip('Refresh alerts'), findsNothing);
    repository.feed = [_alert('manual_alert', -1), ...repository.feed];
    await tester.drag(find.byType(CustomScrollView), const Offset(0, 380));
    await tester.pumpAndSettle();
    expect(repository.loads, 2);
    expect(controller.alerts.first.id, 'manual_alert');
    expect(tester.takeException(), isNull);
  });

  testWidgets('polling pauses on other tabs, covered routes and background', (
    tester,
  ) async {
    final repository = _FeedRepository();
    final controller = await _controller(tester, repository);
    await tester.pumpWidget(_host(controller, active: false));
    await tester.pump(const Duration(seconds: 6));
    expect(repository.loads, 1);
    await tester.pumpWidget(_host(controller));
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(repository.loads, 2);

    final navigator = Navigator.of(tester.element(find.byType(AlertsScreen)));
    unawaited(
      navigator.push(
        MaterialPageRoute<void>(
          builder: (_) => const Scaffold(body: Text('Covered route')),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 6));
    expect(repository.loads, 2);
    navigator.pop();
    await tester.pumpAndSettle();

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump(const Duration(seconds: 6));
    expect(repository.loads, 2);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();
    expect(repository.loads, 3);
    expect(tester.takeException(), isNull);
  });

  testWidgets('slow polling requests never overlap', (tester) async {
    final repository = _FeedRepository();
    final controller = await _controller(tester, repository);
    await tester.pumpWidget(_host(controller));
    repository.blockedLoad = Completer<List<GuardAlert>>();
    await tester.pump(const Duration(seconds: 5));
    expect(repository.loads, 2);
    await tester.pump(const Duration(seconds: 15));
    expect(repository.loads, 2);
    repository.blockedLoad!.complete(repository.feed);
    repository.blockedLoad = null;
    await tester.pump();
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(repository.loads, 3);
    expect(tester.takeException(), isNull);
  });

  testWidgets('failed background refresh preserves data, search and position', (
    tester,
  ) async {
    final repository = _FeedRepository();
    final controller = await _controller(tester, repository);
    await tester.pumpWidget(_host(controller));
    await tester.enterText(find.byType(TextField), 'Campus corridor');
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pump();
    final scroll = tester
        .widget<CustomScrollView>(find.byType(CustomScrollView))
        .controller!;
    scroll.jumpTo(1200);
    await tester.pump();
    final offset = scroll.offset;
    repository.failLoad = true;
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(repository.loads, 2);
    expect(controller.alerts.length, 20);
    expect(scroll.offset, offset);
    expect(find.text('Campus corridor'), findsWidgets);
    expect(find.byKey(const ValueKey('new-alerts-entry')), findsNothing);
    expect(tester.takeException(), isNull);
    repository.failLoad = false;
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(repository.loads, 3);
    expect(scroll.offset, offset);
  });

  testWidgets(
    'opening an alert clears only its dot and survives feed refresh',
    (tester) async {
      final repository = _FeedRepository();
      final controller = await _controller(tester, repository);
      final unreadUpdates = controller.unreadCount;
      await tester.pumpWidget(_host(controller));
      await tester.scrollUntilVisible(
        find.byKey(const ValueKey('feed_0')),
        260,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.byKey(const ValueKey('unread-alert-feed_0')), findsOneWidget);
      await tester.ensureVisible(find.byKey(const ValueKey('feed_0')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('feed_0')));
      await tester.pumpAndSettle();
      expect(repository.readIds, contains('feed_0'));
      expect(controller.unreadCount, unreadUpdates);
      expect(controller.alerts.first.status, AlertStatus.open);
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('unread-alert-feed_0')), findsNothing);
      await controller.refreshAlertFeed();
      await tester.pump();
      expect(controller.alerts.first.isRead, isTrue);
      expect(find.byKey(const ValueKey('unread-alert-feed_0')), findsNothing);

      repository.failRead = true;
      expect(await controller.markAlertRead('feed_1'), isFalse);
      expect(controller.alerts[1].isRead, isFalse);
      expect(tester.takeException(), isNull);
    },
  );
}
