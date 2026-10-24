import 'dart:async';
import 'dart:convert';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:http/http.dart' as http;

import 'models.dart';
import 'notification_service.dart';
import 'repository.dart';

class FirebaseApiGuardAidRepository implements GuardAidRepository {
  FirebaseApiGuardAidRepository({
    required String baseUrl,
    FirebaseAuth? auth,
    FirebaseMessaging? messaging,
    http.Client? client,
  }) : baseUrl = baseUrl.replaceAll(RegExp(r'/+$'), ''),
       _auth = auth ?? FirebaseAuth.instance,
       _messaging = messaging ?? FirebaseMessaging.instance,
       _client = client ?? http.Client();

  final String baseUrl;
  final FirebaseAuth _auth;
  final FirebaseMessaging _messaging;
  final http.Client _client;
  RolePermissions _permissions = const RolePermissions();
  bool _deviceRegistered = false;
  StreamSubscription<String>? _tokenRefreshSubscription;

  @override
  bool get isDemo => false;

  @override
  Stream<String> get openedAlertIds {
    final controller = StreamController<String>();
    StreamSubscription<RemoteMessage>? fcmSubscription;
    StreamSubscription<String>? localSubscription;

    void emit(String? value) {
      final alertId = value?.trim();
      if (alertId != null && alertId.isNotEmpty && !controller.isClosed) {
        controller.add(alertId);
      }
    }

    controller.onListen = () {
      fcmSubscription = FirebaseMessaging.onMessageOpenedApp.listen(
        (message) => emit(message.data['alertId']),
      );
      localSubscription = GuardAidNotificationService.openedAlertIds.listen(
        emit,
      );
    };
    controller.onCancel = () async {
      await fcmSubscription?.cancel();
      await localSubscription?.cancel();
      await controller.close();
    };
    return controller.stream;
  }

  @override
  Future<String?> takeInitialAlertId() async {
    final localAlertId = await GuardAidNotificationService.takeInitialAlertId();
    if (localAlertId != null) return localAlertId;
    final message = await _messaging.getInitialMessage();
    final alertId = message?.data['alertId'];
    return alertId == null || alertId.isEmpty ? null : alertId;
  }

  @override
  Future<AppUser> signIn(String email, String password) async {
    GuardAidNotificationService.markSignInStarted();
    try {
      await _auth.signInWithEmailAndPassword(
        email: email.trim(),
        password: password,
      );
      await _auth.currentUser?.getIdToken(true);
      final session =
          await _requestJson('GET', '/api/mobile/session')
              as Map<String, dynamic>;
      final user = _parseSession(session);
      _permissions = user.permissions;
      if (user.status == UserStatus.active) {
        _deviceRegistered = await _registerDevice();
        _tokenRefreshSubscription ??= _messaging.onTokenRefresh.listen(
          (token) async => _deviceRegistered = await _registerDevice(token),
        );
      } else {
        _deviceRegistered = false;
      }
      return user;
    } on FirebaseAuthException catch (error) {
      throw FormatException(_firebaseMessage(error));
    } catch (_) {
      await _auth.signOut();
      rethrow;
    }
  }

  @override
  Future<AppUser> refreshSession() async {
    final session =
        await _requestJson('GET', '/api/mobile/session')
            as Map<String, dynamic>;
    final user = _parseSession(session);
    _permissions = user.permissions;
    if (user.status == UserStatus.active && !_deviceRegistered) {
      _deviceRegistered = await _registerDevice();
    } else if (user.status != UserStatus.active) {
      _deviceRegistered = false;
    }
    return user;
  }

  @override
  Future<void> sendPasswordReset(String email) async {
    try {
      await _auth.sendPasswordResetEmail(email: email.trim());
    } on FirebaseAuthException catch (error) {
      throw FormatException(_firebaseMessage(error));
    }
  }

  @override
  Future<void> signOut() async {
    await _tokenRefreshSubscription?.cancel();
    _tokenRefreshSubscription = null;
    _deviceRegistered = false;
    await _auth.signOut();
  }

  @override
  Future<List<GuardAlert>> loadAlerts() async {
    final payload =
        await _requestJson('GET', '/api/mobile/alerts') as List<dynamic>;
    final token = await _requiredToken();
    return payload
        .cast<Map<String, dynamic>>()
        .map((item) => _parseAlert(item, token))
        .toList(growable: false);
  }

  @override
  Future<int> loadMonitoredCameraCount() async {
    final value = await _requestJson('GET', '/api/mobile/monitoring-summary')
        as Map<String, dynamic>;
    final count = value['monitoredCameraCount'];
    if (count is! int || count < 0) {
      throw const FormatException('Invalid camera summary.');
    }
    return count;
  }

  @override
  Future<GuardAlert> loadAlertDetails(String alertId) async {
    final value =
        await _requestJson('GET', '/api/mobile/alerts/$alertId')
            as Map<String, dynamic>;
    final token = await _requiredToken();
    return _parseAlert(value, token);
  }

  @override
  Future<List<AppNotification>> loadNotifications() async {
    final payload =
        await _requestJson('GET', '/api/mobile/notifications') as List<dynamic>;
    return payload
        .cast<Map<String, dynamic>>()
        .map((item) {
          return AppNotification(
            id: item['id'] as String,
            alertId: item['alertId'] as String,
            title: item['title'] as String,
            message: item['message'] as String,
            createdAt: DateTime.parse(item['createdAt'] as String),
            isRead: item['isRead'] as bool? ?? false,
          );
        })
        .toList(growable: false);
  }

  @override
  Future<void> markAlertRead(String alertId) async {
    await _requestJson('POST', '/api/mobile/alerts/$alertId/read');
  }

  @override
  Future<List<GuardRole>> loadRoles() async {
    final payload =
        await _requestJson('GET', '/api/mobile/roles') as List<dynamic>;
    return payload
        .cast<Map<String, dynamic>>()
        .map((item) {
          return GuardRole(
            id: item['id'] as String,
            name:
                item['displayName'] as String? ??
                _roleDisplayName(item['name'] as String),
            description: item['description'] as String? ?? '',
            sensitivity: _sensitivity(item['sensitivityMode'] as String?),
            userCount: item['userCount'] as int? ?? 0,
            isAdmin: item['isAdmin'] as bool? ?? false,
            isSystem: item['isSystem'] as bool? ?? true,
            permissions: _parsePermissions(item['permissions']),
          );
        })
        .toList(growable: false);
  }

  @override
  Future<List<AppUser>> loadUsers() async {
    if (!_permissions.manageUsers) return const [];
    final payload =
        await _requestJson('GET', '/api/mobile/admin/users') as List<dynamic>;
    return payload
        .cast<Map<String, dynamic>>()
        .map(_parseUser)
        .toList(growable: false);
  }

  @override
  Future<void> submitAlertAction(
    String alertId,
    AlertStatus status, {
    required String reason,
    List<String> recipientRoleIds = const [],
  }) async {
    final action = switch (status) {
      AlertStatus.dismissed => 'dismiss',
      AlertStatus.resolved => 'resolve',
      AlertStatus.escalated => 'escalate',
      _ => throw ArgumentError('Unsupported final alert status.'),
    };
    await _requestJson(
      'POST',
      '/api/mobile/alerts/$alertId/actions',
      body: {
        'action': action,
        'reason': reason,
        'recipientRoleIds': recipientRoleIds,
      },
    );
  }

  @override
  Future<List<BroadcastRoleOption>> loadBroadcastRoleOptions(
    String alertId,
  ) async {
    final payload =
        await _requestJson(
              'GET',
              '/api/mobile/alerts/$alertId/broadcast-preview',
            )
            as Map<String, dynamic>;
    final roles = payload['roles'] as List<dynamic>? ?? const [];
    return roles
        .cast<Map<String, dynamic>>()
        .map((item) {
          return BroadcastRoleOption(
            id: item['id'] as String,
            name: item['name'] as String? ?? 'Role',
            sensitivity: _sensitivity(item['sensitivityMode'] as String?),
            activeUserCount: item['activeUserCount'] as int? ?? 0,
            eligibleDeviceUserCount:
                item['eligibleDeviceUserCount'] as int? ?? 0,
            defaultSelected: item['defaultSelected'] as bool? ?? false,
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
    final payload =
        await _requestJson(
              'POST',
              '/api/mobile/alerts/$alertId/actions',
              body: {
                'action': action,
                'reason': comment,
                'recipientRoleIds': recipientRoleIds,
                'idempotencyKey': idempotencyKey,
                'expectedWorkflowRevision': expectedRevision,
              },
            )
            as Map<String, dynamic>;
    return WorkflowSubmissionResult(
      updatedAlert: payload['alert'] is Map<String, dynamic>
          ? _parseAlert(
              payload['alert'] as Map<String, dynamic>,
              await _requiredToken(),
            )
          : null,
      broadcastEligibleUsers: payload['broadcastEligibleUsers'] as int? ?? 0,
      notificationsQueued: payload['notificationsQueued'] as int? ?? 0,
      broadcastDispatchesQueued:
          payload['broadcastDispatchesQueued'] as int? ?? 0,
      decisionUpdateDispatchesQueued:
          payload['decisionUpdateDispatchesQueued'] as int? ?? 0,
      idempotent: payload['idempotent'] as bool? ?? false,
    );
  }

  @override
  Future<void> recordSimulatedEmergencyCall(String alertId) async {
    final payload =
        await _requestJson(
              'POST',
              '/api/mobile/alerts/$alertId/simulate-emergency-call',
            )
            as Map<String, dynamic>;
    if (payload['realCallMade'] != false || payload['diallerOpened'] != false) {
      throw StateError('The server did not confirm simulation-only behaviour.');
    }
  }

  @override
  Future<void> markAllNotificationsRead() async {
    await _requestJson('POST', '/api/mobile/notifications/mark-all-read');
  }

  @override
  Future<void> updateRoleSensitivity(
    String roleId,
    SensitivityMode mode,
  ) async {
    await _requestJson(
      'PATCH',
      '/api/mobile/admin/roles/$roleId',
      body: {'sensitivityMode': mode.name},
    );
  }

  @override
  Future<void> createRole({
    required String name,
    required String description,
    required SensitivityMode sensitivity,
    required RolePermissions permissions,
  }) async {
    await _requestJson(
      'POST',
      '/api/mobile/admin/roles',
      body: {
        'name': name.trim(),
        'description': description.trim(),
        'sensitivityMode': sensitivity.name,
        'canManageRoles': permissions.manageRoles,
        'canManageUsers': permissions.manageUsers,
        'canManageBorderlineSettings': permissions.manageBorderlineSettings,
      },
    );
  }

  @override
  Future<void> deleteRole(String roleId) async {
    await _requestJson('DELETE', '/api/mobile/admin/roles/$roleId');
  }

  @override
  Future<void> updateUserAssignment(
    String userId, {
    String? roleId,
    required UserStatus status,
  }) async {
    await _requestJson(
      'PATCH',
      '/api/mobile/admin/users/$userId',
      body: {'roleId': roleId, 'status': status.name},
    );
  }

  @override
  Future<UserProvisioningResult> createUser({
    required String displayName,
    required String email,
    required String roleId,
    required bool linkExisting,
  }) async {
    final cleanEmail = email.trim().toLowerCase();
    final payload =
        await _requestJson(
              'POST',
              '/api/mobile/admin/users',
              body: {
                'displayName': displayName.trim(),
                'email': cleanEmail,
                'roleId': roleId,
                'linkExisting': linkExisting,
              },
            )
            as Map<String, dynamic>;
    final user = _parseUser(payload);
    if (payload['identityAction'] == 'already_registered') {
      return UserProvisioningResult(
        user: user,
        identityLinked: false,
        setupEmailRequested: false,
        alreadyExists: true,
      );
    }
    try {
      await _auth.sendPasswordResetEmail(email: cleanEmail);
      return UserProvisioningResult(
        user: user,
        identityLinked: payload['identityAction'] == 'linked',
        setupEmailRequested: true,
      );
    } on FirebaseAuthException catch (error) {
      return UserProvisioningResult(
        user: user,
        identityLinked: payload['identityAction'] == 'linked',
        setupEmailRequested: false,
        setupEmailError: _firebaseMessage(error),
      );
    } catch (_) {
      return UserProvisioningResult(
        user: user,
        identityLinked: payload['identityAction'] == 'linked',
        setupEmailRequested: false,
        setupEmailError: 'The setup email could not be requested. Try again.',
      );
    }
  }

  @override
  Future<bool> sendUserSetupEmail(String email) async {
    try {
      await _auth.sendPasswordResetEmail(email: email.trim());
      return true;
    } on FirebaseAuthException catch (error) {
      throw FormatException(_firebaseMessage(error));
    }
  }

  @override
  Future<void> deactivateUser(String userId) async {
    await _requestJson('POST', '/api/mobile/admin/users/$userId/deactivate');
  }

  @override
  Future<void> updateProfile(String displayName) async {
    await _requestJson(
      'PATCH',
      '/api/mobile/profile',
      body: {'displayName': displayName.trim()},
    );
  }

  @override
  Future<void> updateBorderlineSettings({
    required bool enabled,
    required Set<String> recipientRoleIds,
  }) async {
    await _requestJson(
      'PUT',
      '/api/mobile/admin/borderline-settings',
      body: {'enabled': enabled, 'recipientRoleIds': recipientRoleIds.toList()},
    );
  }

  @override
  Future<BorderlineSettings> loadBorderlineSettings() async {
    if (!_permissions.manageBorderlineSettings) {
      return const BorderlineSettings(enabled: false, recipientRoleIds: {});
    }
    final value =
        await _requestJson('GET', '/api/mobile/admin/borderline-settings')
            as Map<String, dynamic>;
    return BorderlineSettings(
      enabled: value['enabled'] as bool? ?? false,
      recipientRoleIds: {
        ...(value['recipientRoleIds'] as List<dynamic>? ?? const [])
            .whereType<String>(),
      },
      reviewEnabled: value['reviewEnabled'] as bool? ?? false,
      revision: value['revision'] as int? ?? 1,
      roles: (value['roles'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(
            (role) => BorderlineRoleGroup(
              id: role['id'] as String,
              name:
                  role['displayName'] as String? ??
                  role['name'] as String? ??
                  'Role',
              sensitivity: _sensitivity(role['sensitivityMode'] as String?),
              defaultEnabled: role['defaultEnabled'] as bool? ?? false,
              enabledUserCount: role['enabledUserCount'] as int? ?? 0,
              userCount: role['userCount'] as int? ?? 0,
              selectionState: role['selectionState'] as String? ?? 'off',
              users: (role['users'] as List<dynamic>? ?? const [])
                  .whereType<Map<String, dynamic>>()
                  .map(
                    (user) => BorderlineUserSetting(
                      id: user['id'] as String,
                      displayName: user['displayName'] as String? ?? 'User',
                      status: user['status'] as String? ?? 'active',
                      enabled: user['enabled'] as bool? ?? false,
                      settingSource:
                          user['settingSource'] as String? ?? 'role_default',
                    ),
                  )
                  .toList(growable: false),
            ),
          )
          .toList(growable: false),
    );
  }

  @override
  Future<BorderlineSettings> setBorderlineRoleEnabled(
    String roleId,
    bool enabled,
    int revision,
  ) async => _updateBorderlineRecipient(
    '/api/mobile/admin/borderline-settings/roles/$roleId',
    enabled,
    revision,
  );

  @override
  Future<BorderlineSettings> setBorderlineUserEnabled(
    String userId,
    bool enabled,
    int revision,
  ) async => _updateBorderlineRecipient(
    '/api/mobile/admin/borderline-settings/users/$userId',
    enabled,
    revision,
  );

  Future<BorderlineSettings> _updateBorderlineRecipient(
    String path,
    bool enabled,
    int revision,
  ) async {
    await _requestJson(
      'PUT',
      path,
      body: {'enabled': enabled, 'revision': revision},
    );
    return loadBorderlineSettings();
  }

  Future<bool> _registerDevice([String? refreshedToken]) async {
    try {
      await _messaging.requestPermission(alert: true, badge: true, sound: true);
      final token = refreshedToken ?? await _messaging.getToken();
      if (token == null || token.length < 20) return false;
      await _requestJson(
        'PUT',
        '/api/mobile/device',
        body: {
          'registrationValue': token,
          'platform': 'android',
          'deviceLabel': 'GuardAId Android',
        },
      );
      return true;
    } catch (_) {
      // Login and evidence access remain usable if notification permission is denied.
      return false;
    }
  }

  Future<dynamic> _requestJson(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool forceTokenRefresh = false,
  }) async {
    final token = await _requiredToken(forceRefresh: forceTokenRefresh);
    final uri = Uri.parse('$baseUrl$path');
    final headers = <String, String>{
      'Authorization': 'Bearer $token',
      'Accept': 'application/json',
      if (body != null) 'Content-Type': 'application/json',
    };
    final responseFuture = switch (method) {
      'GET' => _client.get(uri, headers: headers),
      'POST' => _client.post(
        uri,
        headers: headers,
        body: body == null ? null : jsonEncode(body),
      ),
      'DELETE' => _client.delete(uri, headers: headers),
      'PUT' => _client.put(uri, headers: headers, body: jsonEncode(body)),
      'PATCH' => _client.patch(uri, headers: headers, body: jsonEncode(body)),
      _ => throw ArgumentError('Unsupported HTTP method $method'),
    };
    final response = await responseFuture.timeout(const Duration(seconds: 30));
    if (response.statusCode == 401 && !forceTokenRefresh) {
      return _requestJson(method, path, body: body, forceTokenRefresh: true);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      String message = 'GuardAId server returned ${response.statusCode}.';
      String code = 'workflow_changed';
      try {
        final value = jsonDecode(response.body) as Map<String, dynamic>;
        message = value['detail']?.toString() ?? message;
        code = value['code'] as String? ?? code;
      } catch (_) {}
      if (response.statusCode == 409) {
        throw GuardAidConflictException(message, code: code);
      }
      throw GuardAidApiException(response.statusCode, message);
    }
    if (response.body.isEmpty) return null;
    return jsonDecode(response.body);
  }

  Future<String> _requiredToken({bool forceRefresh = false}) async {
    final token = await _auth.currentUser?.getIdToken(forceRefresh);
    if (token == null || token.isEmpty) {
      throw const GuardAidApiException(
        401,
        'Your session has expired. Sign in again.',
      );
    }
    return token;
  }

  AppUser _parseSession(Map<String, dynamic> value) {
    final roles = (value['roles'] as List<dynamic>? ?? const [])
        .cast<Map<String, dynamic>>();
    final firstRole = roles.isEmpty ? null : roles.first;
    return AppUser(
      id: value['userId'] as String,
      email: value['email'] as String? ?? '',
      displayName:
          ((value['displayName'] as String?)?.trim().isNotEmpty ?? false)
          ? (value['displayName'] as String).trim()
          : 'Name not provided',
      status: _userStatus(value['status'] as String?),
      roleId: firstRole?['id'] as String?,
      roleName: firstRole == null
          ? null
          : firstRole['displayName'] as String? ??
                _roleDisplayName(firstRole['name'] as String),
      sensitivity: value['sensitivityMode'] == null
          ? null
          : _sensitivity(value['sensitivityMode'] as String?),
      roleSensitivities: roles
          .map((role) => _sensitivity(role['sensitivityMode'] as String?))
          .toSet()
          .toList(growable: false),
      isAdmin: value['isAdmin'] as bool? ?? false,
      borderlineReviewAvailable:
          value['borderlineReviewAvailable'] as bool? ?? false,
      borderlineReviewEnabledForAccount:
          value['borderlineReviewEnabledForAccount'] as bool? ?? false,
      borderlineReviewUnavailableReason:
          value['borderlineReviewUnavailableReason'] as String?,
      permissions: RolePermissions(
        manageRoles: roles.any(
          (role) => _parsePermissions(role['permissions']).manageRoles,
        ),
        manageUsers: roles.any(
          (role) => _parsePermissions(role['permissions']).manageUsers,
        ),
        manageBorderlineSettings: roles.any(
          (role) =>
              _parsePermissions(role['permissions']).manageBorderlineSettings,
        ),
      ),
    );
  }

  AppUser _parseUser(Map<String, dynamic> value) => AppUser(
    id: value['id'] as String,
    email: value['email'] as String? ?? '',
    displayName: ((value['displayName'] as String?)?.trim().isNotEmpty ?? false)
        ? (value['displayName'] as String).trim()
        : 'Name not provided',
    status: _userStatus(value['status'] as String?),
    roleId: value['roleId'] as String?,
    roleName: value['roleDisplayName'] == null && value['roleName'] == null
        ? null
        : value['roleDisplayName'] as String? ??
              _roleDisplayName(value['roleName'] as String),
    sensitivity: value['sensitivityMode'] == null
        ? null
        : _sensitivity(value['sensitivityMode'] as String?),
    isAdmin: value['isAdmin'] as bool? ?? false,
  );

  GuardAlert _parseAlert(Map<String, dynamic> value, String token) {
    final rawDescription = value['sceneDescription'] as String?;
    final status = _alertStatus(value['status'] as String?);
    final workflowStatus = _workflowStatus(
      value['workflowStatus'] as String?,
      legacyStatus: status,
    );
    return GuardAlert(
      id: value['id'] as String,
      cameraName: value['cameraName'] as String? ?? 'Unknown camera',
      location: value['location'] as String? ?? 'Unspecified',
      type: _alertType(value['type'] as String?),
      status: status,
      workflowStatus: workflowStatus,
      handlerUserId: value['handlerUserId'] as String?,
      handlerName: value['handlerName'] as String?,
      broadcastRoleIds:
          (value['broadcastRoleIds'] as List<dynamic>? ?? const [])
              .whereType<String>()
              .toList(growable: false),
      workflowRevision: value['workflowRevision'] as int? ?? 0,
      confidence:
          (value['maxConfidence'] as num? ?? value['confidence'] as num? ?? 0)
              .toDouble(),
      sensitivity: _sensitivity(value['sensitivityMode'] as String?),
      detectedAt: DateTime.parse(value['firstDetectedAt'] as String),
      descriptionStatus: value['descriptionStatus'] as String? ?? 'pending',
      sceneDescription: rawDescription == null
          ? null
          : readableSceneSummary(rawDescription),
      sceneSummary: AiSceneSummary.fromValue(
        value['sceneSummary'] ?? value['scene_summary'],
        legacy: rawDescription,
      ),
      isBorderline: value['isBorderlinePromoted'] as bool? ?? false,
      borderlineReviewStatus:
          value['borderlineReviewStatus'] as String? ?? 'not_applicable',
      evidenceStatus: value['evidenceStatus'] as String? ?? 'legacy_unknown',
      detectionEvents: (value['detectionEvents'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(_parseDetectionEvent)
          .toList(growable: false),
      borderlineReviewTasks:
          (value['borderlineReviewTasks'] as List<dynamic>? ?? const [])
              .whereType<Map<String, dynamic>>()
              .map(_parseBorderlineReviewTask)
              .toList(growable: false),
      notificationEligibility: _parseNotificationEligibility(
        value['notificationEligibility'],
      ),
      borderlineReviewAvailability: _parseBorderlineReviewAvailability(
        value['borderlineReviewAvailability'],
      ),
      isRead: value['isRead'] as bool? ?? false,
      evidence: (value['evidence'] as List<dynamic>? ?? const [])
          .cast<Map<String, dynamic>>()
          .map(
            (item) => EvidenceRecord(
              id: item['id'] as String,
              mediaType: item['mediaType'] as String? ?? 'evidence',
              url: item['url'] == null
                  ? null
                  : _absoluteUrl(item['url'] as String),
              startSeconds: (item['startSeconds'] as num?)?.toDouble(),
              endSeconds: (item['endSeconds'] as num?)?.toDouble(),
              httpHeaders: {'Authorization': 'Bearer $token'},
            ),
          )
          .toList(growable: false),
      actions: _parseActions(value['actions'] as List<dynamic>?),
      actionNote: _latestActionNote(value['actions'] as List<dynamic>?),
    );
  }

  AlertDetectionEvent _parseDetectionEvent(Map<String, dynamic> value) {
    final rawQualifications = value['qualifications'];
    final qualifications = <String, List<String>>{};
    if (rawQualifications is Map) {
      for (final entry in rawQualifications.entries) {
        if (entry.value is List) {
          qualifications[entry.key.toString()] = (entry.value as List)
              .map((item) => item.toString())
              .toList(growable: false);
        } else if (entry.value is Map) {
          final modes = entry.value as Map;
          for (final mode in modes.entries) {
            if (mode.value is List && (mode.value as List).isNotEmpty) {
              qualifications
                  .putIfAbsent(mode.key.toString(), () => [])
                  .addAll((mode.value as List).map((item) => item.toString()));
            }
          }
        }
      }
    }
    final rawScores = value['weaponClassScores'];
    final weaponClassScores = <String, double>{};
    if (rawScores is Map) {
      for (final entry in rawScores.entries) {
        if (entry.value is num) {
          weaponClassScores[entry.key.toString()] = (entry.value as num)
              .toDouble();
        }
      }
    }
    return AlertDetectionEvent(
      id: value['id'] as String? ?? '',
      confidence: (value['confidence'] as num? ?? 0).toDouble(),
      decisionSource: value['decisionSource'] as String? ?? 'unknown',
      configVersion: value['configVersion'] as int? ?? 0,
      modelVersion: value['modelVersion'] as String? ?? 'unknown',
      detectedAt: DateTime.tryParse(value['detectedAt'] as String? ?? ''),
      sourceStartSeconds: (value['sourceStartSeconds'] as num?)?.toDouble(),
      sourceEndSeconds: (value['sourceEndSeconds'] as num?)?.toDouble(),
      qualifications: qualifications,
      weaponClassScores: weaponClassScores,
    );
  }

  BorderlineReviewTask _parseBorderlineReviewTask(Map<String, dynamic> value) =>
      BorderlineReviewTask(
        id: value['id'] as String? ?? '',
        status: value['status'] as String? ?? 'legacy_unknown',
        confidence: (value['detectorConfidence'] as num? ?? 0).toDouble(),
        sourceStartSeconds: (value['sourceStartSeconds'] as num? ?? 0)
            .toDouble(),
        sourceEndSeconds: (value['sourceEndSeconds'] as num? ?? 0).toDouble(),
        raiseAlert: value['raiseAlert'] as bool?,
        uncertainty: value['uncertainty'] as String?,
        briefReason: value['briefReason'] as String?,
        errorCode: value['errorCode'] as String?,
        modelVersion: value['modelVersion'] as String?,
        promptVersion: value['promptVersion'] as String?,
        createdAt: DateTime.tryParse(value['createdAt'] as String? ?? ''),
        completedAt: DateTime.tryParse(value['completedAt'] as String? ?? ''),
      );

  AlertNotificationEligibility? _parseNotificationEligibility(dynamic value) {
    if (value is! Map) return null;
    final map = Map<String, dynamic>.from(value);
    return AlertNotificationEligibility(
      status: map['status'] as String? ?? 'not_recorded',
      inFirstNotificationAudience:
          map['inFirstNotificationAudience'] as bool? ?? false,
      qualificationReasons:
          (map['qualificationReasons'] as List<dynamic>? ?? const [])
              .whereType<String>()
              .toList(growable: false),
      firstNotificationProviderAcceptedAt: DateTime.tryParse(
        (map['firstNotificationProviderAcceptedAt'] ??
                    map['firstNotificationSentAt'])
                as String? ??
            '',
      ),
      note: map['note'] as String?,
    );
  }

  BorderlineReviewAvailability? _parseBorderlineReviewAvailability(
    dynamic value,
  ) {
    if (value is! Map) return null;
    final map = Map<String, dynamic>.from(value);
    return BorderlineReviewAvailability(
      available: map['available'] as bool? ?? false,
      enabledForAccount: map['enabledForAccount'] as bool? ?? false,
      effective: map['effective'] as bool? ?? false,
      unavailableReason: map['unavailableReason'] as String?,
    );
  }

  RolePermissions _parsePermissions(dynamic value) {
    final map = value is Map ? value : const <String, dynamic>{};
    return RolePermissions(
      manageRoles: map['manageRoles'] == true,
      manageUsers: map['manageUsers'] == true,
      manageBorderlineSettings: map['manageBorderlineSettings'] == true,
    );
  }

  String _absoluteUrl(String value) =>
      value.startsWith('http') ? value : '$baseUrl$value';

  String? _latestActionNote(List<dynamic>? actions) {
    if (actions == null || actions.isEmpty) return null;
    final item = actions.first as Map<String, dynamic>;
    return item['note'] as String?;
  }

  List<AlertActionRecord> _parseActions(List<dynamic>? actions) {
    if (actions == null) return const [];
    return actions
        .whereType<Map<String, dynamic>>()
        .map((item) {
          final rawDate = item['createdAt'] ?? item['created_at'];
          DateTime? createdAt;
          if (rawDate is String) {
            createdAt = DateTime.tryParse(rawDate);
          }
          final action = (item['action'] as String? ?? '').trim();
          final performedBy =
              (item['performedBy'] ?? item['performed_by'] ?? 'Unknown user')
                  .toString()
                  .trim();
          final email = (item['performedByEmail'] ?? item['performed_by_email'])
              ?.toString()
              .trim();
          final note = (item['note'] as String?)?.trim();
          return AlertActionRecord(
            action: action,
            performedBy: performedBy.isEmpty ? 'Unknown user' : performedBy,
            performedByEmail: email?.isEmpty == true ? null : email,
            note: note?.isEmpty == true ? null : note,
            createdAt: createdAt,
            broadcastRoleIds:
                (item['broadcastRoleIds'] as List<dynamic>? ?? const [])
                    .whereType<String>()
                    .toList(growable: false),
            isLegacy: item['isLegacy'] as bool? ?? false,
          );
        })
        .toList(growable: false);
  }
}

class GuardAidApiException extends GuardAidRequestException {
  const GuardAidApiException(super.statusCode, super.message);
}

AlertType _alertType(String? value) => switch (value) {
  'weapon' => AlertType.weapon,
  'physical_contact_bullying' => AlertType.physicalContactBullying,
  _ => AlertType.violence,
};

AlertStatus _alertStatus(String? value) => switch (value) {
  'in_review' => AlertStatus.inReview,
  'resolved' => AlertStatus.resolved,
  'dismissed' => AlertStatus.dismissed,
  'escalated' => AlertStatus.escalated,
  _ => AlertStatus.open,
};

AlertWorkflowStatus _workflowStatus(
  String? value, {
  required AlertStatus legacyStatus,
}) => switch (value) {
  'awaiting_confirmation' => AlertWorkflowStatus.awaitingConfirmation,
  'confirmed_awaiting_handler' => AlertWorkflowStatus.confirmedAwaitingHandler,
  'in_progress' => AlertWorkflowStatus.inProgress,
  'resolved' => AlertWorkflowStatus.resolved,
  'dismissed' => AlertWorkflowStatus.dismissed,
  'legacy_in_review' => AlertWorkflowStatus.legacyInReview,
  'legacy_escalated' => AlertWorkflowStatus.legacyEscalated,
  _ => switch (legacyStatus) {
    AlertStatus.inReview => AlertWorkflowStatus.legacyInReview,
    AlertStatus.resolved => AlertWorkflowStatus.resolved,
    AlertStatus.dismissed => AlertWorkflowStatus.dismissed,
    AlertStatus.escalated => AlertWorkflowStatus.legacyEscalated,
    AlertStatus.open => AlertWorkflowStatus.awaitingConfirmation,
  },
};

SensitivityMode _sensitivity(String? value) => switch (value) {
  'sensitive' => SensitivityMode.sensitive,
  'certain' => SensitivityMode.certain,
  _ => SensitivityMode.moderate,
};

UserStatus _userStatus(String? value) => switch (value) {
  'active' => UserStatus.active,
  'disabled' => UserStatus.disabled,
  _ => UserStatus.pending,
};

String _roleDisplayName(String value) => value
    .split('_')
    .map(
      (part) =>
          part.isEmpty ? part : '${part[0].toUpperCase()}${part.substring(1)}',
    )
    .join(' ');

String _firebaseMessage(FirebaseAuthException error) => switch (error.code) {
  'invalid-credential' ||
  'wrong-password' ||
  'user-not-found' => 'Incorrect email or password.',
  'invalid-email' => 'Enter a valid email address.',
  'user-disabled' => 'This Firebase account has been disabled.',
  'too-many-requests' => 'Too many attempts. Wait before trying again.',
  'network-request-failed' =>
    'Unable to reach Firebase. Check the network connection.',
  _ =>
    error.message ?? 'Firebase Authentication could not complete the request.',
};
