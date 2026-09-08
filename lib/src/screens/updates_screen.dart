import 'package:flutter/material.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';
import 'alert_detail_screen.dart';

class UpdatesScreen extends StatelessWidget {
  const UpdatesScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  Widget build(BuildContext context) {
    final items = [...controller.notifications]
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return ListView(
      padding: pageListPadding(context),
      children: [
        SectionTitle(
          'Updates',
          subtitle:
              '${controller.unreadCount} unread notification${controller.unreadCount == 1 ? '' : 's'}',
          trailing: TextButton.icon(
            onPressed: controller.unreadCount == 0
                ? null
                : () async {
                    final confirmed = await showConfirmDialog(
                      context,
                      title: 'Mark all as read?',
                      message:
                          'All current GuardAId updates will be marked as read.',
                      confirmLabel: 'Mark all',
                    );
                    if (!confirmed || !context.mounted) return;
                    final success = await controller.markAllNotificationsRead();
                    if (context.mounted) {
                      showMessage(
                        context,
                        success
                            ? 'All updates marked as read.'
                            : controller.error ??
                                  'Unable to update notifications.',
                        error: !success,
                      );
                    }
                  },
            icon: const Icon(Icons.done_all_rounded, size: 18),
            label: const Text('Mark all as read'),
          ),
        ),
        const SizedBox(height: 16),
        if (items.isEmpty)
          const Card(
            child: Padding(
              padding: EdgeInsets.all(28),
              child: Column(
                children: [
                  Icon(
                    Icons.notifications_off_outlined,
                    size: 46,
                    color: GuardAidColors.muted,
                  ),
                  SizedBox(height: 10),
                  Text(
                    'No updates yet',
                    style: TextStyle(fontWeight: FontWeight.w800),
                  ),
                ],
              ),
            ),
          )
        else
          ...items.map(
            (item) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _NotificationCard(
                notification: item,
                onOpen: () {
                  final matching = controller.alerts.where(
                    (alert) => alert.id == item.alertId,
                  );
                  if (matching.isEmpty) {
                    showMessage(
                      context,
                      'The related alert is no longer available.',
                      error: true,
                    );
                    return;
                  }
                  Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => AlertDetailScreen(
                        controller: controller,
                        alertId: item.alertId,
                      ),
                    ),
                  );
                },
              ),
            ),
          ),
      ],
    );
  }
}

class _NotificationCard extends StatelessWidget {
  const _NotificationCard({required this.notification, required this.onOpen});

  final AppNotification notification;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final incidentIcon = notification.title.toLowerCase().contains('weapon')
        ? Icons.gpp_bad_outlined
        : notification.title.toLowerCase().contains('bully') ||
              notification.title.toLowerCase().contains('violence')
        ? Icons.sports_mma_rounded
        : notification.title.toLowerCase().contains('resolved')
        ? Icons.check_circle_outline_rounded
        : Icons.notifications_active_outlined;
    final iconColor = notification.title.toLowerCase().contains('weapon')
        ? GuardAidColors.weapon
        : notification.title.toLowerCase().contains('bully') ||
              notification.title.toLowerCase().contains('violence')
        ? GuardAidColors.violence
        : GuardAidColors.navy;
    return Semantics(
      button: true,
      label: notification.title,
      hint: 'Double tap to open this update',
      child: Card(
        color: notification.isRead ? Colors.white : const Color(0xFFF6FBFE),
        child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onOpen,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Stack(
                clipBehavior: Clip.none,
                children: [
                  Container(
                    width: 42,
                    height: 42,
                    decoration: BoxDecoration(
                      color: GuardAidColors.navy.withValues(alpha: 0.09),
                      borderRadius: BorderRadius.circular(13),
                    ),
                    child: Icon(incidentIcon, color: iconColor),
                  ),
                  if (!notification.isRead)
                    const Positioned(
                      right: -2,
                      top: -2,
                      child: CircleAvatar(
                        radius: 5,
                        backgroundColor: GuardAidColors.weapon,
                      ),
                    ),
                ],
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      notification.title,
                      style: const TextStyle(fontWeight: FontWeight.w900),
                    ),
                    const SizedBox(height: 5),
                    Text(
                      notification.message,
                      style: const TextStyle(
                        color: GuardAidColors.muted,
                        height: 1.35,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      formatDateTime(notification.createdAt),
                      style: const TextStyle(
                        fontSize: 12,
                        color: GuardAidColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              const Icon(
                Icons.chevron_right_rounded,
                color: GuardAidColors.muted,
              ),
            ],
          ),
        ),
        ),
      ),
    );
  }
}
