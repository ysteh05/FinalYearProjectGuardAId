import 'package:flutter/material.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';
import 'alert_detail_screen.dart';

enum AlertFilter { all, weapon, violenceBullying, open }

class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> {
  AlertFilter filter = AlertFilter.all;
  String query = '';

  List<GuardAlert> get filteredAlerts {
    final normalized = query.toLowerCase().trim();
    return widget.controller.alerts.where((alert) {
      final filterMatches = switch (filter) {
        AlertFilter.all => true,
        AlertFilter.weapon => alert.type == AlertType.weapon,
        AlertFilter.violenceBullying => alert.type != AlertType.weapon,
        AlertFilter.open =>
          alert.status == AlertStatus.open ||
              alert.status == AlertStatus.inReview,
      };
      final searchMatches =
          normalized.isEmpty ||
          alert.cameraName.toLowerCase().contains(normalized) ||
          alert.location.toLowerCase().contains(normalized) ||
          alert.type.label.toLowerCase().contains(normalized);
      return filterMatches && searchMatches;
    }).toList()..sort((a, b) => b.detectedAt.compareTo(a.detectedAt));
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: widget.controller.refresh,
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverPadding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
            sliver: SliverList.list(
              children: [
                SectionTitle(
                  'Alert Centre',
                  subtitle:
                      'Review AI-assisted safety alerts and available evidence.',
                  trailing: IconButton(
                    tooltip: 'Refresh alerts',
                    onPressed: widget.controller.refresh,
                    icon: const Icon(Icons.refresh_rounded),
                  ),
                ),
                const SizedBox(height: 18),
                LayoutBuilder(
                  builder: (context, constraints) {
                    final active = MetricCard(
                      label: 'Active alerts',
                      value: '${widget.controller.openAlertCount}',
                      icon: Icons.warning_amber_rounded,
                      color: GuardAidColors.weapon,
                    );
                    final cameras = MetricCard(
                      label: 'Monitored cameras',
                      value: '${widget.controller.monitoredCameraCount}',
                      icon: Icons.videocam_outlined,
                      color: GuardAidColors.info,
                    );
                    if (constraints.maxWidth < 340) {
                      return Column(
                        children: [active, const SizedBox(height: 10), cameras],
                      );
                    }
                    return Row(
                      children: [
                        Expanded(child: active),
                        const SizedBox(width: 12),
                        Expanded(child: cameras),
                      ],
                    );
                  },
                ),
                const SizedBox(height: 18),
                TextField(
                  onChanged: (value) => setState(() => query = value),
                  decoration: const InputDecoration(
                    hintText: 'Search alerts, cameras or locations',
                    prefixIcon: Icon(Icons.search_rounded),
                  ),
                ),
                const SizedBox(height: 12),
                SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: AlertFilter.values.map((item) {
                      final label = switch (item) {
                        AlertFilter.all => 'All',
                        AlertFilter.weapon => 'Weapon',
                        AlertFilter.violenceBullying => 'Violence / Bullying',
                        AlertFilter.open => 'Open',
                      };
                      return Padding(
                        padding: const EdgeInsets.only(right: 8),
                        child: FilterChip(
                          label: Text(label),
                          selected: filter == item,
                          onSelected: (_) => setState(() => filter = item),
                        ),
                      );
                    }).toList(),
                  ),
                ),
                const SizedBox(height: 16),
                if (filteredAlerts.isEmpty)
                  const _EmptyAlerts()
                else
                  ...filteredAlerts.map(
                    (alert) => Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: AlertCard(
                        alert: alert,
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
            ),
          ),
        ],
      ),
    );
  }
}

class AlertCard extends StatelessWidget {
  const AlertCard({super.key, required this.alert, required this.onOpen});

  final GuardAlert alert;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    final color = alertTypeColor(alert.type);
    return Card(
      child: InkWell(
        onTap: onOpen,
        borderRadius: BorderRadius.circular(18),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      color: color.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(13),
                    ),
                    child: Icon(
                      alert.type == AlertType.weapon
                          ? Icons.gpp_bad_outlined
                          : Icons.sports_mma_rounded,
                      color: color,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          alert.type.label,
                          style: const TextStyle(
                            fontSize: 17,
                            fontWeight: FontWeight.w900,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          alert.cameraName,
                          style: const TextStyle(color: GuardAidColors.muted),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Align(
                alignment: Alignment.centerRight,
                child: StatusBadge(
                  label: alert.status.label,
                  color: alertStatusColor(alert.status),
                ),
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  StatusBadge(
                    label:
                        '${(alert.confidence * 100).toStringAsFixed(1)}% confidence',
                    color: color,
                    icon: Icons.analytics_outlined,
                  ),
                  StatusBadge(
                    label: alert.sensitivity.label,
                    color: GuardAidColors.info,
                    icon: Icons.tune_rounded,
                  ),
                  if (alert.isBorderline)
                    const StatusBadge(
                      label: 'Borderline review',
                      color: GuardAidColors.violence,
                      icon: Icons.auto_awesome_rounded,
                    ),
                ],
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  const Icon(
                    Icons.location_on_outlined,
                    size: 17,
                    color: GuardAidColors.muted,
                  ),
                  const SizedBox(width: 5),
                  Expanded(
                    child: Text(
                      alert.location,
                      style: const TextStyle(fontSize: 13),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 5),
              Align(
                alignment: Alignment.centerRight,
                child: Text(
                  formatDateTime(alert.detectedAt),
                  style: const TextStyle(
                    fontSize: 12,
                    color: GuardAidColors.muted,
                  ),
                ),
              ),
              if (alert.sceneDescription != null) ...[
                const SizedBox(height: 13),
                Text(
                  alert.sceneDescription!,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: GuardAidColors.muted,
                    height: 1.4,
                  ),
                ),
              ],
              const SizedBox(height: 13),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton.icon(
                  onPressed: onOpen,
                  icon: const Icon(Icons.open_in_new_rounded, size: 18),
                  label: const Text('Open alert'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _EmptyAlerts extends StatelessWidget {
  const _EmptyAlerts();

  @override
  Widget build(BuildContext context) {
    return const Card(
      child: Padding(
        padding: EdgeInsets.all(28),
        child: Column(
          children: [
            Icon(
              Icons.check_circle_outline_rounded,
              size: 48,
              color: GuardAidColors.success,
            ),
            SizedBox(height: 12),
            Text(
              'No matching alerts',
              style: TextStyle(fontWeight: FontWeight.w800, fontSize: 17),
            ),
            SizedBox(height: 5),
            Text(
              'Try changing the search text or filter.',
              style: TextStyle(color: GuardAidColors.muted),
            ),
          ],
        ),
      ),
    );
  }
}
