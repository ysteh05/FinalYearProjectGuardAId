import 'dart:async';

import 'package:flutter/foundation.dart';

import 'models.dart';
import 'repository.dart';

class GuardAidController extends ChangeNotifier {
  GuardAidController(this.repository) {
    _notificationOpenSubscription = repository.openedAlertIds.listen(
      _handleNotificationOpen,
    );
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
  BorderlineSettings? borderlineSettings;
  String? requestedAlertId;
  StreamSubscription<String>? _notificationOpenSubscription;
  Future<void>? _refreshInFlight;
  bool _refreshIncludesAdministration = false;
  String? _readStateUserId;
  final Set<String> _readAlerts = {};
  final Set<String> _markingAlerts = {};
  bool _disposed = false;

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

  Future<void> refresh() => _refreshData(includeAdministration: true);

  Future<void> refreshAlertFeed() => _refreshData(includeAdministration: false);

  Future<void> loadAlertDetails(String alertId) async {
    try {
      final detailed = await repository.loadAlertDetails(alertId);
      if (_disposed) return;
      alerts = alerts
          .map((alert) => alert.id == alertId ? detailed : alert)
          .toList(growable: false);
      notifyListeners();
    } catch (_) {
      // Keep the cached alert usable if the detail request is temporarily down.
    }
  }

  Future<void> _refreshData({required bool includeAdministration}) async {
    if (_disposed) return;
    final running = _refreshInFlight;
    if (running != null) {
      final includesAdministration = _refreshIncludesAdministration;
      await running;
      if (includeAdministration && !includesAdministration) {
        await _refreshData(includeAdministration: true);
      }
      return;
    }
    _refreshIncludesAdministration = includeAdministration;
    final task = _loadRefresh(includeAdministration);
    _refreshInFlight = task;
    try {
      await task;
    } finally {
      _refreshInFlight = null;
    }
  }

  Future<void> _loadRefresh(bool includeAdministration) async {
    final originalUserId = currentUser?.id;
    _syncReadState();
    final session = currentUser == null
        ? null
        : await repository.refreshSession();
    if (_disposed || currentUser?.id != originalUserId) return;
    if (session != null) {
      if (session.status != UserStatus.active) {
        currentUser = session;
        alerts = const [];
        notifications = const [];
        roles = const [];
        users = const [];
        notifyListeners();
        return;
      }
    }
    final results = await Future.wait<Object>([
      repository.loadAlerts(),
      repository.loadNotifications(),
      if (includeAdministration) repository.loadRoles(),
      if (includeAdministration) repository.loadUsers(),
      if (includeAdministration &&
          session?.permissions.manageBorderlineSettings == true)
        repository.loadBorderlineSettings(),
    ]);
    if (_disposed || currentUser?.id != originalUserId) return;
    currentUser = session;
    alerts = (results[0] as List<GuardAlert>)
        .map(
          (alert) => _readAlerts.contains(alert.id)
              ? alert.copyWith(isRead: true)
              : alert,
        )
        .toList(growable: false);
    notifications = results[1] as List<AppNotification>;
    if (includeAdministration) {
      roles = results[2] as List<GuardRole>;
      users = results[3] as List<AppUser>;
    } else if (session?.permissions.manageUsers != true) {
      users = const [];
    }
    if (includeAdministration &&
        session?.permissions.manageBorderlineSettings == true &&
        results.length > 4) {
      final settings = results[4] as BorderlineSettings;
      borderlineSettings = settings;
      borderlineEnabled = settings.reviewEnabled;
      borderlineRecipientRoleIds = Set.of(settings.recipientRoleIds);
    }
    notifyListeners();
  }

  void _syncReadState() {
    if (_readStateUserId == currentUser?.id) return;
    _readStateUserId = currentUser?.id;
    _readAlerts.clear();
  }

  Future<bool> markAlertRead(String alertId) async {
    if (currentUser?.status != UserStatus.active ||
        _markingAlerts.contains(alertId) ||
        alerts.any((alert) => alert.id == alertId && alert.isRead)) {
      return false;
    }
    _syncReadState();
    final userId = currentUser?.id;
    _markingAlerts.add(alertId);
    try {
      await repository.markAlertRead(alertId);
      if (_disposed || currentUser?.id != userId) return false;
      _readAlerts.add(alertId);
      alerts = alerts
          .map(
            (alert) =>
                alert.id == alertId ? alert.copyWith(isRead: true) : alert,
          )
          .toList(growable: false);
      notifyListeners();
      return true;
    } catch (_) {
      return false;
    } finally {
      _markingAlerts.remove(alertId);
    }
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

  Future<void> _handleNotificationOpen(String alertId) async {
    requestedAlertId = alertId;
    // Reload before notifying HomeShell. Otherwise a newly-created alert can
    // be absent from the old list when HomeShell checks whether the payload is
    // authorised, causing a valid tap to be discarded as "not found".
    try {
      await refresh();
    } catch (_) {
      // Keep the payload available for the next rebuild; the normal refresh
      // error state is surfaced by the controller's existing UI lifecycle.
      if (!_disposed) notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
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

  Future<List<BroadcastRoleOption>> loadBroadcastRoleOptions(String alertId) =>
      repository.loadBroadcastRoleOptions(alertId);

  Future<WorkflowSubmissionResult?> submitWorkflowAction(
    String alertId,
    String action, {
    String? comment,
    List<String> recipientRoleIds = const [],
  }) => _runResult(() async {
    final currentAlert = alerts.where((item) => item.id == alertId).firstOrNull;
    if (currentAlert == null) throw StateError('Alert not found.');
    final idempotencyKey =
        '${alertId}_${action}_${DateTime.now().microsecondsSinceEpoch}';
    late final WorkflowSubmissionResult result;
    try {
      result = await repository.submitWorkflowAction(
        alertId,
        action,
        comment: comment,
        recipientRoleIds: recipientRoleIds,
        idempotencyKey: idempotencyKey,
        expectedRevision: currentAlert.workflowRevision,
      );
    } on GuardAidConflictException {
      await refreshAlertFeed();
      rethrow;
    }
    await refresh();
    return result;
  });

  Future<bool> simulateEmergencyCall(String alertId) => _run(() async {
    await repository.recordSimulatedEmergencyCall(alertId);
    await refresh();
  });

  Future<bool> markAllNotificationsRead() => _run(() async {
    await repository.markAllNotificationsRead();
    notifications = await repository.loadNotifications();
  });

  Future<bool> updateRoleSensitivity(String roleId, SensitivityMode mode) =>
      _run(() async {
        await repository.updateRoleSensitivity(roleId, mode);
        currentUser = await repository.refreshSession();
        roles = await repository.loadRoles();
        users = await repository.loadUsers();
        alerts = await repository.loadAlerts();
      });

  Future<bool> createRole({
    required String name,
    required String description,
    required SensitivityMode sensitivity,
    required RolePermissions permissions,
  }) => _run(() async {
    await repository.createRole(
      name: name,
      description: description,
      sensitivity: sensitivity,
      permissions: permissions,
    );
    roles = await repository.loadRoles();
  });

  Future<bool> deleteRole(String roleId) => _run(() async {
    await repository.deleteRole(roleId);
    currentUser = await repository.refreshSession();
    if (currentUser?.status != UserStatus.active) {
      alerts = const [];
      notifications = const [];
      roles = const [];
      users = const [];
      return;
    }
    roles = await repository.loadRoles();
    users = await repository.loadUsers();
    if (currentUser?.permissions.manageBorderlineSettings == true) {
      final settings = await repository.loadBorderlineSettings();
      borderlineSettings = settings;
      borderlineEnabled = settings.reviewEnabled;
      borderlineRecipientRoleIds = Set.of(settings.recipientRoleIds);
    }
  });

  Future<bool> updateUserAssignment(
    String userId,
    String? roleId,
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

  Future<UserProvisioningResult?> createUser({
    required String displayName,
    required String email,
    required String roleId,
    required bool linkExisting,
  }) async {
    isBusy = true;
    error = null;
    notifyListeners();
    try {
      final result = await repository.createUser(
        displayName: displayName,
        email: email,
        roleId: roleId,
        linkExisting: linkExisting,
      );
      currentUser = await repository.refreshSession();
      users = await repository.loadUsers();
      roles = await repository.loadRoles();
      return result;
    } catch (exception) {
      error = _errorMessage(exception);
      return null;
    } finally {
      isBusy = false;
      notifyListeners();
    }
  }

  Future<bool> resendUserSetupEmail(String email) async {
    if (repository.isDemo) return false;
    return _run(() async {
      final requested = await repository.sendUserSetupEmail(email);
      if (!requested) {
        throw StateError('The setup email could not be requested.');
      }
    });
  }

  Future<bool> deactivateUser(String userId) => _run(() async {
    await repository.deactivateUser(userId);
    users = await repository.loadUsers();
    roles = await repository.loadRoles();
  });

  Future<bool> updateProfile(String displayName) => _run(() async {
    await repository.updateProfile(displayName);
    currentUser = await repository.refreshSession();
    users = await repository.loadUsers();
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

  Future<bool> setBorderlineRoleEnabled(String roleId, bool enabled) =>
      _updateBorderlineRecipient(
        (settings) => repository.setBorderlineRoleEnabled(
          roleId,
          enabled,
          settings.revision,
        ),
      );

  Future<bool> setBorderlineUserEnabled(String userId, bool enabled) =>
      _updateBorderlineRecipient(
        (settings) => repository.setBorderlineUserEnabled(
          userId,
          enabled,
          settings.revision,
        ),
      );

  Future<bool> _updateBorderlineRecipient(
    Future<BorderlineSettings> Function(BorderlineSettings settings) update,
  ) => _run(() async {
    final current =
        borderlineSettings ?? await repository.loadBorderlineSettings();
    borderlineSettings = await update(current);
    currentUser = await repository.refreshSession();
    borderlineEnabled = borderlineSettings!.reviewEnabled;
    borderlineRecipientRoleIds = {
      for (final group in borderlineSettings!.roles)
        if (group.selectionState == 'on') group.id,
    };
  });

  Future<bool> _run(Future<void> Function() task) async {
    isBusy = true;
    error = null;
    notifyListeners();
    try {
      await task();
      return true;
    } catch (exception) {
      error = _errorMessage(exception);
      return false;
    } finally {
      isBusy = false;
      notifyListeners();
    }
  }

  Future<T?> _runResult<T>(Future<T> Function() task) async {
    isBusy = true;
    error = null;
    notifyListeners();
    try {
      return await task();
    } catch (exception) {
      error = _errorMessage(exception);
      return null;
    } finally {
      isBusy = false;
      notifyListeners();
    }
  }

  String _errorMessage(Object exception) => switch (exception) {
    FormatException(:final message) => message,
    _ => exception.toString().replaceFirst('Exception: ', ''),
  };
}
