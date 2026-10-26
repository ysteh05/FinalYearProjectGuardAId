import 'dart:convert';

import 'models.dart';

class GuardAidConflictException implements Exception {
  const GuardAidConflictException(
    this.message, {
    this.code = 'workflow_changed',
  });

  final String message;
  final String code;

  @override
  String toString() => message;
}

abstract class GuardAidRequestException implements Exception {
  const GuardAidRequestException(this.statusCode, this.message);
  final int statusCode;
  final String message;
  @override
  String toString() => message;
}

abstract interface class GuardAidRepository {
  bool get isDemo;
  Stream<String> get openedAlertIds;
  Future<String?> takeInitialAlertId();
  Future<AppUser> signIn(String email, String password);
  Future<AppUser> refreshSession();
  Future<void> sendPasswordReset(String email);
  Future<void> signOut();
  Future<List<GuardAlert>> loadAlerts();
  Future<int> loadMonitoredCameraCount();
  Future<GuardAlert> loadAlertDetails(String alertId);
  Future<void> markAlertRead(String alertId);
  Future<List<AppNotification>> loadNotifications();
  Future<List<GuardRole>> loadRoles();
  Future<List<AppUser>> loadUsers();
  Future<void> submitAlertAction(
    String alertId,
    AlertStatus status, {
    required String reason,
    List<String> recipientRoleIds = const [],
  });
  Future<List<BroadcastRoleOption>> loadBroadcastRoleOptions(String alertId);
  Future<WorkflowSubmissionResult> submitWorkflowAction(
    String alertId,
    String action, {
    String? comment,
    List<String> recipientRoleIds = const [],
    required String idempotencyKey,
    required int expectedRevision,
  });
  Future<void> recordSimulatedEmergencyCall(String alertId);
  Future<void> markAllNotificationsRead();
  Future<void> updateRoleSensitivity(String roleId, SensitivityMode mode);
  Future<void> createRole({
    required String name,
    required String description,
    required SensitivityMode sensitivity,
    required RolePermissions permissions,
  });
  Future<void> deleteRole(String roleId);
  Future<void> updateUserAssignment(
    String userId, {
    String? roleId,
    required UserStatus status,
  });
  Future<UserProvisioningResult> createUser({
    required String displayName,
    required String email,
    required String roleId,
    required bool linkExisting,
  });
  Future<bool> sendUserSetupEmail(String email);
  Future<void> deactivateUser(String userId);
  Future<void> updateProfile(String displayName);
  Future<void> updateBorderlineSettings({
    required bool enabled,
    required Set<String> recipientRoleIds,
  });
  Future<BorderlineSettings> loadBorderlineSettings();
  Future<BorderlineSettings> setBorderlineRoleEnabled(
    String roleId,
    bool enabled,
    int revision,
  );
  Future<BorderlineSettings> setBorderlineUserEnabled(
    String userId,
    bool enabled,
    int revision,
  );
}

class DemoGuardAidRepository implements GuardAidRepository {
  // Independent demo camera inventory; never derive camera totals from alerts.
  static const _cameraIds = {'demo_north_gate', 'demo_canteen', 'demo_corridor'};

  @override
  Future<int> loadMonitoredCameraCount() async => _cameraIds.length;

  DemoGuardAidRepository({
    this.firstReminderDelay = const Duration(minutes: 1),
    this.allUsersReminderDelay = const Duration(minutes: 2),
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now {
    final now = _clock();
    _alerts = [
      GuardAlert(
        id: 'alert_weapon_01',
        cameraName: 'North Gate Camera',
        location: 'North Gate',
        type: AlertType.weapon,
        status: AlertStatus.open,
        confidence: 0.94,
        sensitivity: SensitivityMode.moderate,
        detectedAt: now.subtract(const Duration(minutes: 3)),
        descriptionStatus: 'ready',
        sceneDescription:
            'A person is holding a knife-shaped object near the north gate. Two other people move away from the area.',
        sceneSummary: const AiSceneSummary(
          description: 'An object appears near a person’s hand.',
          visibleEvidence: [
            'A person stands near the north gate.',
            'Two people move away from the area.',
          ],
          uncertainty: 'medium',
        ),
        evidence: const [
          EvidenceRecord(
            id: 'evidence_weapon_01',
            mediaType: 'weapon_screenshot',
          ),
        ],
      ),
      GuardAlert(
        id: 'alert_violence_01',
        cameraName: 'Block B Corridor',
        location: 'Block B · Level 2',
        type: AlertType.violence,
        status: AlertStatus.inReview,
        workflowStatus: AlertWorkflowStatus.confirmedAwaitingHandler,
        broadcastRoleIds: const ['role_security_personnel'],
        confidence: 0.82,
        sensitivity: SensitivityMode.moderate,
        detectedAt: now.subtract(const Duration(minutes: 18)),
        descriptionStatus: 'ready',
        sceneDescription:
            'Two students make repeated forceful physical contact. One student appears to push the other against a wall.',
        sceneSummary: const AiSceneSummary(
          description: 'Possible physical contact between two people.',
          visibleEvidence: [
            'Two people are visible in the corridor.',
            'One person moves an arm toward the other.',
          ],
          uncertainty: 'medium',
        ),
        evidence: const [
          EvidenceRecord(
            id: 'evidence_violence_01',
            mediaType: 'violence_clip',
            startSeconds: 15,
            endSeconds: 25,
          ),
        ],
      ),
      GuardAlert(
        id: 'alert_bullying_01',
        cameraName: 'Cafeteria Camera 2',
        location: 'Main Cafeteria',
        type: AlertType.physicalContactBullying,
        status: AlertStatus.open,
        confidence: 0.61,
        sensitivity: SensitivityMode.sensitive,
        detectedAt: now.subtract(const Duration(hours: 1, minutes: 8)),
        descriptionStatus: 'pending',
        isBorderline: true,
        borderlineReviewStatus: 'queued',
        evidence: const [
          EvidenceRecord(
            id: 'evidence_bullying_01',
            mediaType: 'bullying_clip',
            startSeconds: 40,
            endSeconds: 50,
          ),
        ],
      ),
      GuardAlert(
        id: 'alert_resolved_01',
        cameraName: 'Sports Hall Entrance',
        location: 'Sports Hall',
        type: AlertType.violence,
        status: AlertStatus.resolved,
        workflowStatus: AlertWorkflowStatus.resolved,
        confidence: 0.88,
        sensitivity: SensitivityMode.certain,
        detectedAt: now.subtract(const Duration(days: 1, hours: 2)),
        descriptionStatus: 'ready',
        sceneDescription:
            'A brief physical confrontation occurs near the entrance before staff members intervene.',
        sceneSummary: const AiSceneSummary(
          description:
              'A brief physical confrontation occurs near an entrance.',
          visibleEvidence: [
            'Two people move close to each other.',
            'Other people approach the scene.',
          ],
          uncertainty: 'low',
        ),
        actionNote: 'Resolved after on-site staff intervention.',
        actions: [
          AlertActionRecord(
            action: 'resolve',
            performedBy: 'Alex Morgan',
            note: 'Resolved after on-site staff intervention.',
            createdAt: now.subtract(
              const Duration(days: 1, hours: 1, minutes: 58),
            ),
          ),
        ],
      ),
      GuardAlert(
        id: 'alert_dismissed_01',
        cameraName: 'Playground East',
        location: 'East Playground',
        type: AlertType.physicalContactBullying,
        status: AlertStatus.dismissed,
        workflowStatus: AlertWorkflowStatus.dismissed,
        confidence: 0.58,
        sensitivity: SensitivityMode.sensitive,
        detectedAt: now.subtract(const Duration(days: 2, hours: 3)),
        descriptionStatus: 'ready',
        sceneDescription:
            'Students appear to be playing a supervised contact sport. No harmful interaction is visible.',
        sceneSummary: const AiSceneSummary(
          description:
              'People appear to be playing a supervised contact sport.',
          visibleEvidence: [
            'Several people are visible on the playground.',
            'No harmful interaction is clearly visible.',
          ],
          uncertainty: 'medium',
        ),
        actionNote: 'Dismissed as supervised sports activity.',
        actions: [
          AlertActionRecord(
            action: 'dismiss',
            performedBy: 'Alex Morgan',
            note: 'Dismissed as supervised sports activity.',
            createdAt: now.subtract(
              const Duration(days: 2, hours: 2, minutes: 58),
            ),
          ),
        ],
      ),
    ];
    _notifications = [
      AppNotification(
        id: 'notification_01',
        alertId: 'alert_weapon_01',
        title: 'Weapon alert detected',
        message: 'North Gate Camera · Immediate review required',
        createdAt: now.subtract(const Duration(minutes: 3)),
      ),
      AppNotification(
        id: 'notification_02',
        alertId: 'alert_violence_01',
        title: 'Incident is under review',
        message: 'Block B Corridor · Review started by Security Team',
        createdAt: now.subtract(const Duration(minutes: 15)),
      ),
      AppNotification(
        id: 'notification_03',
        alertId: 'alert_resolved_01',
        title: 'Incident resolved',
        message: 'Sports Hall Entrance · Resolution recorded',
        createdAt: now.subtract(const Duration(days: 1, hours: 2)),
        isRead: true,
      ),
    ];
    _reminderPhases['alert_weapon_01'] = _DemoReminderPhase(
      kind: 'awaiting_decision',
      startedAt: now.subtract(const Duration(minutes: 3)),
      roleIds: const [],
      initiatedBy: null,
    );
  }

  late List<GuardAlert> _alerts;
  late List<AppNotification> _notifications;
  final DateTime Function() _clock;
  final Duration firstReminderDelay;
  final Duration allUsersReminderDelay;
  final Map<String, _DemoReminderPhase> _reminderPhases = {};
  final Map<String, String> _completedWorkflowKeys = {};
  final List<GuardRole> _roles = [
    const GuardRole(
      id: 'role_school_administrator',
      name: 'School Administrator',
      description: 'Manage users, roles, thresholds and incident response.',
      sensitivity: SensitivityMode.moderate,
      userCount: 1,
      isAdmin: true,
    ),
    const GuardRole(
      id: 'role_security_personnel',
      name: 'Security Personnel',
      description: 'Receive, review and escalate operational alerts.',
      sensitivity: SensitivityMode.sensitive,
      userCount: 3,
    ),
    const GuardRole(
      id: 'role_teacher',
      name: 'Teacher',
      description: 'Receive relevant alerts and support incident review.',
      sensitivity: SensitivityMode.certain,
      userCount: 12,
    ),
  ];
  final List<AppUser> _users = [
    const AppUser(
      id: 'user_admin',
      email: 'admin@guardaid.demo',
      displayName: 'Alex Morgan',
      status: UserStatus.active,
      roleId: 'role_school_administrator',
      roleName: 'School Administrator',
      sensitivity: SensitivityMode.moderate,
      isAdmin: true,
      permissions: RolePermissions(
        manageRoles: true,
        manageUsers: true,
        manageBorderlineSettings: true,
      ),
      borderlineReviewAvailable: true,
      borderlineReviewEnabledForAccount: true,
    ),
    const AppUser(
      id: 'user_security',
      email: 'security@guardaid.demo',
      displayName: 'Jordan Lee',
      status: UserStatus.active,
      roleId: 'role_security_personnel',
      roleName: 'Security Personnel',
      sensitivity: SensitivityMode.sensitive,
      borderlineReviewAvailable: true,
      borderlineReviewEnabledForAccount: true,
    ),
    const AppUser(
      id: 'user_pending',
      email: 'teacher@guardaid.demo',
      displayName: 'Taylor Wong',
      status: UserStatus.pending,
    ),
  ];

  bool borderlineEnabled = true;
  Set<String> borderlineRecipients = {
    'role_school_administrator',
    'role_security_personnel',
  };

  @override
  bool get isDemo => true;

  @override
  Stream<String> get openedAlertIds => const Stream.empty();

  @override
  Future<String?> takeInitialAlertId() async => null;

  @override
  Future<AppUser> signIn(String email, String password) async {
    await Future<void>.delayed(const Duration(milliseconds: 350));
    if (email.trim().toLowerCase() == 'admin@guardaid.demo' &&
        password == 'GuardAId123!') {
      return _users.first;
    }
    throw const FormatException('Incorrect email or password.');
  }

  @override
  Future<AppUser> refreshSession() async => _users.first;

  @override
  Future<void> sendPasswordReset(String email) async {
    await Future<void>.delayed(const Duration(milliseconds: 250));
    if (!email.contains('@')) {
      throw const FormatException('Enter a valid email address.');
    }
  }

  @override
  Future<void> signOut() async {}

  @override
  Future<List<GuardAlert>> loadAlerts() async => List.unmodifiable(_alerts);

  @override
  Future<GuardAlert> loadAlertDetails(String alertId) async =>
      _alerts.firstWhere((alert) => alert.id == alertId);

  @override
  Future<void> markAlertRead(String alertId) async {
    final index = _alerts.indexWhere((alert) => alert.id == alertId);
    if (index < 0) throw StateError('Alert not found.');
    _alerts[index] = _alerts[index].copyWith(isRead: true);
  }

  @override
  Future<List<AppNotification>> loadNotifications() async {
    await processDueReminders();
    return List.unmodifiable(_notifications);
  }

  @override
  Future<List<GuardRole>> loadRoles() async => List.unmodifiable(_roles);

  @override
  Future<List<AppUser>> loadUsers() async => List.unmodifiable(_users);

  @override
  Future<void> submitAlertAction(
    String alertId,
    AlertStatus status, {
    required String reason,
    List<String> recipientRoleIds = const [],
  }) async {
    final index = _alerts.indexWhere((alert) => alert.id == alertId);
    if (index < 0) throw StateError('Alert not found.');
    final target = _alerts[index];
    final actionWord = switch (status) {
      AlertStatus.dismissed => 'dismissed',
      AlertStatus.resolved => 'resolved',
      AlertStatus.escalated => 'escalated',
      _ => 'updated',
    };
    _alerts[index] = _alerts[index].copyWith(
      status: status,
      actionNote: reason.trim().isEmpty ? null : reason.trim(),
      actions: [
        ..._alerts[index].actions,
        AlertActionRecord(
          action: switch (status) {
            AlertStatus.dismissed => 'dismiss',
            AlertStatus.resolved => 'resolve',
            AlertStatus.escalated => 'escalate',
            _ => 'update',
          },
          performedBy: 'Alex Morgan',
          note: reason.trim().isEmpty ? null : reason.trim(),
          createdAt: DateTime.now(),
        ),
      ],
    );
    _notifications = [
      AppNotification(
        id: 'notification_${DateTime.now().microsecondsSinceEpoch}',
        alertId: alertId,
        title: 'Alert ${status.label.toLowerCase()}',
        message:
            'Alex Morgan $actionWord the ${target.type.label} alert at ${target.cameraName}.',
        createdAt: DateTime.now(),
      ),
      ..._notifications,
    ];
  }

  @override
  Future<List<BroadcastRoleOption>> loadBroadcastRoleOptions(
    String alertId,
  ) async {
    final alert = _alerts.where((item) => item.id == alertId).firstOrNull;
    if (alert == null) throw StateError('Alert not found.');
    int rank(SensitivityMode mode) => switch (mode) {
      SensitivityMode.sensitive => 0,
      SensitivityMode.moderate => 1,
      SensitivityMode.certain => 2,
    };
    return _roles
        .map((role) {
          final activeMembers = _users
              .where(
                (user) =>
                    user.status == UserStatus.active && user.roleId == role.id,
              )
              .toList(growable: false);
          final activeCount = activeMembers.length;
          final eligibleCount = activeMembers
              .where((user) => user.id != currentUserId)
              .length;
          return BroadcastRoleOption(
            id: role.id,
            name: role.name,
            sensitivity: role.sensitivity,
            activeUserCount: activeCount,
            eligibleDeviceUserCount: eligibleCount,
            defaultSelected: rank(role.sensitivity) <= rank(alert.sensitivity),
          );
        })
        .toList(growable: false);
  }

  @override
  Future<WorkflowSubmissionResult> submitWorkflowAction(
    String alertId,
    String action, {
    String? comment,
    List<String> recipientRoleIds = const [],
    required String idempotencyKey,
    required int expectedRevision,
  }) async {
    final canonicalRoles = recipientRoleIds.toSet().toList()..sort();
    final fingerprint = jsonEncode([
      currentUserId,
      action,
      comment == null || comment.trim().isEmpty ? null : comment.trim(),
      canonicalRoles,
    ]);
    final previous = _completedWorkflowKeys[idempotencyKey];
    if (previous != null) {
      if (previous != fingerprint) {
        throw const GuardAidConflictException(
          'This request number was already used for a different response. Review the incident before trying again.',
          code: 'idempotency_payload_mismatch',
        );
      }
      return WorkflowSubmissionResult(
        idempotent: true,
        updatedAlert: _alerts.firstWhere((item) => item.id == alertId),
      );
    }
    final index = _alerts.indexWhere((item) => item.id == alertId);
    if (index < 0) throw StateError('Alert not found.');
    final current = _alerts[index];
    if (current.workflowRevision != expectedRevision) {
      final statusMessage = switch (current.workflowStatus) {
        AlertWorkflowStatus.resolved =>
          'This incident has already been resolved.',
        AlertWorkflowStatus.dismissed =>
          'This alert has already been dismissed as a false alarm.',
        AlertWorkflowStatus.confirmedAwaitingHandler =>
          'This incident has already been confirmed and is awaiting a handler.',
        AlertWorkflowStatus.inProgress => 'This incident is now being handled.',
        _ => 'The incident response has changed.',
      };
      throw GuardAidConflictException(
        '$statusMessage Review the latest details before responding.',
      );
    }
    final cleanComment = comment?.trim();
    final note = cleanComment == null || cleanComment.isEmpty
        ? null
        : cleanComment;
    final selectedRoles = recipientRoleIds.toSet().toList(growable: false);
    if (action == 'confirm_only') {
      if (selectedRoles.isEmpty) {
        throw StateError('Select at least one Broadcast Role.');
      }
      final validRoleIds = _roles.map((role) => role.id).toSet();
      if (selectedRoles.any((roleId) => !validRoleIds.contains(roleId))) {
        throw StateError('One or more selected Roles are unavailable.');
      }
      // Device availability is not a prerequisite for recording the incident
      // decision; later reminder delivery rechecks current eligibility.
    }
    final broadcastEligibleUsers = action == 'confirm_only'
        ? _users
              .where(
                (user) =>
                    user.status == UserStatus.active &&
                    user.id != currentUserId &&
                    selectedRoles.contains(user.roleId),
              )
              .map((user) => user.id)
              .toSet()
              .length
        : 0;
    final allowed = switch (action) {
      'dismiss_false_alarm' ||
      'confirm_only' ||
      'confirm_and_handle' ||
      'confirm_resolved' =>
        current.workflowStatus == AlertWorkflowStatus.awaitingConfirmation,
      'take_ownership' =>
        current.workflowStatus == AlertWorkflowStatus.confirmedAwaitingHandler,
      'resolve' =>
        current.workflowStatus == AlertWorkflowStatus.inProgress &&
            current.handlerUserId == currentUserId,
      _ => false,
    };
    if (!allowed) {
      throw StateError(
        'This action is not available in the alert\'s current state.',
      );
    }
    _completedWorkflowKeys[idempotencyKey] = fingerprint;
    final nextWorkflow = switch (action) {
      'dismiss_false_alarm' => AlertWorkflowStatus.dismissed,
      'confirm_only' => AlertWorkflowStatus.confirmedAwaitingHandler,
      'confirm_and_handle' ||
      'take_ownership' => AlertWorkflowStatus.inProgress,
      'confirm_resolved' || 'resolve' => AlertWorkflowStatus.resolved,
      _ => current.workflowStatus,
    };
    final nextStatus = switch (nextWorkflow) {
      AlertWorkflowStatus.awaitingConfirmation => AlertStatus.open,
      AlertWorkflowStatus.confirmedAwaitingHandler ||
      AlertWorkflowStatus.inProgress ||
      AlertWorkflowStatus.legacyInReview => AlertStatus.inReview,
      AlertWorkflowStatus.resolved => AlertStatus.resolved,
      AlertWorkflowStatus.dismissed => AlertStatus.dismissed,
      AlertWorkflowStatus.legacyEscalated => AlertStatus.escalated,
    };
    final actorName = currentUserName;
    final actionAt = _clock();
    _alerts[index] = current.copyWith(
      status: nextStatus,
      workflowStatus: nextWorkflow,
      handlerUserId:
          action == 'confirm_and_handle' || action == 'take_ownership'
          ? currentUserId
          : current.handlerUserId,
      handlerName: action == 'confirm_and_handle' || action == 'take_ownership'
          ? actorName
          : current.handlerName,
      broadcastRoleIds: action == 'confirm_only'
          ? selectedRoles
          : current.broadcastRoleIds,
      workflowRevision: current.workflowRevision + 1,
      actionNote: note,
      actions: [
        ...current.actions,
        AlertActionRecord(
          action: action,
          performedBy: actorName,
          note: note,
          broadcastRoleIds: selectedRoles,
          createdAt: actionAt,
        ),
      ],
    );
    if (action == 'confirm_only') {
      _reminderPhases[alertId] = _DemoReminderPhase(
        kind: 'awaiting_handler',
        startedAt: actionAt,
        roleIds: selectedRoles,
        initiatedBy: currentUserId,
      );
    } else if (action != 'simulate_emergency_call') {
      _reminderPhases.remove(alertId);
    }
    // Demo mode has one signed-in actor, so it must not fabricate a push
    // update back to that same actor. The action remains visible in the alert
    // history; reminder simulations represent system-generated notices.
    return WorkflowSubmissionResult(
      updatedAlert: _alerts[index],
      broadcastEligibleUsers: broadcastEligibleUsers,
      notificationsQueued: broadcastEligibleUsers > 0 ? 1 : 0,
      broadcastDispatchesQueued: broadcastEligibleUsers > 0 ? 1 : 0,
    );
  }

  String get currentUserId => 'user_admin';
  String get currentUserName => 'Alex Morgan';
  String get currentUserRoleId => 'role_school_administrator';

  /// Deterministic demo/test clock entry point for the two reminder stages.
  /// Demo mode never sends FCM; this only adds simulated entries to Updates.
  Future<int> processDueReminders({DateTime? now}) async {
    final currentTime = now ?? _clock();
    var generated = 0;
    for (final entry in _reminderPhases.entries.toList()) {
      final alert = _alerts.where((item) => item.id == entry.key).firstOrNull;
      final phase = entry.value;
      if (alert == null ||
          (phase.kind == 'awaiting_decision' &&
              alert.workflowStatus !=
                  AlertWorkflowStatus.awaitingConfirmation) ||
          (phase.kind == 'awaiting_handler' &&
              alert.workflowStatus !=
                  AlertWorkflowStatus.confirmedAwaitingHandler)) {
        _reminderPhases.remove(entry.key);
        continue;
      }
      final elapsed = currentTime.difference(phase.startedAt);
      final stage = elapsed >= allUsersReminderDelay
          ? 2
          : elapsed >= firstReminderDelay
          ? 1
          : 0;
      if (stage == 0 || phase.completedStages.contains(stage)) continue;
      final actionAt = currentTime;
      final audienceText = stage == 2
          ? 'All active demo users are included.'
          : phase.kind == 'awaiting_handler'
          ? 'The selected Broadcast Roles are included.'
          : 'The alert sensitivity audience is included.';
      _notifications = [
        AppNotification(
          id: 'demo_reminder_${entry.key}_$stage',
          alertId: entry.key,
          title: stage == 1
              ? 'Alert reminder · Demo'
              : 'All-user alert reminder · Demo',
          message: phase.kind == 'awaiting_handler'
              ? 'No handler has taken ownership of the ${alert.type.label} incident at ${alert.cameraName}. $audienceText Demo notification only.'
              : 'No response was recorded for the ${alert.type.label} alert at ${alert.cameraName}. $audienceText Demo notification only.',
          createdAt: actionAt,
        ),
        ..._notifications,
      ];
      phase.completedStages.add(stage);
      if (stage == 2) _reminderPhases.remove(entry.key);
      generated++;
    }
    return generated;
  }

  @override
  Future<void> recordSimulatedEmergencyCall(String alertId) async {
    final index = _alerts.indexWhere((item) => item.id == alertId);
    if (index < 0) throw StateError('Alert not found.');
    final now = _clock();
    final alert = _alerts[index];
    _alerts[index] = alert.copyWith(
      actions: [
        ...alert.actions,
        AlertActionRecord(
          action: 'simulated_emergency_call',
          performedBy: currentUserName,
          note: 'No real call was made.',
          createdAt: now,
        ),
      ],
    );
  }

  @override
  Future<void> markAllNotificationsRead() async {
    _notifications = _notifications
        .map((item) => item.copyWith(isRead: true))
        .toList();
  }

  @override
  Future<void> updateRoleSensitivity(
    String roleId,
    SensitivityMode mode,
  ) async {
    final index = _roles.indexWhere((role) => role.id == roleId);
    if (index < 0) throw StateError('Role not found.');
    _roles[index] = _roles[index].copyWith(sensitivity: mode);
  }

  @override
  Future<void> createRole({
    required String name,
    required String description,
    required SensitivityMode sensitivity,
    required RolePermissions permissions,
  }) async {
    if (_roles.any(
      (role) => role.name.toLowerCase() == name.trim().toLowerCase(),
    )) {
      throw StateError('A role with this name already exists.');
    }
    _roles.add(
      GuardRole(
        id: 'role_custom_${DateTime.now().microsecondsSinceEpoch}',
        name: name.trim(),
        description: description.trim(),
        sensitivity: sensitivity,
        userCount: 0,
        isAdmin: false,
        isSystem: false,
        permissions: permissions,
      ),
    );
  }

  @override
  Future<void> deleteRole(String roleId) async {
    final index = _roles.indexWhere((role) => role.id == roleId);
    if (index < 0) throw StateError('Role not found.');
    final role = _roles[index];
    if (role.isSystem) throw StateError('System roles cannot be deleted.');
    for (var userIndex = 0; userIndex < _users.length; userIndex++) {
      final user = _users[userIndex];
      if (user.roleId != roleId) continue;
      _users[userIndex] = AppUser(
        id: user.id,
        email: user.email,
        displayName: user.displayName,
        status: UserStatus.pending,
      );
    }
    _roles.removeAt(index);
    borderlineRecipients.remove(roleId);
  }

  void _refreshRoleCounts() {
    for (var index = 0; index < _roles.length; index++) {
      final role = _roles[index];
      _roles[index] = role.copyWith(
        userCount: _users.where((user) => user.roleId == role.id).length,
      );
    }
  }

  @override
  Future<void> updateUserAssignment(
    String userId, {
    String? roleId,
    required UserStatus status,
  }) async {
    final index = _users.indexWhere((user) => user.id == userId);
    if (index < 0) throw StateError('User not found.');
    if (userId == 'user_admin') {
      throw StateError('You cannot change your own role or access status.');
    }
    final target = _users[index];
    GuardRole? role;
    if (roleId != null) {
      for (final candidate in _roles) {
        if (candidate.id == roleId) role = candidate;
      }
      if (role == null) throw StateError('Role not found.');
    }
    if (target.status == UserStatus.active &&
        target.isAdmin &&
        !(status == UserStatus.active && (role?.isAdmin ?? false)) &&
        _users
                .where(
                  (user) => user.status == UserStatus.active && user.isAdmin,
                )
                .length <=
            1) {
      throw StateError(
        'The last active School Administrator cannot be changed.',
      );
    }
    if (roleId == null) {
      final user = _users[index];
      _users[index] = AppUser(
        id: user.id,
        email: user.email,
        displayName: user.displayName,
        status: UserStatus.pending,
      );
      _refreshRoleCounts();
      return;
    }
    final assignedRole = role!;
    _users[index] = _users[index].copyWith(
      roleId: assignedRole.id,
      roleName: assignedRole.name,
      sensitivity: assignedRole.sensitivity,
      status: status,
      isAdmin: assignedRole.isAdmin,
      permissions: assignedRole.permissions,
    );
    _refreshRoleCounts();
  }

  @override
  Future<UserProvisioningResult> createUser({
    required String displayName,
    required String email,
    required String roleId,
    required bool linkExisting,
  }) async {
    final cleanEmail = email.trim().toLowerCase();
    final cleanName = displayName.trim();
    if (cleanName.isEmpty || !cleanEmail.contains('@')) {
      throw const FormatException('Enter a valid name and email address.');
    }
    final existingUser = _users
        .where((user) => user.email.toLowerCase() == cleanEmail)
        .firstOrNull;
    if (existingUser != null) {
      return UserProvisioningResult(
        user: existingUser,
        identityLinked: false,
        setupEmailRequested: false,
        alreadyExists: true,
        isDemo: true,
      );
    }
    GuardRole? role;
    for (final candidate in _roles) {
      if (candidate.id == roleId) role = candidate;
    }
    if (role == null) throw StateError('Role not found.');
    final user = AppUser(
      id: 'demo_user_${DateTime.now().microsecondsSinceEpoch}',
      email: cleanEmail,
      displayName: cleanName,
      status: UserStatus.active,
      roleId: role.id,
      roleName: role.name,
      sensitivity: role.sensitivity,
      isAdmin: role.isAdmin,
      permissions: role.permissions,
    );
    _users.add(user);
    _refreshRoleCounts();
    return UserProvisioningResult(
      user: user,
      identityLinked: linkExisting,
      setupEmailRequested: false,
      isDemo: true,
    );
  }

  @override
  Future<bool> sendUserSetupEmail(String email) async => false;

  @override
  Future<void> deactivateUser(String userId) async {
    final index = _users.indexWhere((user) => user.id == userId);
    if (index < 0) throw StateError('User not found.');
    if (userId == 'user_admin') {
      throw StateError('You cannot deactivate your own account.');
    }
    final user = _users[index];
    if (user.status == UserStatus.active &&
        user.isAdmin &&
        _users
                .where(
                  (item) => item.status == UserStatus.active && item.isAdmin,
                )
                .length <=
            1) {
      throw StateError(
        'The last active School Administrator cannot be deactivated.',
      );
    }
    _users[index] = user.copyWith(status: UserStatus.disabled);
    _refreshRoleCounts();
  }

  @override
  Future<void> updateProfile(String displayName) async {
    final index = _users.indexWhere((user) => user.id == 'user_admin');
    if (index >= 0) {
      _users[index] = AppUser(
        id: _users[index].id,
        email: _users[index].email,
        displayName: displayName.trim(),
        status: _users[index].status,
        roleId: _users[index].roleId,
        roleName: _users[index].roleName,
        sensitivity: _users[index].sensitivity,
        isAdmin: _users[index].isAdmin,
      );
    }
  }

  @override
  Future<void> updateBorderlineSettings({
    required bool enabled,
    required Set<String> recipientRoleIds,
  }) async {
    borderlineEnabled = enabled;
    borderlineRecipients = Set.of(recipientRoleIds);
    for (final user in _users.where((item) => item.roleId != null).toList()) {
      final receives = enabled && recipientRoleIds.contains(user.roleId);
      _borderlineUserEnabled[user.id] = receives;
      final index = _users.indexWhere((item) => item.id == user.id);
      _users[index] = user.copyWith(
        borderlineReviewAvailable: true,
        borderlineReviewEnabledForAccount: receives,
      );
    }
  }

  @override
  Future<BorderlineSettings> loadBorderlineSettings() async =>
      BorderlineSettings(
        enabled: borderlineEnabled,
        recipientRoleIds: Set.unmodifiable(borderlineRecipients),
        reviewEnabled: true,
        roles: _roles
            .map((role) {
              final members = _users
                  .where((user) => user.roleId == role.id)
                  .toList();
              final enabledCount = members
                  .where((user) => _borderlineUserEnabled[user.id] ?? false)
                  .length;
              return BorderlineRoleGroup(
                id: role.id,
                name: role.name,
                sensitivity: role.sensitivity,
                defaultEnabled: borderlineRecipients.contains(role.id),
                enabledUserCount: enabledCount,
                userCount: members.length,
                selectionState: enabledCount == 0
                    ? 'off'
                    : enabledCount == members.length
                    ? 'on'
                    : 'mixed',
                users: members
                    .map(
                      (user) => BorderlineUserSetting(
                        id: user.id,
                        displayName: user.displayName,
                        status: user.status.name,
                        enabled: _borderlineUserEnabled[user.id] ?? false,
                        settingSource: 'demo',
                      ),
                    )
                    .toList(growable: false),
              );
            })
            .toList(growable: false),
      );

  final Map<String, bool> _borderlineUserEnabled = {
    'user_admin': true,
    'user_security': true,
  };

  @override
  Future<BorderlineSettings> setBorderlineRoleEnabled(
    String roleId,
    bool enabled,
    int revision,
  ) async {
    if (!_roles.any((role) => role.id == roleId)) {
      throw StateError('Role not found.');
    }
    if (enabled) {
      borderlineRecipients.add(roleId);
    } else {
      borderlineRecipients.remove(roleId);
    }
    for (final user in _users.where((user) => user.roleId == roleId).toList()) {
      _borderlineUserEnabled[user.id] = enabled;
      final index = _users.indexWhere((item) => item.id == user.id);
      _users[index] = user.copyWith(borderlineReviewEnabledForAccount: enabled);
    }
    return loadBorderlineSettings();
  }

  @override
  Future<BorderlineSettings> setBorderlineUserEnabled(
    String userId,
    bool enabled,
    int revision,
  ) async {
    if (!_users.any((user) => user.id == userId && user.roleId != null)) {
      throw StateError('User not found or has no role.');
    }
    _borderlineUserEnabled[userId] = enabled;
    final index = _users.indexWhere((user) => user.id == userId);
    _users[index] = _users[index].copyWith(
      borderlineReviewEnabledForAccount: enabled,
    );
    return loadBorderlineSettings();
  }
}

class _DemoReminderPhase {
  _DemoReminderPhase({
    required this.kind,
    required this.startedAt,
    required this.roleIds,
    required this.initiatedBy,
  });

  final String kind;
  final DateTime startedAt;
  final List<String> roleIds;
  final String? initiatedBy;
  final Set<int> completedStages = {};
}
