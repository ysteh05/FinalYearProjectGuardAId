import 'package:flutter/material.dart';

import 'models.dart';
import 'theme.dart';

class GuardAidBrand extends StatelessWidget {
  const GuardAidBrand({super.key, this.compact = false});

  final bool compact;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: compact ? 36 : 48,
          height: compact ? 36 : 48,
          decoration: BoxDecoration(
            color: GuardAidColors.navy,
            borderRadius: BorderRadius.circular(compact ? 11 : 15),
          ),
          child: Icon(
            Icons.shield_rounded,
            color: Colors.white,
            size: compact ? 22 : 30,
          ),
        ),
        const SizedBox(width: 11),
        Flexible(
          child: Text(
            'GuardAId',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: compact ? 20 : 28,
              color: GuardAidColors.navy,
              fontWeight: FontWeight.w900,
              letterSpacing: -0.7,
            ),
          ),
        ),
      ],
    );
  }
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.title, {super.key, this.subtitle, this.trailing});

  final String title;
  final String? subtitle;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final titleBlock = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w900),
        ),
        if (subtitle != null) ...[
          const SizedBox(height: 4),
          Text(subtitle!, style: const TextStyle(color: GuardAidColors.muted)),
        ],
      ],
    );
    final action = trailing;
    if (action == null) return titleBlock;
    return LayoutBuilder(
      builder: (context, constraints) {
        final scaledBodySize = MediaQuery.textScalerOf(context).scale(14);
        final stackTrailing = constraints.maxWidth < 360 || scaledBodySize > 17;
        if (stackTrailing) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              titleBlock,
              const SizedBox(height: 6),
              Align(alignment: Alignment.centerRight, child: action),
            ],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: titleBlock),
            const SizedBox(width: 8),
            action,
          ],
        );
      },
    );
  }
}

class StatusBadge extends StatelessWidget {
  const StatusBadge({
    super.key,
    required this.label,
    required this.color,
    this.icon,
  });

  final String label;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: BoxConstraints(
        maxWidth: (MediaQuery.sizeOf(context).width - 64)
            .clamp(96, 480)
            .toDouble(),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.25)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 5),
          ],
          Flexible(
            child: Text(
              label,
              style: TextStyle(
                color: color,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Keep a full workflow label on one line when it fits; otherwise move the
/// entire badge below the title instead of squeezing it into half a row.
class AlertDetailHeading extends StatelessWidget {
  const AlertDetailHeading({super.key, required this.title, required this.status,
    required this.color});
  final String title;
  final String status;
  final Color color;

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, size) {
    const titleStyle = TextStyle(fontSize: 20, height: 1.15, fontWeight: FontWeight.w700);
    const badgeStyle = TextStyle(fontSize: 12, fontWeight: FontWeight.w600);
    double width(String text, TextStyle style) {
      final painter = TextPainter(
        text: TextSpan(text: text, style: DefaultTextStyle.of(context).style.merge(style)),
        textDirection: Directionality.of(context), textScaler: MediaQuery.textScalerOf(context),
      )..layout();
      final result = painter.width;
      painter.dispose();
      return result;
    }
    final inline = width(title, titleStyle) + 12 + width(status, badgeStyle) + 22 <= size.maxWidth;
    final titleWidget = Text(title, key: const ValueKey('alert-detail-title'), style: titleStyle);
    final badge = StatusBadge(label: status, color: color);
    if (!inline) {
      return Column(key: const ValueKey('alert-heading-stacked'),
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [titleWidget, const SizedBox(height: 8), badge]);
    }
    return Row(key: const ValueKey('alert-heading-inline'), children: [
      titleWidget, const Spacer(), badge,
    ]);
  });
}

class AiSceneSummaryView extends StatelessWidget {
  const AiSceneSummaryView({
    super.key,
    required this.summary,
    this.legacy,
    this.pending = false,
    this.compact = false,
  });

  final AiSceneSummary? summary;
  final String? legacy;
  final bool pending;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    if (pending) {
      return Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SizedBox.square(
            dimension: 18,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          const SizedBox(width: 10),
          const Expanded(
            child: Text(
              'Scene summary is being prepared. The alert remains available now.',
              style: TextStyle(height: 1.4),
            ),
          ),
        ],
      );
    }
    final value = summary ?? AiSceneSummary.fromValue(null, legacy: legacy);
    if (value == null) {
      return const Text(
        unavailableSceneSummary,
        style: TextStyle(color: GuardAidColors.muted, height: 1.4),
      );
    }
    if (compact) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text.rich(
            TextSpan(
              children: [
                const TextSpan(
                  text: 'Overall: ',
                  style: TextStyle(fontWeight: FontWeight.w800),
                ),
                TextSpan(text: value.description),
              ],
            ),
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(color: GuardAidColors.muted, height: 1.4),
          ),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Overall',
          style: TextStyle(
            color: GuardAidColors.muted,
            fontSize: 12,
            fontWeight: FontWeight.w800,
          ),
        ),
        const SizedBox(height: 4),
        Text(value.description, style: const TextStyle(height: 1.45)),
        if (value.visibleEvidence.isNotEmpty) ...[
          const SizedBox(height: 14),
          const Text(
            'Details',
            style: TextStyle(
              color: GuardAidColors.muted,
              fontSize: 12,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 5),
          for (final item in value.visibleEvidence)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '•  ',
                    style: TextStyle(color: GuardAidColors.info),
                  ),
                  Expanded(
                    child: Text(item, style: const TextStyle(height: 1.4)),
                  ),
                ],
              ),
            ),
        ],
        const SizedBox(height: 12),
        const Text(
          'AI-generated summary. Verify against the video evidence.',
          style: TextStyle(
            color: GuardAidColors.muted,
            fontSize: 12,
            height: 1.35,
          ),
        ),
      ],
    );
  }
}

Color alertTypeColor(AlertType type) => switch (type) {
  AlertType.weapon => GuardAidColors.weapon,
  AlertType.violence ||
  AlertType.physicalContactBullying => GuardAidColors.violence,
};

Color alertStatusColor(AlertStatus status) => switch (status) {
  AlertStatus.open => GuardAidColors.weapon,
  AlertStatus.inReview => GuardAidColors.violence,
  AlertStatus.resolved => GuardAidColors.success,
  AlertStatus.dismissed => GuardAidColors.muted,
  AlertStatus.escalated => const Color(0xFF7B2CBF),
};

String formatDateTime(DateTime value) {
  final local = value.toLocal();
  String two(int number) => number.toString().padLeft(2, '0');
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  return '${local.day} ${months[local.month - 1]} ${local.year}, '
      '${two(local.hour)}:${two(local.minute)}';
}

EdgeInsets pageListPadding(BuildContext context, {double top = 12}) {
  final bottomInset = MediaQuery.viewPaddingOf(context).bottom;
  return EdgeInsets.fromLTRB(16, top, 16, 96 + bottomInset);
}

class MetricCard extends StatelessWidget {
  const MetricCard({
    super.key,
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
    this.compact = false,
  });

  final String label;
  final String value;
  final IconData icon;
  final Color color;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final content = Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Container(
          width: compact ? 36 : 42,
          height: compact ? 36 : 42,
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.1),
            borderRadius: BorderRadius.circular(13),
          ),
          child: Icon(icon, color: color, size: compact ? 20 : 24),
        ),
        SizedBox(width: compact ? 9 : 12),
        Expanded(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                value,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: compact ? 21 : 23,
                  fontWeight: FontWeight.w900,
                ),
              ),
              if (compact) const SizedBox(height: 2),
              Text(
                label,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 12,
                  color: GuardAidColors.muted,
                  height: compact ? 1.15 : null,
                ),
              ),
            ],
          ),
        ),
      ],
    );
    return Card(
      child: Padding(
        padding: EdgeInsets.all(compact ? 12 : 16),
        child: compact ? Center(child: content) : content,
      ),
    );
  }
}

Future<bool> showConfirmDialog(
  BuildContext context, {
  required String title,
  required String message,
  String confirmLabel = 'Confirm',
  bool destructive = false,
}) async {
  return await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: Text(message),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              style: destructive
                  ? FilledButton.styleFrom(
                      backgroundColor: GuardAidColors.weapon,
                    )
                  : null,
              onPressed: () => Navigator.pop(context, true),
              child: Text(confirmLabel),
            ),
          ],
        ),
      ) ??
      false;
}

void showMessage(BuildContext context, String message, {bool error = false}) {
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(
      content: Text(message),
      backgroundColor: error ? GuardAidColors.weapon : GuardAidColors.navy,
      behavior: SnackBarBehavior.floating,
    ),
  );
}
