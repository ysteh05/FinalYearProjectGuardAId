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
  AlertStatus? statusFilter;
  SensitivityMode? sensitivityFilter;
  String? cameraFilter;
  DateTimeRange? dateRangeFilter;

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
      final statusMatches =
          statusFilter == null || alert.status == statusFilter;
      final sensitivityMatches =
          sensitivityFilter == null || alert.sensitivity == sensitivityFilter;
      final cameraMatches =
          cameraFilter == null || alert.cameraName == cameraFilter;
      final dateMatches = dateRangeFilter == null ||
          (!alert.detectedAt.isBefore(dateRangeFilter!.start) &&
              alert.detectedAt.isBefore(
                dateRangeFilter!.end.add(const Duration(days: 1)),
              ));
      return filterMatches &&
          searchMatches &&
          statusMatches &&
          sensitivityMatches &&
          cameraMatches &&
          dateMatches;
    }).toList()..sort((a, b) => b.detectedAt.compareTo(a.detectedAt));
  }

  int get advancedFilterCount =>
      (statusFilter == null ? 0 : 1) +
      (sensitivityFilter == null ? 0 : 1) +
      (cameraFilter == null ? 0 : 1) +
      (dateRangeFilter == null ? 0 : 1);

  Future<void> _openAdvancedFilters() async {
    final cameras = widget.controller.alerts
        .map((alert) => alert.cameraName)
        .toSet()
        .toList()
      ..sort();
    final selection = await showModalBottomSheet<_AdvancedAlertFilters>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (sheetContext) => _AdvancedAlertFilterSheet(
        cameras: cameras,
        status: statusFilter,
        sensitivity: sensitivityFilter,
        camera: cameraFilter,
        dateRange: dateRangeFilter,
      ),
    );
    if (selection == null || !mounted) return;
    setState(() {
      statusFilter = selection.status;
      sensitivityFilter = selection.sensitivity;
      cameraFilter = selection.camera;
      dateRangeFilter = selection.dateRange;
    });
  }

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: widget.controller.refresh,
      child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        slivers: [
          SliverPadding(
            padding: pageListPadding(context),
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
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: AlertFilter.values.map((item) {
                    final label = switch (item) {
                      AlertFilter.all => 'All',
                      AlertFilter.weapon => 'Weapon',
                      AlertFilter.violenceBullying => 'Violence / Bullying',
                      AlertFilter.open => 'Open',
                    };
                    return FilterChip(
                      label: Text(label),
                      selected: filter == item,
                      onSelected: (_) => setState(() => filter = item),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                    onPressed: _openAdvancedFilters,
                    icon: const Icon(Icons.filter_alt_outlined),
                    label: Text(
                      advancedFilterCount == 0
                          ? 'More filters'
                          : 'More filters ($advancedFilterCount)',
                    ),
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

class _AdvancedAlertFilters {
  const _AdvancedAlertFilters({
    this.status,
    this.sensitivity,
    this.camera,
    this.dateRange,
  });

  final AlertStatus? status;
  final SensitivityMode? sensitivity;
  final String? camera;
  final DateTimeRange? dateRange;
}

class _AdvancedAlertFilterSheet extends StatefulWidget {
  const _AdvancedAlertFilterSheet({
    required this.cameras,
    this.status,
    this.sensitivity,
    this.camera,
    this.dateRange,
  });

  final List<String> cameras;
  final AlertStatus? status;
  final SensitivityMode? sensitivity;
  final String? camera;
  final DateTimeRange? dateRange;

  @override
  State<_AdvancedAlertFilterSheet> createState() =>
      _AdvancedAlertFilterSheetState();
}

class _AdvancedAlertFilterSheetState
    extends State<_AdvancedAlertFilterSheet> {
  late AlertStatus? status = widget.status;
  late SensitivityMode? sensitivity = widget.sensitivity;
  late String? camera = widget.camera;
  late DateTimeRange? dateRange = widget.dateRange;

  Future<void> _selectDateRange() async {
    final today = DateTime.now();
    final selected = await showDateRangePicker(
      context: context,
      firstDate: DateTime(today.year - 2),
      lastDate: DateTime(today.year + 1, 12, 31),
      initialDateRange: dateRange,
    );
    if (selected != null && mounted) setState(() => dateRange = selected);
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Filter alerts',
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 6),
            const Text(
              'Use these filters together with the incident-type chips and search field.',
              style: TextStyle(color: GuardAidColors.muted, height: 1.4),
            ),
            const SizedBox(height: 18),
            DropdownButtonFormField<String>(
              initialValue: status?.name ?? 'all',
              decoration: const InputDecoration(labelText: 'Incident status'),
              items: [
                const DropdownMenuItem(value: 'all', child: Text('All statuses')),
                ...AlertStatus.values.map(
                  (item) => DropdownMenuItem(
                    value: item.name,
                    child: Text(item.label),
                  ),
                ),
              ],
              onChanged: (value) => setState(
                () => status = value == null || value == 'all'
                    ? null
                    : AlertStatus.values.byName(value),
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: sensitivity?.name ?? 'all',
              decoration: const InputDecoration(labelText: 'Threshold mode'),
              items: [
                const DropdownMenuItem(value: 'all', child: Text('All modes')),
                ...SensitivityMode.values.map(
                  (item) => DropdownMenuItem(
                    value: item.name,
                    child: Text(item.label),
                  ),
                ),
              ],
              onChanged: (value) => setState(
                () => sensitivity = value == null || value == 'all'
                    ? null
                    : SensitivityMode.values.byName(value),
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<String>(
              initialValue: camera ?? 'all',
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Camera source'),
              items: [
                const DropdownMenuItem(value: 'all', child: Text('All cameras')),
                ...widget.cameras.map(
                  (item) => DropdownMenuItem(value: item, child: Text(item)),
                ),
              ],
              onChanged: (value) => setState(
                () => camera = value == null || value == 'all' ? null : value,
              ),
            ),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: _selectDateRange,
              icon: const Icon(Icons.date_range_outlined),
              label: Text(
                dateRange == null
                    ? 'Choose date range'
                    : '${formatDateTime(dateRange!.start)} – ${formatDateTime(dateRange!.end)}',
              ),
            ),
            if (dateRange != null)
              TextButton(
                onPressed: () => setState(() => dateRange = null),
                child: const Text('Clear date range'),
              ),
            const SizedBox(height: 18),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('Cancel'),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: FilledButton(
                    onPressed: () => Navigator.pop(
                      context,
                      _AdvancedAlertFilters(
                        status: status,
                        sensitivity: sensitivity,
                        camera: camera,
                        dateRange: dateRange,
                      ),
                    ),
                    child: const Text('Apply filters'),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class AlertCard extends StatelessWidget {
  const AlertCard({
    super.key,
    required this.alert,
    required this.onOpen,
    this.actionLabel,
  });

  final GuardAlert alert;
  final VoidCallback onOpen;
  final String? actionLabel;

  @override
  Widget build(BuildContext context) {
    final color = alertTypeColor(alert.type);
    return Semantics(
      button: true,
      label:
          '${alert.type.label} ${alert.status.label.toLowerCase()} record at ${alert.cameraName}',
      hint: alert.status == AlertStatus.open ||
              alert.status == AlertStatus.inReview
          ? 'Double tap to review alert details'
          : 'Double tap to view the saved incident record',
      child: Card(
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
              if (actionLabel != null) ...[
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    onPressed: onOpen,
                    icon: const Icon(Icons.folder_open_outlined, size: 18),
                    label: Text(actionLabel!),
                  ),
                ),
              ],
            ],
          ),
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
