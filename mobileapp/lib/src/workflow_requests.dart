import 'dart:convert';
import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

class PendingWorkflowRequest {
  PendingWorkflowRequest({
    required this.alertId,
    required this.action,
    required this.expectedRevision,
    required this.key,
    this.comment,
    required List<String> roleIds,
  }) : roleIds = List.unmodifiable(roleIds.toSet().toList()..sort());

  factory PendingWorkflowRequest.create({
    required String alertId,
    required String action,
    required int expectedRevision,
    String? comment,
    required List<String> roleIds,
  }) => PendingWorkflowRequest(
    alertId: alertId,
    action: action,
    expectedRevision: expectedRevision,
    comment: _cleanComment(comment),
    roleIds: roleIds,
    key:
        'response_${DateTime.now().microsecondsSinceEpoch}_${Random.secure().nextInt(1 << 32)}',
  );

  final String alertId;
  final String action;
  final int expectedRevision;
  final String key;
  final String? comment;
  final List<String> roleIds;

  static String? _cleanComment(String? value) =>
      value == null || value.trim().isEmpty ? null : value.trim();

  bool matches(String action, String? comment, List<String> roles) =>
      this.action == action &&
      this.comment == _cleanComment(comment) &&
      jsonEncode(roleIds) == jsonEncode(roles.toSet().toList()..sort());

  Map<String, dynamic> toJson() => {
    'alertId': alertId,
    'action': action,
    'expectedRevision': expectedRevision,
    'key': key,
    'comment': comment,
    'roleIds': roleIds,
  };

  factory PendingWorkflowRequest.fromJson(Map<String, dynamic> value) =>
      PendingWorkflowRequest(
        alertId: value['alertId'] as String,
        action: value['action'] as String,
        expectedRevision: value['expectedRevision'] as int,
        key: value['key'] as String,
        comment: value['comment'] as String?,
        roleIds: (value['roleIds'] as List).cast<String>(),
      );
}

abstract interface class WorkflowRequestStore {
  Future<Map<String, PendingWorkflowRequest>> load(String userId);
  Future<void> save(String userId, PendingWorkflowRequest request);
  Future<void> remove(String userId, String alertId);
}

class MemoryWorkflowRequestStore implements WorkflowRequestStore {
  final Map<String, Map<String, PendingWorkflowRequest>> _values = {};
  @override
  Future<Map<String, PendingWorkflowRequest>> load(String userId) async =>
      Map.of(_values[userId] ?? {});
  @override
  Future<void> save(String userId, PendingWorkflowRequest request) async {
    (_values[userId] ??= {})[request.alertId] = request;
  }

  @override
  Future<void> remove(String userId, String alertId) async {
    _values[userId]?.remove(alertId);
  }
}

class PreferencesWorkflowRequestStore implements WorkflowRequestStore {
  final SharedPreferencesAsync _preferences = SharedPreferencesAsync();
  String _prefix(String userId) => 'guardaid.pending_response.v1.$userId.';
  @override
  Future<Map<String, PendingWorkflowRequest>> load(String userId) async {
    final result = <String, PendingWorkflowRequest>{};
    for (final key in await _preferences.getKeys()) {
      if (!key.startsWith(_prefix(userId))) continue;
      final text = await _preferences.getString(key);
      if (text == null) continue;
      final request = PendingWorkflowRequest.fromJson(
        jsonDecode(text) as Map<String, dynamic>,
      );
      result[request.alertId] = request;
    }
    return result;
  }

  @override
  Future<void> save(String userId, PendingWorkflowRequest request) =>
      _preferences.setString(
        '${_prefix(userId)}${request.alertId}',
        jsonEncode(request.toJson()),
      );
  @override
  Future<void> remove(String userId, String alertId) =>
      _preferences.remove('${_prefix(userId)}$alertId');
}
