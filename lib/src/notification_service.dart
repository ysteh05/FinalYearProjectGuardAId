import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// Creates the two notification channels used by GuardAId.
///
/// The urgent channel is intentionally separate because Android persists a
/// channel's sound/importance after first creation. Future sound changes must
/// use a new channel id (for example, guardaid_urgent_alerts_v2).
class GuardAidNotificationService {
  GuardAidNotificationService._();

  static final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();
  static bool _initialised = false;

  static const urgentChannel = AndroidNotificationChannel(
    'guardaid_urgent_alerts_v1',
    'Urgent GuardAId alerts',
    description: 'Weapon, violence and physical-contact bullying alerts.',
    importance: Importance.max,
    playSound: true,
    sound: RawResourceAndroidNotificationSound('guardaid_urgent_alerts_v1'),
    enableVibration: true,
  );
  static const updatesChannel = AndroidNotificationChannel(
    'guardaid_updates_v1',
    'GuardAId updates',
    description: 'Routine GuardAId status updates.',
    importance: Importance.defaultImportance,
    playSound: true,
  );

  static Future<void> initialize() async {
    if (_initialised) return;
    const settings = InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      iOS: DarwinInitializationSettings(),
    );
    await _plugin.initialize(settings);
    final android = _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    await android?.createNotificationChannel(urgentChannel);
    await android?.createNotificationChannel(updatesChannel);
    await android?.requestNotificationsPermission();
    FirebaseMessaging.onMessage.listen(_showForegroundMessage);
    _initialised = true;
  }

  static Future<void> _showForegroundMessage(RemoteMessage message) async {
    final type = message.data['type']?.toString();
    final urgent = type == 'weapon' ||
        type == 'violence' ||
        type == 'physical_contact_bullying';
    final channel = urgent ? urgentChannel : updatesChannel;
    final details = AndroidNotificationDetails(
      channel.id,
      channel.name,
      channelDescription: channel.description,
      importance: urgent ? Importance.max : Importance.defaultImportance,
      priority: urgent ? Priority.max : Priority.defaultPriority,
      playSound: true,
      enableVibration: urgent,
      sound: urgent
          ? const RawResourceAndroidNotificationSound('guardaid_urgent_alerts_v1')
          : null,
      category: urgent ? AndroidNotificationCategory.alarm : null,
    );
    await _plugin.show(
      message.hashCode,
      message.notification?.title ?? 'GuardAId update',
      message.notification?.body ?? 'Open GuardAId to review this update.',
      NotificationDetails(android: details),
      payload: message.data['alertId']?.toString(),
    );
  }
}
