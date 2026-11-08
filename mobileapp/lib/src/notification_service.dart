import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Creates the notification channels used by GuardAId.
///
/// The urgent channel is intentionally separate because Android persists a
/// channel's sound/importance after first creation. Future sound changes must
/// use a new channel id.
class GuardAidNotificationService {
  GuardAidNotificationService._();

  static FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  /// Replaces the platform plugin during tests. Production keeps the default.
  @visibleForTesting
  static set pluginForTesting(FlutterLocalNotificationsPlugin value) {
    _plugin = value;
  }

  /// Runs the same foreground handler used by Firebase messaging.
  @visibleForTesting
  static Future<void> handleForegroundMessageForTesting(
    RemoteMessage message,
  ) => _showForegroundMessage(message);
  static final StreamController<String> _openedAlertIds =
      StreamController<String>.broadcast();
  static final Set<String> _shownEventIds = <String>{};
  static DateTime? _notificationListeningStartedAt;
  static DateTime? _signInStartedAt;
  static bool _initialised = false;
  static bool _channelsInitialised = false;

  static Stream<String> get openedAlertIds => _openedAlertIds.stream;

  static const urgentSoundResource = 'guardaid_urgent_beep_v7';
  static const reminderSoundResource = 'guardaid_urgent_repeat_3beep_v1';
  static const escalationSoundResource = 'guardaid_urgent_escalated_4beep_v1';
  static const urgentChannel = AndroidNotificationChannel(
    'guardaid_urgent_alerts_v8',
    'Urgent GuardAId alerts',
    description: 'Loud two-beep alerts for urgent safety incidents.',
    importance: Importance.max,
    playSound: true,
    sound: RawResourceAndroidNotificationSound(urgentSoundResource),
    audioAttributesUsage: AudioAttributesUsage.notification,
    enableVibration: true,
  );
  static const updatesChannel = AndroidNotificationChannel(
    'guardaid_updates_v1',
    'GuardAId updates',
    description: 'Routine GuardAId status updates.',
    importance: Importance.defaultImportance,
    playSound: true,
  );
  static const reminderChannel = AndroidNotificationChannel(
    'guardaid_urgent_repeat_v2',
    'GuardAId alert reminders',
    description: 'Three-beep repeat alerts for incidents without a response.',
    importance: Importance.max,
    playSound: true,
    sound: RawResourceAndroidNotificationSound(reminderSoundResource),
    audioAttributesUsage: AudioAttributesUsage.notification,
    enableVibration: true,
  );
  static const escalationChannel = AndroidNotificationChannel(
    'guardaid_urgent_escalated_v2',
    'GuardAId all-user escalations',
    description: 'Four-beep alerts sent to all active users after no response.',
    importance: Importance.max,
    playSound: true,
    sound: RawResourceAndroidNotificationSound(escalationSoundResource),
    audioAttributesUsage: AudioAttributesUsage.notification,
    enableVibration: true,
  );
  static const historicalAlertsChannel = AndroidNotificationChannel(
    'guardaid_historical_alerts_silent_v1',
    'Earlier GuardAId alerts',
    description: 'Earlier alerts shown without replaying an urgent sound.',
    importance: Importance.defaultImportance,
    playSound: false,
    enableVibration: false,
  );

  static void markSignInStarted([DateTime? startedAt]) {
    _signInStartedAt = startedAt ?? DateTime.now();
  }

  static bool shouldSilenceInitialAlert(
    Map<String, dynamic> data, {
    DateTime? signInStartedAt,
    DateTime? notificationSentAt,
  }) {
    if (data['source']?.toString() != 'alert_created') return false;
    final eventTime =
        DateTime.tryParse(data['alertDetectedAt']?.toString() ?? '') ??
        notificationSentAt;
    final signInTime =
        signInStartedAt ?? _signInStartedAt ?? _notificationListeningStartedAt;
    return eventTime != null &&
        signInTime != null &&
        eventTime.isBefore(signInTime);
  }

  /// Registers the app's Android channels without requesting notification
  /// permission. This is also useful for checking native channel persistence
  /// independently from the user-consent prompt.
  static Future<void> initializePlatformChannels() async {
    if (_channelsInitialised) return;
    const settings = InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      iOS: DarwinInitializationSettings(),
    );
    await _plugin.initialize(
      settings,
      onDidReceiveNotificationResponse: _handleNotificationResponse,
    );
    final android = _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    await android?.createNotificationChannel(urgentChannel);
    await android?.createNotificationChannel(reminderChannel);
    await android?.createNotificationChannel(escalationChannel);
    await android?.createNotificationChannel(updatesChannel);
    await android?.createNotificationChannel(historicalAlertsChannel);
    _channelsInitialised = true;
  }

  static Future<void> initialize() async {
    if (_initialised) return;
    await initializePlatformChannels();
    final android = _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    await android?.requestNotificationsPermission();
    _notificationListeningStartedAt = DateTime.now();
    FirebaseMessaging.onMessage.listen(_showForegroundMessage);
    _initialised = true;
  }

  static void _handleNotificationResponse(NotificationResponse response) {
    final alertId = response.payload?.trim();
    if (alertId != null && alertId.isNotEmpty) {
      _openedAlertIds.add(alertId);
    }
  }

  static Future<String?> takeInitialAlertId() async {
    final details = await _plugin.getNotificationAppLaunchDetails();
    if (details?.didNotificationLaunchApp != true) return null;
    final alertId = details?.notificationResponse?.payload?.trim();
    return alertId == null || alertId.isEmpty ? null : alertId;
  }

  static AndroidNotificationChannel channelForMessageData(
    Map<String, dynamic> data,
  ) {
    // Decision updates must remain routine even if a stale or malformed payload
    // also contains an urgent channel ID or incident type.
    if (data['source']?.toString() == 'alert_decision') {
      return updatesChannel;
    }

    final stage = data['notificationStage']?.toString();
    if (stage == 'first_repeat') return reminderChannel;
    if (stage == 'all_users_escalation') return escalationChannel;

    final requestedChannel = data['channelId']?.toString();
    if (requestedChannel == reminderChannel.id ||
        requestedChannel == 'guardaid_urgent_repeat_v1') {
      return reminderChannel;
    }
    if (requestedChannel == escalationChannel.id ||
        requestedChannel == 'guardaid_urgent_escalated_v1') {
      return escalationChannel;
    }
    if (requestedChannel == urgentChannel.id ||
        requestedChannel == 'guardaid_urgent_alerts_v7') {
      return urgentChannel;
    }
    if (requestedChannel == updatesChannel.id) return updatesChannel;

    final type = data['type']?.toString();
    final urgent =
        type == 'weapon' ||
        type == 'violence' ||
        type == 'physical_contact_bullying';
    return urgent ? urgentChannel : updatesChannel;
  }

  static Future<void> _showForegroundMessage(RemoteMessage message) async {
    final eventId = message.data['notificationEventId']?.toString().trim();
    if (eventId != null && eventId.isNotEmpty) {
      if (!_shownEventIds.add(eventId)) return;
      if (_shownEventIds.length > 512) {
        _shownEventIds.remove(_shownEventIds.first);
      }
    }
    final earlierAlert = shouldSilenceInitialAlert(
      message.data,
      notificationSentAt: message.sentTime,
    );
    final channel = earlierAlert
        ? historicalAlertsChannel
        : channelForMessageData(message.data);
    final sound = channel.id == reminderChannel.id
        ? reminderSoundResource
        : channel.id == escalationChannel.id
        ? escalationSoundResource
        : urgentSoundResource;
    final isUrgent = !earlierAlert && channel.id != updatesChannel.id;
    final details = AndroidNotificationDetails(
      channel.id,
      channel.name,
      channelDescription: channel.description,
      importance: isUrgent ? Importance.max : Importance.defaultImportance,
      priority: isUrgent ? Priority.max : Priority.defaultPriority,
      playSound: !earlierAlert,
      enableVibration: isUrgent,
      sound: isUrgent ? RawResourceAndroidNotificationSound(sound) : null,
      category: isUrgent ? AndroidNotificationCategory.event : null,
      audioAttributesUsage: isUrgent
          ? AudioAttributesUsage.notification
          : AudioAttributesUsage.notification,
    );
    await _plugin.show(
      _notificationId(
        eventId ?? message.messageId ?? message.hashCode.toString(),
      ),
      message.notification?.title ?? 'GuardAId update',
      message.notification?.body ?? 'Open GuardAId to review this update.',
      NotificationDetails(android: details),
      payload: message.data['alertId']?.toString(),
    );
  }

  static int _notificationId(String eventId) {
    var hash = 0x811c9dc5;
    for (final unit in eventId.codeUnits) {
      hash = ((hash ^ unit) * 0x01000193) & 0x7fffffff;
    }
    return hash;
  }
}
