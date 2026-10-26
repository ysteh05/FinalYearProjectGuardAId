import 'dart:async';

import 'package:flutter/foundation.dart';

import 'models.dart';
import 'repository.dart';
import 'workflow_requests.dart';

class GuardAidController extends ChangeNotifier {
  GuardAidController(
    this.repository, {
    WorkflowRequestStore? workflowRequestStore,
  }) : _workflowRequestStore =
           workflowRequestStore ??
           (repository.isDemo
               ? MemoryWorkflowRequestStore()
               : PreferencesWorkflowRequestStore()) {
    _notificationOpenSubscription = repository.openedAlertIds.listen(
      _handleNotificationOpen,
    );
  }

  final GuardAidRepository repository;
  final WorkflowRequestStore _workflowRequestStore;
  String? _pendingWorkflowUserId;
  Map<String, PendingWorkflowRequest> _pendingWorkflowRequests = {};
  final Set<String> _workflowInFlight = {};

  PendingWorkflowRequest? pendingWorkflowRequest(String alertId) =>
      _pendingWorkflowUserId == currentUser?.id
      ? _pendingWorkflowRequests[alertId]
      : null;

  Future<void> _ensureWorkflowRequests() async {
    final userId = currentUser?.id;
    if (userId == _pendingWorkflowUserId) return;
    final requests = userId == null
        ? <String, PendingWorkflowRequest>{}
        : await _workflowRequestStore.load(userId);
    if (currentUser?.id != userId) return;
    _pendingWorkflowRequests = requests;
    _pendingWorkflowUserId = userId;
  }

  Future<void> _forgetWorkflowRequest(String userId, String alertId) async {
    await _workflowRequestStore.remove(userId, alertId);
    if (_pendingWorkflowUserId == userId) {
      _pendingWorkflowRequests.remove(alertId);
    }
  }

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
  String? _refreshUserId;
  int? monitoredCameraCount;
  final Map<String, String> refreshErrors = {};
  final Map<String, DateTime> lastRefreshSuccess = {};
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

  /// Messages shown for locally rejected sign-in input. The values are
  /// validated before any identity request is made.
  static const emailRequiredMessage = 'Enter your email address.';
  static const emailInvalidMessage = 'Enter a valid email address.';
  static const passwordRequiredMessage = 'Enter your password.';
  static const signInFailedMessage = 'Unable to sign in. Please try again.';

  static final RegExp _emailPattern = RegExp(
    r'^[A-Za-z0-9.!#$%&*+/=?^_`{|}~-]+@'
    r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
    r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$',
  );

  /// Validates the submitted credentials without contacting the identity
  /// service. The password is never trimmed or otherwise rewritten here.
  static String? validateSignInInput(String email, String password) {
    final trimmedEmail = email.trim();
    if (trimmedEmail.isEmpty) return emailRequiredMessage;
    if (!_emailPattern.hasMatch(trimmedEmail)) return emailInvalidMessage;
    if (password.isEmpty) return passwordRequiredMessage;
    return null;
  }

  Future<bool> signIn(String email, String password) async {
    final trimmedEmail = email.trim();
    final validationError = validateSignInInput(trimmedEmail, password);
    if (validationError != null) {
      error = validationError;
      notifyListeners();
      return Future.value(false);
    }
    return _run(fallbackMessage: signInFailedMessage, () async {
      currentUser = await repository.signIn(trimmedEmail, password);
      if (currentUser?.status == UserStatus.active) {
        await refresh();
        requestedAlertId = await repository.takeInitialAlertId();
      }
    });
  }

  /// Shown when a reset is requested without an address. The request is
  /// rejected locally, so the identity service is never contacted.
  static const resetEmailRequiredMessage =
      'Enter your email address to request a reset link.';

  Future<bool> sendPasswordReset(String email) {
    if (email.trim().isEmpty) {
      error = resetEmailRequiredMessage;
      notifyListeners();
      return Future.value(false);
    }
    return _run(() => repository.sendPasswordReset(email));
  }

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
      final runningUserId = _refreshUserId;
      await running;
      if (currentUser?.id != runningUserId ||
          (includeAdministration && !includesAdministration)) {
        await _refreshData(includeAdministration: includeAdministration);
      }
      return;
    }
    _refreshIncludesAdministration = includeAdministration;
    _refreshUserId = currentUser?.id;
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
    AppUser? session;
    try {
      session = currentUser == null ? null : await repository.refreshSession();
    } catch (exception) {
      if (!_disposed && currentUser?.id == originalUserId) {
        if (exception is GuardAidRequestException &&
            (exception.statusCode == 401 || exception.statusCode == 403)) {
          _clearProtectedData();
        }
        refreshErrors['session'] =
            'Session could not be verified. Data is not current.';
        notifyListeners();
      }
      rethrow;
    }
    if (_disposed || currentUser?.id != originalUserId) return;
    if (session != null) {
      if (session.status != UserStatus.active) {
        currentUser = session;
        _clearProtectedData();
        notifyListeners();
        return;
      }
    }
    currentUser = session;
    refreshErrors.remove('session');
    if (session?.permissions.manageUsers != true) {
      users = const [];
    }
    if (session?.permissions.manageBorderlineSettings != true) {
      borderlineSettings = null;
    }
    bool denied = false;
    Object? feedFailure;
    Future<void> load<T>(
      String key,
      Future<T> Function() request,
      void Function(T) commit,
    ) async {
      try {
        final value = await request();
        if (_disposed || currentUser?.id != originalUserId || denied) return;
        commit(value);
        refreshErrors.remove(key);
        lastRefreshSuccess[key] = DateTime.now();
      } catch (exception) {
        if (_disposed || currentUser?.id != originalUserId) return;
        if (exception is GuardAidRequestException &&
            (exception.statusCode == 401 || exception.statusCode == 403)) {
          denied = true;
          _clearProtectedData();
        }
        refreshErrors[key] = 'Unable to refresh $key.';
        if (key == 'alerts' || denied) feedFailure = exception;
      }
      if (!_disposed && currentUser?.id == originalUserId) notifyListeners();
    }

    await Future.wait<void>([
      load('alerts', repository.loadAlerts, (value) {
        alerts = value
            .map(
              (alert) => _readAlerts.contains(alert.id)
                  ? alert.copyWith(isRead: true)
                  : alert,
            )
            .toList(growable: false);
      }),
      load(
        'notifications',
        repository.loadNotifications,
        (value) => notifications = value,
      ),
      load(
        'cameras',
        repository.loadMonitoredCameraCount,
        (value) => monitoredCameraCount = value,
      ),
      if (includeAdministration)
        load('roles', repository.loadRoles, (value) => roles = value),
      if (includeAdministration && session?.permissions.manageUsers == true)
        load('users', repository.loadUsers, (value) => users = value),
      if (includeAdministration &&
          session?.permissions.manageBorderlineSettings == true)
        load('borderline settings', repository.loadBorderlineSettings, (value) {
          borderlineSettings = value;
          borderlineEnabled = value.reviewEnabled;
          borderlineRecipientRoleIds = Set.of(value.recipientRoleIds);
        }),
    ]);
    if (_disposed || currentUser?.id != originalUserId) return;
    await _ensureWorkflowRequests();
    if (!_disposed) notifyListeners();
    if (feedFailure != null) throw feedFailure!;
  }

  void _clearProtectedData() {
    alerts = const [];
    notifications = const [];
    roles = const [];
    users = const [];
    borderlineSettings = null;
    monitoredCameraCount = null;
    refreshErrors.clear();
    lastRefreshSuccess.clear();
  }

  void _syncReadState() {
    if (_readStateUserId == currentUser?.id) return;
    _readStateUserId = currentUser?.id;
    _readAlerts.clear();
    monitoredCameraCount = null;
    refreshErrors.clear();
    lastRefreshSuccess.clear();
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
    // The navigation target belongs to the session that requested it; keeping
    // it would open a previous account's alert after the next sign-in.
    requestedAlertId = null;
    _clearProtectedData();
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
    if (!_workflowInFlight.add(alertId)) {
      throw const FormatException(
        'A response is already being sent. Please wait.',
      );
    }
    try {
      final submittingUserId = currentUser?.id;
      await _ensureWorkflowRequests();
      final userId = currentUser?.id;
      if (userId != submittingUserId) {
        throw const FormatException(
          'Your account changed. Review the incident before responding.',
        );
      }
      final currentAlert = alerts
          .where((item) => item.id == alertId)
          .firstOrNull;
      if (userId == null || currentAlert == null) {
        throw StateError('Alert not found.');
      }
      var request = _pendingWorkflowRequests[alertId];
      if (request != null &&
          !request.matches(action, comment, recipientRoleIds)) {
        throw const FormatException(
          'A previous response is still awaiting confirmation. Retry that response before submitting a different one.',
        );
      }
      if (request == null) {
        request = PendingWorkflowRequest.create(
          alertId: alertId,
          action: action,
          expectedRevision: currentAlert.workflowRevision,
          comment: comment,
          roleIds: recipientRoleIds,
        );
        await _workflowRequestStore.save(userId, request);
        if (currentUser?.id != userId) {
          throw const FormatException(
            'Your account changed. Review the incident before responding.',
          );
        }
        _pendingWorkflowRequests[alertId] = request;
        if (!_disposed) notifyListeners();
      }
      late final WorkflowSubmissionResult result;
      try {
        result = await repository.submitWorkflowAction(
          alertId,
          request.action,
          comment: request.comment,
          recipientRoleIds: request.roleIds,
          idempotencyKey: request.key,
          expectedRevision: request.expectedRevision,
        );
      } on GuardAidConflictException {
        await _forgetWorkflowRequest(userId, alertId);
        try {
          await refreshAlertFeed();
        } catch (_) {
          /* Preserve the specific conflict message. */
        }
        rethrow;
      } on GuardAidRequestException catch (exception) {
        if (exception.statusCode >= 400 &&
            exception.statusCode < 500 &&
            !{401, 403, 408, 429}.contains(exception.statusCode)) {
          await _forgetWorkflowRequest(userId, alertId);
          rethrow;
        }
        throw const FormatException(
          'The server response could not be confirmed. Use Retry response to check whether your response was saved.',
        );
      } catch (_) {
        throw const FormatException(
          'The server response could not be confirmed. Use Retry response to check whether your response was saved.',
        );
      }
      // A committed response stays successful even if the following feed refresh fails.
      try {
        await _forgetWorkflowRequest(userId, alertId);
      } catch (_) {
        /* Retrying the saved request is safe. */
      }
      if (currentUser?.id == userId && result.updatedAlert != null) {
        alerts = alerts
            .map((item) => item.id == alertId ? result.updatedAlert! : item)
            .toList();
      }
      try {
        if (currentUser?.id == userId) await refresh();
      } catch (_) {
        /* The write was acknowledged. */
      }
      return result;
    } finally {
      _workflowInFlight.remove(alertId);
    }
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

  Future<bool> _run(
    Future<void> Function() task, {
    String? fallbackMessage,
  }) async {
    isBusy = true;
    error = null;
    notifyListeners();
    try {
      await task();
      return true;
    } catch (exception) {
      error = _errorMessage(exception, fallbackMessage: fallbackMessage);
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

  String _errorMessage(Object exception, {String? fallbackMessage}) =>
      switch (exception) {
        FormatException(:final message) => message,
        TimeoutException(:final message) when message != null => message,
        _ =>
          fallbackMessage ??
              exception.toString().replaceFirst('Exception: ', ''),
      };
}
