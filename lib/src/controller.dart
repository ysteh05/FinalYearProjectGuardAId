import 'dart:async';

import 'package:flutter/foundation.dart';

import 'models.dart';
import 'repository.dart';

class GuardAidController extends ChangeNotifier {
  GuardAidController(this.repository) {
    _notificationOpenSubscription = repository.openedAlertIds.listen((alertId) {
      requestedAlertId = alertId;
      refresh();
      notifyListeners();
    });
  }

  final GuardAidRepository repository;
  AppUser? currentUser;
  List<GuardAlert> alerts = const [];
  List<AppNotification> notifications = const [];
  List<GuardRole> roles = const [];
  List<AppUser> users = const [];
  bool isBusy = false;
  String? error;
  bool borderlineEnabled = true;
  Set<String> borderlineRecipientRoleIds = {
    'role_school_administrator',
    'role_security_personnel',
  };
  String? requestedAlertId;
  StreamSubscription<String>? _notificationOpenSubscription;

  bool get isSignedIn => currentUser != null;
  int get unreadCount => notifications.where((item) => !item.isRead).length;
  int get openAlertCount => alerts
      .where(
        (alert) =>
            alert.status == AlertStatus.open ||
            alert.status == AlertStatus.inReview,
      )
      .length;
  int get monitoredCameraCount =>
      alerts.map((alert) => alert.cameraName).toSet().length;

  Future<bool> signIn(String email, String password) async {
    return _run(() async {
      currentUser = await repository.signIn(email, password);
      if (currentUser?.status == UserStatus.active) {
        await refresh();
        requestedAlertId = await repository.takeInitialAlertId();
      }
    });
  }

  Future<bool> sendPasswordReset(String email) =>
      _run(() => repository.sendPasswordReset(email));

  Future<void> refresh() async {
    final results = await Future.wait<Object>([
      repository.loadAlerts(),
      repository.loadNotifications(),
      repository.loadRoles(),
      repository.loadUsers(),
    ]);
    alerts = results[0] as List<GuardAlert>;
    notifications = results[1] as List<AppNotification>;
    roles = results[2] as List<GuardRole>;
    users = results[3] as List<AppUser>;
    notifyListeners();
  }

  Future<void> signOut() async {
    await repository.signOut();
    currentUser = null;
    alerts = const [];
    notifications = const [];
    notifyListeners();
  }

  void clearRequestedAlert() {
    requestedAlertId = null;
  }

  @override
  void dispose() {
    _notificationOpenSubscription?.cancel();
    super.dispose();
  }

  Future<bool> submitAlertAction(
    String alertId,
    AlertStatus status,
    String reason, {
    List<String> recipientRoleIds = const [],
  }) => _run(() async {
    await repository.submitAlertAction(
      alertId,
      status,
      reason: reason,
      recipientRoleIds: recipientRoleIds,
    );
    await refresh();
  });

  Future<bool> simulateEmergencyCall(String alertId) =>
      _run(() => repository.recordSimulatedEmergencyCall(alertId));

  Future<bool> markAllNotificationsRead() => _run(() async {
    await repository.markAllNotificationsRead();
    notifications = await repository.loadNotifications();
  });

  Future<bool> updateRoleSensitivity(String roleId, SensitivityMode mode) =>
      _run(() async {
        await repository.updateRoleSensitivity(roleId, mode);
        roles = await repository.loadRoles();
        users = await repository.loadUsers();
      });

  Future<bool> updateUserAssignment(
    String userId,
    String roleId,
    UserStatus status,
  ) => _run(() async {
    await repository.updateUserAssignment(
      userId,
      roleId: roleId,
      status: status,
    );
    users = await repository.loadUsers();
    roles = await repository.loadRoles();
  });

  Future<bool> updateBorderlineSettings(bool enabled, Set<String> recipients) =>
      _run(() async {
        await repository.updateBorderlineSettings(
          enabled: enabled,
          recipientRoleIds: recipients,
        );
        borderlineEnabled = enabled;
        borderlineRecipientRoleIds = Set.of(recipients);
      });

  Future<bool> _run(Future<void> Function() task) async {
    isBusy = true;
    error = null;
    notifyListeners();
    try {
      await task();
      return true;
    } catch (exception) {
      error = switch (exception) {
        FormatException(:final message) => message,
        _ => exception.toString().replaceFirst('Exception: ', ''),
      };
      return false;
    } finally {
      isBusy = false;
      notifyListeners();
    }
  }
}
