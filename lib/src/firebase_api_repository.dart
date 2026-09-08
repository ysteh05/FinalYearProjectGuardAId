import 'dart:async';
import 'dart:convert';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:http/http.dart' as http;

import 'models.dart';
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
  bool _isAdmin = false;
  StreamSubscription<String>? _tokenRefreshSubscription;

  @override
  bool get isDemo => false;

  @override
  Stream<String> get openedAlertIds async* {
    await for (final message in FirebaseMessaging.onMessageOpenedApp) {
      final alertId = message.data['alertId'];
      if (alertId != null && alertId.isNotEmpty) yield alertId;
    }
  }

  @override
  Future<String?> takeInitialAlertId() async {
    final message = await _messaging.getInitialMessage();
    final alertId = message?.data['alertId'];
    return alertId == null || alertId.isEmpty ? null : alertId;
  }

  @override
  Future<AppUser> signIn(String email, String password) async {
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
      _isAdmin = user.isAdmin;
      if (user.status == UserStatus.active) {
        await _registerDevice();
        _tokenRefreshSubscription ??= _messaging.onTokenRefresh.listen(
          (token) => _registerDevice(token),
        );
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
  Future<void> sendPasswordReset(String email) async {
    try {
      await _auth.sendPasswordResetEmail(email: email.trim());
    } on FirebaseAuthException catch (error) {
      throw FormatException(_firebaseMessage(error));
    }
  }

  @override
  Future<void> signOut() async {
    _isAdmin = false;
    await _tokenRefreshSubscription?.cancel();
    _tokenRefreshSubscription = null;
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
  Future<List<GuardRole>> loadRoles() async {
    final payload =
        await _requestJson('GET', '/api/mobile/roles') as List<dynamic>;
    return payload
        .cast<Map<String, dynamic>>()
        .map((item) {
          return GuardRole(
            id: item['id'] as String,
            name: _roleDisplayName(item['name'] as String),
            description: item['description'] as String? ?? '',
            sensitivity: _sensitivity(item['sensitivityMode'] as String?),
            userCount: item['userCount'] as int? ?? 0,
            isAdmin: item['isAdmin'] as bool? ?? false,
          );
        })
        .toList(growable: false);
  }

  @override
  Future<List<AppUser>> loadUsers() async {
    if (!_isAdmin) return const [];
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
  Future<void> updateUserAssignment(
    String userId, {
    required String roleId,
    required UserStatus status,
  }) async {
    await _requestJson(
      'PATCH',
      '/api/mobile/admin/users/$userId',
      body: {'roleId': roleId, 'status': status.name},
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

  Future<void> _registerDevice([String? refreshedToken]) async {
    try {
      await _messaging.requestPermission(alert: true, badge: true, sound: true);
      final token = refreshedToken ?? await _messaging.getToken();
      if (token == null || token.length < 20) return;
      await _requestJson(
        'PUT',
        '/api/mobile/device',
        body: {
          'registrationValue': token,
          'platform': 'android',
          'deviceLabel': 'GuardAId Android',
        },
      );
    } catch (_) {
      // Login and evidence access remain usable if notification permission is denied.
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
    final response = switch (method) {
      'GET' => await _client.get(uri, headers: headers),
      'POST' => await _client.post(
        uri,
        headers: headers,
        body: body == null ? null : jsonEncode(body),
      ),
      'PUT' => await _client.put(uri, headers: headers, body: jsonEncode(body)),
      'PATCH' => await _client.patch(
        uri,
        headers: headers,
        body: jsonEncode(body),
      ),
      _ => throw ArgumentError('Unsupported HTTP method $method'),
    };
    if (response.statusCode == 401 && !forceTokenRefresh) {
      return _requestJson(method, path, body: body, forceTokenRefresh: true);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      String message = 'GuardAId server returned ${response.statusCode}.';
      try {
        final value = jsonDecode(response.body) as Map<String, dynamic>;
        message = value['detail']?.toString() ?? message;
      } catch (_) {}
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
          value['displayName'] as String? ??
          value['email'] as String? ??
          'GuardAId user',
      status: _userStatus(value['status'] as String?),
      roleId: firstRole?['id'] as String?,
      roleName: firstRole == null
          ? null
          : _roleDisplayName(firstRole['name'] as String),
      sensitivity: value['sensitivityMode'] == null
          ? null
          : _sensitivity(value['sensitivityMode'] as String?),
      isAdmin: value['isAdmin'] as bool? ?? false,
    );
  }

  AppUser _parseUser(Map<String, dynamic> value) => AppUser(
    id: value['id'] as String,
    email: value['email'] as String? ?? '',
    displayName:
        value['displayName'] as String? ??
        value['email'] as String? ??
        'Pending user',
    status: _userStatus(value['status'] as String?),
    roleId: value['roleId'] as String?,
    roleName: value['roleName'] == null
        ? null
        : _roleDisplayName(value['roleName'] as String),
    sensitivity: value['sensitivityMode'] == null
        ? null
        : _sensitivity(value['sensitivityMode'] as String?),
    isAdmin: value['isAdmin'] as bool? ?? false,
  );

  GuardAlert _parseAlert(Map<String, dynamic> value, String token) {
    return GuardAlert(
      id: value['id'] as String,
      cameraName: value['cameraName'] as String? ?? 'Unknown camera',
      location: value['location'] as String? ?? 'Unspecified',
      type: _alertType(value['type'] as String?),
      status: _alertStatus(value['status'] as String?),
      confidence:
          (value['maxConfidence'] as num? ?? value['confidence'] as num? ?? 0)
              .toDouble(),
      sensitivity: _sensitivity(value['sensitivityMode'] as String?),
      detectedAt: DateTime.parse(value['firstDetectedAt'] as String),
      descriptionStatus: value['descriptionStatus'] as String? ?? 'pending',
      sceneDescription: value['sceneDescription'] as String?,
      isBorderline: value['isBorderlinePromoted'] as bool? ?? false,
      isProvisional: value['isProvisional'] as bool? ?? false,
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
      actionNote: _latestActionNote(value['actions'] as List<dynamic>?),
    );
  }

  String _absoluteUrl(String value) =>
      value.startsWith('http') ? value : '$baseUrl$value';

  String? _latestActionNote(List<dynamic>? actions) {
    if (actions == null || actions.isEmpty) return null;
    final item = actions.first as Map<String, dynamic>;
    return item['note'] as String?;
  }
}

class GuardAidApiException implements Exception {
  const GuardAidApiException(this.statusCode, this.message);

  final int statusCode;
  final String message;

  @override
  String toString() => message;
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
