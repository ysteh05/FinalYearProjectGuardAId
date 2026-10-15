import 'dart:convert';

enum AlertType { weapon, violence, physicalContactBullying }

enum AlertStatus { open, inReview, resolved, dismissed, escalated }

enum AlertWorkflowStatus {
  awaitingConfirmation,
  confirmedAwaitingHandler,
  inProgress,
  resolved,
  dismissed,
  legacyInReview,
  legacyEscalated,
}

extension AlertWorkflowStatusLabels on AlertWorkflowStatus {
  String get label => switch (this) {
    AlertWorkflowStatus.awaitingConfirmation => 'Awaiting confirmation',
    AlertWorkflowStatus.confirmedAwaitingHandler =>
      'Confirmed · awaiting handler',
    AlertWorkflowStatus.inProgress => 'In progress',
    AlertWorkflowStatus.resolved => 'Resolved',
    AlertWorkflowStatus.dismissed => 'Dismissed',
    AlertWorkflowStatus.legacyInReview => 'In review · legacy record',
    AlertWorkflowStatus.legacyEscalated => 'Escalated · legacy record',
  };
}

enum SensitivityMode { sensitive, moderate, certain }

enum UserStatus { pending, active, disabled }

const unavailableSceneSummary =
    'AI scene summary is unavailable. Review the video evidence.';

class AiSceneSummary {
  const AiSceneSummary({
    required this.description,
    this.visibleEvidence = const [],
    this.uncertainty = 'high',
  });

  final String description;
  final List<String> visibleEvidence;
  final String uncertainty;

  String get uncertaintyLabel => switch (uncertainty) {
    'low' => 'Low',
    'medium' => 'Medium',
    _ => 'High',
  };

  static AiSceneSummary? fromValue(dynamic raw, {String? legacy}) {
    dynamic decoded = raw;
    if (decoded is String) {
      final text = decoded.trim();
      final parsed = _decodeSceneJson(text);
      decoded =
          parsed ??
          (text.startsWith('{') || text.startsWith('[') ? null : text);
    }
    for (var depth = 0; depth < 2 && decoded is String; depth++) {
      final nested = _decodeSceneJson(decoded.trim());
      if (nested == null) break;
      decoded = nested;
    }
    String? description;
    List<String> evidence = const [];
    var uncertainty = 'high';
    if (decoded is Map) {
      dynamic mapValue = decoded;
      if (mapValue['sceneSummary'] is Map) mapValue = mapValue['sceneSummary'];
      if (mapValue['scene_summary'] is Map) {
        mapValue = mapValue['scene_summary'];
      }
      if (mapValue['result'] is Map && mapValue['description'] == null) {
        mapValue = mapValue['result'];
      }
      if (mapValue is Map) {
        final rawDescription =
            mapValue['description'] ??
            mapValue['sceneDescription'] ??
            mapValue['scene_description'] ??
            mapValue['summary'];
        if (rawDescription is String) description = rawDescription.trim();
        final rawEvidence =
            mapValue['visibleEvidence'] ??
            mapValue['visible_evidence'] ??
            mapValue['observations'] ??
            mapValue['evidence'];
        if (rawEvidence is List) {
          evidence = rawEvidence
              .map((item) => item.toString().trim())
              .where((item) => item.isNotEmpty)
              .take(2)
              .toList(growable: false);
        }
        final rawUncertainty = mapValue['uncertainty']
            ?.toString()
            .toLowerCase();
        if (rawUncertainty == 'low' || rawUncertainty == 'medium') {
          uncertainty = rawUncertainty ?? 'high';
        }
      }
    } else if (decoded is String) {
      description = decoded.trim();
    }
    if ((description == null || description.isEmpty) && legacy != null) {
      final readable = readableSceneSummary(legacy);
      if (readable.isNotEmpty &&
          readable != unavailableSceneSummary &&
          readable != 'Scene description is not available yet.') {
        description = readable;
      }
    }
    final descriptionText = description;
    if (descriptionText == null || descriptionText.isEmpty) return null;
    final bounded = _boundSummaryText(descriptionText);
    if (bounded.isEmpty) return null;
    return AiSceneSummary(
      description: bounded,
      visibleEvidence: evidence
          .map((item) => _boundSummaryText(item, maxWords: 10))
          .where((item) => item.isNotEmpty)
          .take(2)
          .toList(growable: false),
      uncertainty: uncertainty,
    );
  }
}

String _boundSummaryText(
  String value, {
  int maxWords = 12,
  int maxCharacters = 120,
}) {
  final words = value.trim().replaceAll(RegExp(r'\s+'), ' ').split(' ');
  if (words.isEmpty || (words.length == 1 && words.first.isEmpty)) return '';
  final boundedWords = words.length > maxWords
      ? words.take(maxWords).toList()
      : words;
  var result = boundedWords.join(' ');
  if (words.length > maxWords) result = '$result…';
  if (result.length > maxCharacters) {
    result = result.substring(0, maxCharacters).trimRight();
  }
  return result;
}

extension AlertTypeLabels on AlertType {
  String get label => switch (this) {
    AlertType.weapon => 'Weapon',
    AlertType.violence => 'Violence',
    AlertType.physicalContactBullying => 'Physical-contact bullying',
  };
}

extension AlertStatusLabels on AlertStatus {
  String get label => switch (this) {
    AlertStatus.open => 'Open',
    AlertStatus.inReview => 'In review',
    AlertStatus.resolved => 'Resolved',
    AlertStatus.dismissed => 'Dismissed',
    AlertStatus.escalated => 'Escalated',
  };
}

extension SensitivityLabels on SensitivityMode {
  String get label => switch (this) {
    SensitivityMode.sensitive => 'Sensitive',
    SensitivityMode.moderate => 'Moderate',
    SensitivityMode.certain => 'Certain',
  };
}

String readableSceneSummary(String? raw) {
  if (raw == null || raw.trim().isEmpty) {
    return 'Scene description is not available yet.';
  }
  if (raw.trim() == 'Scene description could not be formatted.') {
    return unavailableSceneSummary;
  }
  final candidate = raw
      .trim()
      .replaceAll('```json', '')
      .replaceAll('```', '')
      .trim();
  final extracted = _sceneTextFromValue(candidate);
  if (extracted != null && extracted.isNotEmpty) return extracted;
  final recovered = _recoverSceneTextFromMalformedJson(candidate);
  if (recovered != null && recovered.isNotEmpty) return recovered;
  final incomplete = _recoverIncompleteSceneDescription(candidate);
  if (incomplete != null && incomplete.isNotEmpty) return incomplete;
  if (candidate.startsWith('{') || candidate.startsWith('[')) {
    return unavailableSceneSummary;
  }
  return candidate;
}

const _sceneSummaryKeys = [
  'description',
  'scenedescription',
  'scenesummary',
  'summary',
  'briefreason',
  'briefdescription',
  'narrative',
  'caption',
  'whatishappening',
  'actiondescription',
  'text',
  'content',
  'output',
  'result',
];

const _sceneObservationKeys = [
  'visibleevidence',
  'visibleobservations',
  'observations',
  'evidence',
  'events',
  'actions',
];

const _sceneMetadataKeys = {
  'uncertainty',
  'confidence',
  'severity',
  'raisealert',
  'decisionlabel',
  'status',
  'incidenttype',
  'type',
  'category',
  'model',
  'version',
  'score',
  'timestamp',
  'id',
};

String? _recoverSceneTextFromMalformedJson(String raw) {
  // Preserve a complete description already emitted by the model even if its
  // later JSON fields were truncated (for example when generation hit a limit).
  final fieldPattern = RegExp(
    r'"([^"\\]+)"\s*:\s*("(?:\\.|[^"\\])*")',
    caseSensitive: false,
  );
  for (final match in fieldPattern.allMatches(raw)) {
    final key = _normaliseSceneKey(match.group(1));
    if (!_sceneSummaryKeys.contains(key) &&
        !_sceneObservationKeys.contains(key)) {
      continue;
    }
    try {
      // Qwen occasionally emits \\' for apostrophes. That is not valid JSON
      // although the resulting description is still safe readable text.
      final value = jsonDecode(match.group(2)!.replaceAll(r"\'", "'"));
      if (value is String && value.trim().isNotEmpty) return value.trim();
    } on FormatException {
      continue;
    }
  }
  return null;
}

String? _recoverIncompleteSceneDescription(String raw) {
  final match = RegExp(
    r'''["']description["']\s*:\s*["'](.*)$''',
    caseSensitive: false,
    dotAll: true,
  ).firstMatch(raw);
  if (match == null) return null;
  var fragment = match
      .group(1)!
      .replaceAll(r'\n', ' ')
      .replaceAll(r"\'", "'")
      .trim();
  final nextField = RegExp(
    r'''",\s*["'](?:visible_|uncertainty|confidence|summary)''',
    caseSensitive: false,
  ).firstMatch(fragment);
  if (nextField != null) {
    fragment = fragment.substring(0, nextField.start).trimRight();
  }
  final lastStop = [
    fragment.lastIndexOf('.'),
    fragment.lastIndexOf('!'),
    fragment.lastIndexOf('?'),
  ].reduce((left, right) => left > right ? left : right);
  if (lastStop >= 40) return fragment.substring(0, lastStop + 1).trimRight();
  if (fragment.isEmpty) return null;
  final shortened = fragment
      .substring(0, fragment.length > 800 ? 800 : fragment.length)
      .trimRight();
  final boundary = shortened.lastIndexOf(' ');
  return '${(boundary >= 40 ? shortened.substring(0, boundary) : shortened).trimRight()}…';
}

String _normaliseSceneKey(Object? key) =>
    key.toString().toLowerCase().replaceAll(RegExp(r'[^a-z0-9]'), '');

dynamic _decodeSceneJson(String value) {
  try {
    return jsonDecode(value);
  } on FormatException {
    for (final pair in const [
      ['{', '}'],
      ['[', ']'],
    ]) {
      final start = value.indexOf(pair[0]);
      final end = value.lastIndexOf(pair[1]);
      if (start < 0 || end <= start) continue;
      try {
        return jsonDecode(value.substring(start, end + 1));
      } on FormatException {
        continue;
      }
    }
  }
  return null;
}

String? _sceneTextFromValue(Object? value, [int depth = 0]) {
  if (depth > 5) return null;
  if (value is String) {
    final text = value
        .trim()
        .replaceAll('```json', '')
        .replaceAll('```', '')
        .trim();
    if (text.isEmpty) return null;
    final decoded = _decodeSceneJson(text);
    if (decoded != null) return _sceneTextFromValue(decoded, depth + 1);
    if (text.startsWith('{') || text.startsWith('[')) return null;
    return text;
  }
  if (value is List) {
    final parts = value
        .map((item) => _sceneTextFromValue(item, depth + 1))
        .whereType<String>()
        .where((text) => text.isNotEmpty)
        .toSet();
    return parts.isEmpty ? null : parts.join('. ');
  }
  if (value is Map) {
    final keyed = <String, Object?>{
      for (final entry in value.entries)
        _normaliseSceneKey(entry.key): entry.value,
    };
    for (final key in [..._sceneSummaryKeys, ..._sceneObservationKeys]) {
      if (!keyed.containsKey(key)) continue;
      final text = _sceneTextFromValue(keyed[key], depth + 1);
      if (text != null && text.isNotEmpty) return text;
    }
    final parts = value.entries
        .where(
          (entry) =>
              !_sceneMetadataKeys.contains(_normaliseSceneKey(entry.key)),
        )
        .map((entry) => _sceneTextFromValue(entry.value, depth + 1))
        .whereType<String>()
        .where((text) => text.isNotEmpty)
        .toSet();
    return parts.isEmpty ? null : parts.join('. ');
  }
  return null;
}

class EvidenceRecord {
  const EvidenceRecord({
    required this.id,
    required this.mediaType,
    this.url,
    this.startSeconds,
    this.endSeconds,
    this.httpHeaders = const {},
  });

  final String id;
  final String mediaType;
  final String? url;
  final double? startSeconds;
  final double? endSeconds;
  final Map<String, String> httpHeaders;

  bool get isVideo => mediaType.contains('video') || mediaType.contains('clip');
}

class GuardAlert {
  const GuardAlert({
    required this.id,
    required this.cameraName,
    required this.location,
    required this.type,
    required this.status,
    required this.confidence,
    required this.sensitivity,
    required this.detectedAt,
    required this.descriptionStatus,
    this.workflowStatus = AlertWorkflowStatus.awaitingConfirmation,
    this.handlerUserId,
    this.handlerName,
    this.broadcastRoleIds = const [],
    this.workflowRevision = 0,
    this.sceneDescription,
    this.sceneSummary,
    this.isBorderline = false,
    this.isProvisional = false,
    this.borderlineReviewStatus = 'not_applicable',
    this.evidenceStatus = 'legacy_unknown',
    this.detectionEvents = const [],
    this.borderlineReviewTasks = const [],
    this.notificationEligibility,
    this.borderlineReviewAvailability,
    this.evidence = const [],
    this.actionNote,
    this.actions = const [],
    this.isRead = false,
  });

  final String id;
  final String cameraName;
  final String location;
  final AlertType type;
  final AlertStatus status;
  final double confidence;
  final SensitivityMode sensitivity;
  final DateTime detectedAt;
  final String descriptionStatus;
  final AlertWorkflowStatus workflowStatus;
  final String? handlerUserId;
  final String? handlerName;
  final List<String> broadcastRoleIds;
  final int workflowRevision;
  final String? sceneDescription;
  final AiSceneSummary? sceneSummary;
  final bool isBorderline;
  final bool isProvisional;
  final String borderlineReviewStatus;
  final String evidenceStatus;
  final List<AlertDetectionEvent> detectionEvents;
  final List<BorderlineReviewTask> borderlineReviewTasks;
  final AlertNotificationEligibility? notificationEligibility;
  final BorderlineReviewAvailability? borderlineReviewAvailability;
  final List<EvidenceRecord> evidence;
  final String? actionNote;
  final List<AlertActionRecord> actions;
  final bool isRead;

  bool get isWorkflowActive =>
      workflowStatus == AlertWorkflowStatus.awaitingConfirmation ||
      workflowStatus == AlertWorkflowStatus.confirmedAwaitingHandler ||
      workflowStatus == AlertWorkflowStatus.inProgress;

  GuardAlert copyWith({
    AlertStatus? status,
    AlertWorkflowStatus? workflowStatus,
    String? handlerUserId,
    String? handlerName,
    List<String>? broadcastRoleIds,
    int? workflowRevision,
    String? actionNote,
    List<AlertActionRecord>? actions,
    bool? isRead,
    List<AlertDetectionEvent>? detectionEvents,
    List<BorderlineReviewTask>? borderlineReviewTasks,
    AlertNotificationEligibility? notificationEligibility,
    BorderlineReviewAvailability? borderlineReviewAvailability,
  }) => GuardAlert(
    id: id,
    cameraName: cameraName,
    location: location,
    type: type,
    status: status ?? this.status,
    workflowStatus: workflowStatus ?? this.workflowStatus,
    handlerUserId: handlerUserId ?? this.handlerUserId,
    handlerName: handlerName ?? this.handlerName,
    broadcastRoleIds: broadcastRoleIds ?? this.broadcastRoleIds,
    workflowRevision: workflowRevision ?? this.workflowRevision,
    confidence: confidence,
    sensitivity: sensitivity,
    detectedAt: detectedAt,
    descriptionStatus: descriptionStatus,
    sceneDescription: sceneDescription,
    sceneSummary: sceneSummary,
    isBorderline: isBorderline,
    isProvisional: isProvisional,
    borderlineReviewStatus: borderlineReviewStatus,
    evidenceStatus: evidenceStatus,
    detectionEvents: detectionEvents ?? this.detectionEvents,
    borderlineReviewTasks: borderlineReviewTasks ?? this.borderlineReviewTasks,
    notificationEligibility:
        notificationEligibility ?? this.notificationEligibility,
    borderlineReviewAvailability:
        borderlineReviewAvailability ?? this.borderlineReviewAvailability,
    evidence: evidence,
    actionNote: actionNote ?? this.actionNote,
    actions: actions ?? this.actions,
    isRead: isRead ?? this.isRead,
  );
}

class AlertDetectionEvent {
  const AlertDetectionEvent({
    required this.id,
    required this.confidence,
    required this.decisionSource,
    required this.configVersion,
    required this.modelVersion,
    this.detectedAt,
    this.sourceStartSeconds,
    this.sourceEndSeconds,
    this.qualifications = const {},
    this.weaponClassScores = const {},
  });

  final String id;
  final double confidence;
  final String decisionSource;
  final int configVersion;
  final String modelVersion;
  final DateTime? detectedAt;
  final double? sourceStartSeconds;
  final double? sourceEndSeconds;
  final Map<String, List<String>> qualifications;
  final Map<String, double> weaponClassScores;
}

class BorderlineReviewTask {
  const BorderlineReviewTask({
    required this.id,
    required this.status,
    required this.confidence,
    required this.sourceStartSeconds,
    required this.sourceEndSeconds,
    this.raiseAlert,
    this.uncertainty,
    this.briefReason,
    this.errorCode,
    this.modelVersion,
    this.promptVersion,
    this.createdAt,
    this.completedAt,
  });

  final String id;
  final String status;
  final double confidence;
  final double sourceStartSeconds;
  final double sourceEndSeconds;
  final bool? raiseAlert;
  final String? uncertainty;
  final String? briefReason;
  final String? errorCode;
  final String? modelVersion;
  final String? promptVersion;
  final DateTime? createdAt;
  final DateTime? completedAt;
}

class AlertNotificationEligibility {
  const AlertNotificationEligibility({
    required this.status,
    required this.inFirstNotificationAudience,
    this.qualificationReasons = const [],
    this.firstNotificationProviderAcceptedAt,
    this.note,
  });

  final String status;
  final bool inFirstNotificationAudience;
  final List<String> qualificationReasons;
  final DateTime? firstNotificationProviderAcceptedAt;
  final String? note;
}

class BorderlineReviewAvailability {
  const BorderlineReviewAvailability({
    required this.available,
    required this.enabledForAccount,
    required this.effective,
    this.unavailableReason,
  });

  final bool available;
  final bool enabledForAccount;
  final bool effective;
  final String? unavailableReason;
}

class AlertActionRecord {
  const AlertActionRecord({
    required this.action,
    required this.performedBy,
    this.performedByEmail,
    this.note,
    this.createdAt,
    this.broadcastRoleIds = const [],
    this.isLegacy = false,
  });

  final String action;
  final String performedBy;
  final String? performedByEmail;
  final String? note;
  final DateTime? createdAt;
  final List<String> broadcastRoleIds;
  final bool isLegacy;

  String get actionLabel => switch (action) {
    'dismiss' => 'Dismissed',
    'resolve' => 'Resolved',
    'escalate' => 'Escalated',
    'simulated_emergency_call' => 'Simulated emergency call',
    'dismiss_false_alarm' => 'Dismissed as a false alarm',
    'confirm_only' => 'Confirmed · requested a handler',
    'confirm_and_handle' => 'Confirmed · handling incident',
    'confirm_resolved' => 'Confirmed · already resolved',
    'take_ownership' => 'Took ownership',
    'acknowledge' => 'Acknowledged',
    'review' => 'Reviewed',
    _ => action.isEmpty ? 'Updated' : action,
  };
}

class BroadcastRoleOption {
  const BroadcastRoleOption({
    required this.id,
    required this.name,
    required this.sensitivity,
    required this.activeUserCount,
    required this.eligibleDeviceUserCount,
    required this.defaultSelected,
  });

  final String id;
  final String name;
  final SensitivityMode sensitivity;
  final int activeUserCount;
  final int eligibleDeviceUserCount;
  final bool defaultSelected;
}

class WorkflowSubmissionResult {
  const WorkflowSubmissionResult({
    this.broadcastEligibleUsers = 0,
    this.notificationsQueued = 0,
    this.broadcastDispatchesQueued = 0,
    this.decisionUpdateDispatchesQueued = 0,
    this.idempotent = false,
  });

  final int broadcastEligibleUsers;

  /// Number of queued outbox entries for the selected-role Broadcast only.
  final int broadcastDispatchesQueued;

  /// Number of queued routine collaboration-update outbox entries.
  final int decisionUpdateDispatchesQueued;

  /// Backward-compatible total across Broadcast and routine updates.
  final int notificationsQueued;
  final bool idempotent;
}

class AppUser {
  const AppUser({
    required this.id,
    required this.email,
    required this.displayName,
    required this.status,
    this.roleId,
    this.roleName,
    this.sensitivity,
    this.roleSensitivities = const [],
    this.isAdmin = false,
    this.permissions = const RolePermissions(),
    this.borderlineReviewAvailable = false,
    this.borderlineReviewEnabledForAccount = false,
    this.borderlineReviewUnavailableReason,
  });

  final String id;
  final String email;
  final String displayName;
  final UserStatus status;
  final String? roleId;
  final String? roleName;
  final SensitivityMode? sensitivity;
  final List<SensitivityMode> roleSensitivities;
  final bool isAdmin;
  final RolePermissions permissions;
  final bool borderlineReviewAvailable;
  final bool borderlineReviewEnabledForAccount;
  final String? borderlineReviewUnavailableReason;

  bool get borderlineReviewEffective =>
      borderlineReviewAvailable && borderlineReviewEnabledForAccount;

  AppUser copyWith({
    UserStatus? status,
    String? roleId,
    String? roleName,
    SensitivityMode? sensitivity,
    List<SensitivityMode>? roleSensitivities,
    bool? isAdmin,
    RolePermissions? permissions,
    bool? borderlineReviewAvailable,
    bool? borderlineReviewEnabledForAccount,
    String? borderlineReviewUnavailableReason,
  }) => AppUser(
    id: id,
    email: email,
    displayName: displayName,
    status: status ?? this.status,
    roleId: roleId ?? this.roleId,
    roleName: roleName ?? this.roleName,
    sensitivity: sensitivity ?? this.sensitivity,
    roleSensitivities: roleSensitivities ?? this.roleSensitivities,
    isAdmin: isAdmin ?? this.isAdmin,
    permissions: permissions ?? this.permissions,
    borderlineReviewAvailable:
        borderlineReviewAvailable ?? this.borderlineReviewAvailable,
    borderlineReviewEnabledForAccount:
        borderlineReviewEnabledForAccount ??
        this.borderlineReviewEnabledForAccount,
    borderlineReviewUnavailableReason:
        borderlineReviewUnavailableReason ??
        this.borderlineReviewUnavailableReason,
  );
}

class UserProvisioningResult {
  const UserProvisioningResult({
    required this.user,
    required this.identityLinked,
    required this.setupEmailRequested,
    this.alreadyExists = false,
    this.isDemo = false,
    this.setupEmailError,
  });

  final AppUser user;
  final bool identityLinked;
  final bool setupEmailRequested;
  final bool alreadyExists;
  final bool isDemo;
  final String? setupEmailError;
}

class RolePermissions {
  const RolePermissions({
    this.manageRoles = false,
    this.manageUsers = false,
    this.manageBorderlineSettings = false,
  });

  final bool manageRoles;
  final bool manageUsers;
  final bool manageBorderlineSettings;
}

class GuardRole {
  const GuardRole({
    required this.id,
    required this.name,
    required this.description,
    required this.sensitivity,
    required this.userCount,
    this.isAdmin = false,
    this.isSystem = true,
    this.permissions = const RolePermissions(),
  });

  final String id;
  final String name;
  final String description;
  final SensitivityMode sensitivity;
  final int userCount;
  final bool isAdmin;
  final bool isSystem;
  final RolePermissions permissions;

  GuardRole copyWith({SensitivityMode? sensitivity, int? userCount}) =>
      GuardRole(
        id: id,
        name: name,
        description: description,
        sensitivity: sensitivity ?? this.sensitivity,
        userCount: userCount ?? this.userCount,
        isAdmin: isAdmin,
        isSystem: isSystem,
        permissions: permissions,
      );
}

class AppNotification {
  const AppNotification({
    required this.id,
    required this.alertId,
    required this.title,
    required this.message,
    required this.createdAt,
    this.isRead = false,
  });

  final String id;
  final String alertId;
  final String title;
  final String message;
  final DateTime createdAt;
  final bool isRead;

  AppNotification copyWith({bool? isRead}) => AppNotification(
    id: id,
    alertId: alertId,
    title: title,
    message: message,
    createdAt: createdAt,
    isRead: isRead ?? this.isRead,
  );
}

class BorderlineSettings {
  const BorderlineSettings({
    this.enabled = false,
    this.recipientRoleIds = const {},
    this.reviewEnabled = false,
    this.revision = 1,
    this.roles = const [],
  });

  // Legacy fields remain readable for older API responses.
  final bool enabled;
  final Set<String> recipientRoleIds;
  final bool reviewEnabled;
  final int revision;
  final List<BorderlineRoleGroup> roles;
}

class BorderlineRoleGroup {
  const BorderlineRoleGroup({
    required this.id,
    required this.name,
    required this.sensitivity,
    required this.defaultEnabled,
    required this.enabledUserCount,
    required this.userCount,
    required this.selectionState,
    required this.users,
  });

  final String id;
  final String name;
  final SensitivityMode sensitivity;
  final bool defaultEnabled;
  final int enabledUserCount;
  final int userCount;
  final String selectionState;
  final List<BorderlineUserSetting> users;
}

class BorderlineUserSetting {
  const BorderlineUserSetting({
    required this.id,
    required this.displayName,
    required this.status,
    required this.enabled,
    required this.settingSource,
  });

  final String id;
  final String displayName;
  final String status;
  final bool enabled;
  final String settingSource;
}
