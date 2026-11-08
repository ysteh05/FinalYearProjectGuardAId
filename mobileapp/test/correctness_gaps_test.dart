import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:guardaid_mobile/src/controller.dart';
import 'package:guardaid_mobile/src/models.dart';
import 'package:guardaid_mobile/src/repository.dart';

class _Repository extends DemoGuardAidRepository {
  int cameras = 4;
  bool failCameras = false, failNotifications = false, failAlerts = false;
  bool forbidden = false;
  Completer<List<GuardAlert>>? gate;
  @override
  Future<int> loadMonitoredCameraCount() async {
    if (forbidden) throw const _Denied();
    if (failCameras) throw TimeoutException('cameras offline');
    return cameras;
  }
  @override
  Future<List<AppNotification>> loadNotifications() async {
    if (failNotifications) throw TimeoutException('notifications offline');
    return super.loadNotifications();
  }
  @override
  Future<List<GuardAlert>> loadAlerts() async {
    if (gate != null) return gate!.future;
    if (failAlerts) throw TimeoutException('alerts offline');
    return super.loadAlerts();
  }
}
class _Denied extends GuardAidRequestException {
  const _Denied() : super(403, 'Role revoked');
}

Future<GuardAidController> _signedIn(_Repository repo) async {
  final c = GuardAidController(repo);
  c.currentUser = await repo.refreshSession();
  await c.refresh();
  return c;
}

void main() {
  test('camera count is independent of names and alert count; zero is known', () async {
    final r = _Repository(); final c = await _signedIn(r);
    expect(c.monitoredCameraCount, 4);
    c.alerts = [];
    await c.refreshAlertFeed(); expect(c.monitoredCameraCount, 4);
    r.cameras = 0; await c.refreshAlertFeed(); expect(c.monitoredCameraCount, 0);
    c.dispose();
  });
  test('notifications and camera failure do not block successful feed; recovery clears stale', () async {
    final r = _Repository(); final c = await _signedIn(r);
    c.alerts = []; r.failNotifications = r.failCameras = true;
    await c.refreshAlertFeed();
    expect(c.alerts, isNotEmpty); expect(c.monitoredCameraCount, 4);
    expect(c.refreshErrors.keys, containsAll(['notifications','cameras']));
    r.failNotifications = r.failCameras = false;
    await c.refreshAlertFeed(); expect(c.refreshErrors, isEmpty);
    c.dispose();
  });
  test('first camera failure is unknown, not zero', () async {
    final r = _Repository()..failCameras = true;
    final c = await _signedIn(r);
    expect(c.monitoredCameraCount, isNull); expect(c.alerts, isNotEmpty);
    c.dispose();
  });
  test('failed feed retains data and reports staleness', () async {
    final r = _Repository(); final c = await _signedIn(r);
    final ids = c.alerts.map((a) => a.id).toList(); r.failAlerts = true;
    await expectLater(c.refreshAlertFeed(), throwsA(isA<TimeoutException>()));
    expect(c.alerts.map((a) => a.id), ids); expect(c.refreshErrors, contains('alerts'));
    c.dispose();
  });
  test('permission failure clears protected data and blocks late successful feed', () async {
    final r = _Repository(); final c = await _signedIn(r);
    r.gate = Completer<List<GuardAlert>>(); r.forbidden = true;
    final refresh = c.refreshAlertFeed();
    final checked = expectLater(refresh, throwsA(isA<_Denied>()));
    await Future<void>.delayed(Duration.zero);
    r.gate!.complete(await DemoGuardAidRepository().loadAlerts()); await checked;
    expect(c.alerts, isEmpty); expect(c.notifications, isEmpty);
    expect(c.monitoredCameraCount, isNull); c.dispose();
  });
  test('sign out while refresh is pending cannot repopulate private data', () async {
    final r = _Repository(); final c = await _signedIn(r);
    r.gate = Completer<List<GuardAlert>>(); final refresh = c.refreshAlertFeed();
    await Future<void>.delayed(Duration.zero); await c.signOut();
    r.gate!.complete(await DemoGuardAidRepository().loadAlerts()); await refresh;
    expect(c.currentUser, isNull); expect(c.alerts, isEmpty);
    expect(c.monitoredCameraCount, isNull); c.dispose();
  });
}
