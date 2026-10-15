import 'package:flutter/material.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';
import 'alert_detail_screen.dart';
import 'alerts_screen.dart';

class LogsScreen extends StatefulWidget {
  const LogsScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<LogsScreen> createState() => _LogsScreenState();
}

class _LogsScreenState extends State<LogsScreen> {
  String query = '';
  AlertStatus? status;

  @override
  Widget build(BuildContext context) {
    final logs = widget.controller.alerts.where((alert) {
      if (alert.status == AlertStatus.open ||
          alert.status == AlertStatus.inReview) {
        return false;
      }
      if (status != null && alert.status != status) return false;
      final normalized = query.toLowerCase().trim();
      return normalized.isEmpty ||
          alert.cameraName.toLowerCase().contains(normalized) ||
          alert.location.toLowerCase().contains(normalized) ||
          alert.type.label.toLowerCase().contains(normalized);
    }).toList()..sort((a, b) => b.detectedAt.compareTo(a.detectedAt));

    int count(AlertStatus value) =>
        widget.controller.alerts.where((item) => item.status == value).length;
    return ListView(
      padding: pageListPadding(context),
      children: [
        const SectionTitle(
          'Incident logs',
          subtitle: 'Search saved evidence and recorded response actions.',
        ),
        const SizedBox(height: 16),
        LayoutBuilder(
          builder: (context, constraints) {
            final columns = constraints.maxWidth < 340 ? 2 : 3;
            final itemWidth =
                (constraints.maxWidth - (columns - 1) * 8) / columns;
            return Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                SizedBox(
                  width: itemWidth,
                  child: _LogMetric(
                    label: 'Resolved',
                    value: count(AlertStatus.resolved),
                    color: GuardAidColors.success,
                  ),
                ),
                SizedBox(
                  width: itemWidth,
                  child: _LogMetric(
                    label: 'Dismissed',
                    value: count(AlertStatus.dismissed),
                    color: GuardAidColors.muted,
                  ),
                ),
                SizedBox(
                  width: itemWidth,
                  child: _LogMetric(
                    label: 'Escalated',
                    value: count(AlertStatus.escalated),
                    color: const Color(0xFF7B2CBF),
                  ),
                ),
              ],
            );
          },
        ),
        const SizedBox(height: 16),
        TextField(
          onChanged: (value) => setState(() => query = value),
          decoration: const InputDecoration(
            hintText: 'Search saved incidents',
            prefixIcon: Icon(Icons.search_rounded),
          ),
        ),
        const SizedBox(height: 10),
        DropdownButtonFormField<AlertStatus?>(
          initialValue: status,
          decoration: const InputDecoration(labelText: 'Incident status'),
          items: const [
            DropdownMenuItem(value: null, child: Text('All saved statuses')),
            DropdownMenuItem(
              value: AlertStatus.resolved,
              child: Text('Resolved'),
            ),
            DropdownMenuItem(
              value: AlertStatus.dismissed,
              child: Text('Dismissed'),
            ),
            DropdownMenuItem(
              value: AlertStatus.escalated,
              child: Text('Escalated'),
            ),
          ],
          onChanged: (value) => setState(() => status = value),
        ),
        const SizedBox(height: 16),
        if (logs.isEmpty)
          const Card(
            child: Padding(
              padding: EdgeInsets.all(28),
              child: Center(
                child: Text('No saved incidents match this filter.'),
              ),
            ),
          )
        else
          ...logs.map(
            (alert) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: AlertCard(
                alert: alert,
                actionLabel: 'View saved record',
                onOpen: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => AlertDetailScreen(
                      controller: widget.controller,
                      alertId: alert.id,
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _LogMetric extends StatelessWidget {
  const _LogMetric({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;
  final int value;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '$label incidents: $value. Summary statistic.',
      child: Card(
        child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 13),
        child: Column(
          children: [
            Text(
              '$value',
              style: TextStyle(
                fontSize: 21,
                color: color,
                fontWeight: FontWeight.w900,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              label,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 11, color: GuardAidColors.muted),
            ),
          ],
        ),
        ),
      ),
    );
  }
}
