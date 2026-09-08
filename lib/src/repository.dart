import 'models.dart';

abstract interface class GuardAidRepository {
  bool get isDemo;
  Stream<String> get openedAlertIds;
  Future<String?> takeInitialAlertId();
  Future<AppUser> signIn(String email, String password);
  Future<AppUser> refreshSession();
  Future<void> sendPasswordReset(String email);
  Future<void> signOut();
  Future<List<GuardAlert>> loadAlerts();
  Future<List<AppNotification>> loadNotifications();
  Future<List<GuardRole>> loadRoles();
  Future<List<AppUser>> loadUsers();
  Future<void> submitAlertAction(
    String alertId,
    AlertStatus status, {
    required String reason,
    List<String> recipientRoleIds = const [],
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
  Future<void> updateUserAssignment(
    String userId, {
    String? roleId,
    required UserStatus status,
  });
  Future<void> updateProfile(String displayName);
  Future<void> updateBorderlineSettings({
    required bool enabled,
    required Set<String> recipientRoleIds,
  });
  Future<BorderlineSettings> loadBorderlineSettings();
}

class DemoGuardAidRepository implements GuardAidRepository {
  DemoGuardAidRepository() {
    final now = DateTime.now();
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
        confidence: 0.82,
        sensitivity: SensitivityMode.moderate,
        detectedAt: now.subtract(const Duration(minutes: 18)),
        descriptionStatus: 'ready',
        sceneDescription:
            'Two students make repeated forceful physical contact. One student appears to push the other against a wall.',
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
        isProvisional: true,
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
        confidence: 0.88,
        sensitivity: SensitivityMode.certain,
        detectedAt: now.subtract(const Duration(days: 1, hours: 2)),
        descriptionStatus: 'ready',
        sceneDescription:
            'A brief physical confrontation occurs near the entrance before staff members intervene.',
        actionNote: 'Resolved after on-site staff intervention.',
      ),
      GuardAlert(
        id: 'alert_dismissed_01',
        cameraName: 'Playground East',
        location: 'East Playground',
        type: AlertType.physicalContactBullying,
        status: AlertStatus.dismissed,
        confidence: 0.58,
        sensitivity: SensitivityMode.sensitive,
        detectedAt: now.subtract(const Duration(days: 2, hours: 3)),
        descriptionStatus: 'ready',
        sceneDescription:
            'Students appear to be playing a supervised contact sport. No harmful interaction is visible.',
        actionNote: 'Dismissed as supervised sports activity.',
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
  }

  late List<GuardAlert> _alerts;
  late List<AppNotification> _notifications;
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
    ),
    const AppUser(
      id: 'user_security',
      email: 'security@guardaid.demo',
      displayName: 'Jordan Lee',
      status: UserStatus.active,
      roleId: 'role_security_personnel',
      roleName: 'Security Personnel',
      sensitivity: SensitivityMode.sensitive,
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
  Future<List<AppNotification>> loadNotifications() async =>
      List.unmodifiable(_notifications);

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
    _alerts[index] = _alerts[index].copyWith(
      status: status,
      actionNote: reason,
    );
    _notifications = [
      AppNotification(
        id: 'notification_${DateTime.now().microsecondsSinceEpoch}',
        alertId: alertId,
        title: 'Alert ${status.label.toLowerCase()}',
        message: reason,
        createdAt: DateTime.now(),
      ),
      ..._notifications,
    ];
  }

  @override
  Future<void> recordSimulatedEmergencyCall(String alertId) async {}

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
    if (_roles.any((role) => role.name.toLowerCase() == name.trim().toLowerCase())) {
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
  Future<void> updateUserAssignment(
    String userId, {
    String? roleId,
    required UserStatus status,
  }) async {
    final index = _users.indexWhere((user) => user.id == userId);
    if (index < 0) throw StateError('User not found.');
    if (roleId == null) {
      _users[index] = _users[index].copyWith(status: UserStatus.pending);
      return;
    }
    final role = _roles.firstWhere((item) => item.id == roleId);
    _users[index] = _users[index].copyWith(
      roleId: role.id,
      roleName: role.name,
      sensitivity: role.sensitivity,
      status: status,
      isAdmin: role.isAdmin,
    );
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
  }

  @override
  Future<BorderlineSettings> loadBorderlineSettings() async =>
      BorderlineSettings(
        enabled: borderlineEnabled,
        recipientRoleIds: Set.unmodifiable(borderlineRecipients),
      );
}
