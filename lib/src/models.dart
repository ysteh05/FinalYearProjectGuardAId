enum AlertType { weapon, violence, physicalContactBullying }

enum AlertStatus { open, inReview, resolved, dismissed, escalated }

enum SensitivityMode { sensitive, moderate, certain }

enum UserStatus { pending, active, disabled }

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
    this.sceneDescription,
    this.isBorderline = false,
    this.isProvisional = false,
    this.evidence = const [],
    this.actionNote,
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
  final String? sceneDescription;
  final bool isBorderline;
  final bool isProvisional;
  final List<EvidenceRecord> evidence;
  final String? actionNote;

  GuardAlert copyWith({AlertStatus? status, String? actionNote}) => GuardAlert(
    id: id,
    cameraName: cameraName,
    location: location,
    type: type,
    status: status ?? this.status,
    confidence: confidence,
    sensitivity: sensitivity,
    detectedAt: detectedAt,
    descriptionStatus: descriptionStatus,
    sceneDescription: sceneDescription,
    isBorderline: isBorderline,
    isProvisional: isProvisional,
    evidence: evidence,
    actionNote: actionNote ?? this.actionNote,
  );
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
    this.isAdmin = false,
  });

  final String id;
  final String email;
  final String displayName;
  final UserStatus status;
  final String? roleId;
  final String? roleName;
  final SensitivityMode? sensitivity;
  final bool isAdmin;

  AppUser copyWith({
    UserStatus? status,
    String? roleId,
    String? roleName,
    SensitivityMode? sensitivity,
    bool? isAdmin,
  }) => AppUser(
    id: id,
    email: email,
    displayName: displayName,
    status: status ?? this.status,
    roleId: roleId ?? this.roleId,
    roleName: roleName ?? this.roleName,
    sensitivity: sensitivity ?? this.sensitivity,
    isAdmin: isAdmin ?? this.isAdmin,
  );
}

class GuardRole {
  const GuardRole({
    required this.id,
    required this.name,
    required this.description,
    required this.sensitivity,
    required this.userCount,
    this.isAdmin = false,
  });

  final String id;
  final String name;
  final String description;
  final SensitivityMode sensitivity;
  final int userCount;
  final bool isAdmin;

  GuardRole copyWith({SensitivityMode? sensitivity, int? userCount}) =>
      GuardRole(
        id: id,
        name: name,
        description: description,
        sensitivity: sensitivity ?? this.sensitivity,
        userCount: userCount ?? this.userCount,
        isAdmin: isAdmin,
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
    required this.enabled,
    required this.recipientRoleIds,
  });

  final bool enabled;
  final Set<String> recipientRoleIds;
}
