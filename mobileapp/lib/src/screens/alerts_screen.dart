import 'dart:async';

import 'package:flutter/material.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';
import 'alert_detail_screen.dart';

enum AlertFilter { all, weapon, violenceBullying, open }

class AlertsScreen extends StatefulWidget {
  const AlertsScreen({
    super.key,
    required this.controller,
    this.isActive = true,
  });

  final GuardAidController controller;
  final bool isActive;

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen>
    with WidgetsBindingObserver {
  final ScrollController _scrollController = ScrollController();
  Timer? _pollTimer;
  Timer? _noticeTimer;
  Future<void>? _refreshFuture;
  List<GuardAlert> _presentedAlerts = const [];
  Set<String> _knownAlertIds = {};
  Set<String> _pendingAlertIds = {};
  String? _userId;
  String? _arrivalNotice;
  bool _deferredUpdates = false;
  bool _foreground = true;
  AlertFilter filter = AlertFilter.open;
  String query = '';
  AlertStatus? statusFilter;
  String? cameraFilter;
  DateTimeRange? dateRangeFilter;

  @override
  void initState() {
    super.initState();
    _foreground =
        WidgetsBinding.instance.lifecycleState == null ||
        WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed;
    WidgetsBinding.instance.addObserver(this);
    _userId = widget.controller.currentUser?.id;
    _adoptLatest();
    _knownAlertIds = widget.controller.alerts.map((alert) => alert.id).toSet();
    widget.controller.addListener(_onFeedChanged);
    _scrollController.addListener(_onScroll);
    _configurePolling();
  }

  @override
  void didUpdateWidget(covariant AlertsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_onFeedChanged);
      widget.controller.addListener(_onFeedChanged);
      _userId = widget.controller.currentUser?.id;
      _adoptLatest();
      _knownAlertIds = widget.controller.alerts
          .map((alert) => alert.id)
          .toSet();
    }
    if (oldWidget.isActive != widget.isActive) _configurePolling();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
    _configurePolling();
    if (_foreground) _poll();
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _noticeTimer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    widget.controller.removeListener(_onFeedChanged);
    _scrollController.dispose();
    super.dispose();
  }

  bool get _nearTop =>
      !_scrollController.hasClients || _scrollController.offset <= 80;

  void _configurePolling() {
    _pollTimer?.cancel();
    if (widget.isActive && _foreground) {
      _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) => _poll());
    }
  }

  void _poll() {
    if (!mounted ||
        !widget.isActive ||
        !_foreground ||
        ModalRoute.of(context)?.isCurrent != true ||
        widget.controller.isBusy ||
        _refreshFuture != null) {
      return;
    }
    unawaited(_refreshFeed());
  }

  Future<void> _refreshFeed({bool manual = false}) async {
    final request = _refreshFuture ??= widget.controller.refreshAlertFeed();
    try {
      await request;
      if (manual && mounted) setState(_adoptLatest);
    } catch (_) {
      if (manual && mounted) {
        showMessage(
          context,
          'Unable to refresh alerts. Please try again.',
          error: true,
        );
      }
    } finally {
      if (identical(_refreshFuture, request)) _refreshFuture = null;
    }
  }

  void _adoptLatest() {
    _presentedAlerts = List.of(widget.controller.alerts);
    _pendingAlertIds.clear();
    _deferredUpdates = false;
  }

  void _onScroll() {
    if (_nearTop && _deferredUpdates) setState(_adoptLatest);
  }

  void _onFeedChanged() {
    if (!mounted) return;
    final incoming = widget.controller.alerts;
    final ids = incoming.map((alert) => alert.id).toSet();
    final changedUser = _userId != widget.controller.currentUser?.id;
    final arrivals = ids.difference(_knownAlertIds);
    setState(() {
      _userId = widget.controller.currentUser?.id;
      if (changedUser || _nearTop || incoming.isEmpty) {
        _adoptLatest();
      } else {
        // Keep the visible snapshot and its heights stable while browsing old
        // records. Remove inaccessible records, but defer new cards/contents.
        final currentById = {for (final alert in incoming) alert.id: alert};
        _presentedAlerts = _presentedAlerts
            .where((alert) => currentById.containsKey(alert.id))
            .map(
              (alert) => alert.copyWith(isRead: currentById[alert.id]!.isRead),
            )
            .toList(growable: false);
        _pendingAlertIds = ids.difference(
          _presentedAlerts.map((alert) => alert.id).toSet(),
        );
        _deferredUpdates = true;
      }
      _knownAlertIds = ids;
    });
    if (!changedUser &&
        arrivals.isNotEmpty &&
        widget.isActive &&
        _foreground &&
        ModalRoute.of(context)?.isCurrent == true) {
      _noticeTimer?.cancel();
      setState(
        () => _arrivalNotice = arrivals.length == 1
            ? 'New alert received'
            : '${arrivals.length} new alerts received',
      );
      _noticeTimer = Timer(const Duration(seconds: 4), () {
        if (mounted) setState(() => _arrivalNotice = null);
      });
    }
  }

  Future<void> _viewNewAlerts() async {
    if (_scrollController.hasClients) {
      await _scrollController.animateTo(
        0,
        duration: const Duration(milliseconds: 350),
        curve: Curves.easeOut,
      );
    }
    if (mounted) setState(_adoptLatest);
  }

  List<GuardAlert> get filteredAlerts =>
      _presentedAlerts.where(_matchesAlert).toList()
        ..sort((a, b) => b.detectedAt.compareTo(a.detectedAt));

  int get _pendingCount => widget.controller.alerts
      .where(
        (alert) => _pendingAlertIds.contains(alert.id) && _matchesAlert(alert),
      )
      .length;

  bool _matchesAlert(GuardAlert alert) {
    final normalized = query.toLowerCase().trim();
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
    final statusMatches = statusFilter == null || alert.status == statusFilter;
    final cameraMatches =
        cameraFilter == null || alert.cameraName == cameraFilter;
    final dateMatches =
        dateRangeFilter == null ||
        (!alert.detectedAt.isBefore(dateRangeFilter!.start) &&
            alert.detectedAt.isBefore(
              dateRangeFilter!.end.add(const Duration(days: 1)),
            ));
    return filterMatches &&
        searchMatches &&
        statusMatches &&
        cameraMatches &&
        dateMatches;
  }

  int get advancedFilterCount =>
      (statusFilter == null ? 0 : 1) +
      (cameraFilter == null ? 0 : 1) +
      (dateRangeFilter == null ? 0 : 1);

  Future<void> _openAdvancedFilters() async {
    final cameras =
        widget.controller.alerts
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
        camera: cameraFilter,
        dateRange: dateRangeFilter,
      ),
    );
    if (selection == null || !mounted) return;
    setState(() {
      statusFilter = selection.status;
      cameraFilter = selection.camera;
      dateRangeFilter = selection.dateRange;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        RefreshIndicator(
          onRefresh: () => _refreshFeed(manual: true),
          child: CustomScrollView(
            key: const PageStorageKey('alert-centre-scroll'),
            controller: _scrollController,
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [
              SliverPadding(
                padding: pageListPadding(context),
                sliver: SliverList.list(
                  children: [
                    const SectionTitle(
                      'Alert Centre',
                      subtitle:
                          'Review AI-assisted safety alerts and available evidence.',
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
                            children: [
                              active,
                              const SizedBox(height: 10),
                              cameras,
                            ],
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
                          AlertFilter.open => 'Active',
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
                            key: ValueKey(alert.id),
                            alert: alert,
                            showUnreadIndicator: true,
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
        ),
        if (_pendingCount > 0)
          Positioned(
            top: 8,
            left: 16,
            right: 16,
            child: Align(
              alignment: Alignment.topCenter,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 300),
                child: FilledButton(
                  key: const ValueKey('new-alerts-entry'),
                  onPressed: _viewNewAlerts,
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.arrow_upward_rounded, size: 18),
                      const SizedBox(width: 8),
                      Flexible(
                        child: Text(
                          '$_pendingCount new alert${_pendingCount == 1 ? '' : 's'} · View',
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        if (_arrivalNotice != null)
          Positioned(
            bottom: 16,
            left: 20,
            right: 20,
            child: IgnorePointer(
              child: Material(
                key: const ValueKey('new-alerts-toast'),
                color: GuardAidColors.navy,
                elevation: 4,
                borderRadius: BorderRadius.circular(14),
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                  child: Text(
                    _arrivalNotice!,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: Colors.white),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _AdvancedAlertFilters {
  const _AdvancedAlertFilters({
    this.status,
    this.camera,
    this.dateRange,
  });

  final AlertStatus? status;
  final String? camera;
  final DateTimeRange? dateRange;
}

class _AdvancedAlertFilterSheet extends StatefulWidget {
  const _AdvancedAlertFilterSheet({
    required this.cameras,
    this.status,
    this.camera,
    this.dateRange,
  });

  final List<String> cameras;
  final AlertStatus? status;
  final String? camera;
  final DateTimeRange? dateRange;

  @override
  State<_AdvancedAlertFilterSheet> createState() =>
      _AdvancedAlertFilterSheetState();
}

class _AdvancedAlertFilterSheetState extends State<_AdvancedAlertFilterSheet> {
  late AlertStatus? status = widget.status;
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
                const DropdownMenuItem(
                  value: 'all',
                  child: Text('All statuses'),
                ),
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
              initialValue: camera ?? 'all',
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Camera source'),
              items: [
                const DropdownMenuItem(
                  value: 'all',
                  child: Text('All cameras'),
                ),
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
    this.showUnreadIndicator = false,
  });

  final GuardAlert alert;
  final VoidCallback onOpen;
  final String? actionLabel;
  final bool showUnreadIndicator;

  @override
  Widget build(BuildContext context) {
    final color = alertTypeColor(alert.type);
    return Semantics(
      button: true,
      label:
          '${showUnreadIndicator && !alert.isRead ? 'Unread ' : ''}${alert.type.label} ${alert.status.label.toLowerCase()} record at ${alert.cameraName}',
      hint:
          alert.status == AlertStatus.open ||
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
                    Stack(
                      clipBehavior: Clip.none,
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
                        if (showUnreadIndicator && !alert.isRead)
                          Positioned(
                            top: -2,
                            right: -2,
                            child: Tooltip(
                              message: 'Not viewed yet',
                              child: CircleAvatar(
                                key: ValueKey('unread-alert-${alert.id}'),
                                radius: 5,
                                backgroundColor: GuardAidColors.weapon,
                              ),
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
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: _AlertMetric(
                        label: 'Confidence',
                        value: _confidenceLabel(alert.confidence),
                        color: color,
                        icon: Icons.analytics_outlined,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: _AlertMetric(
                        label: 'Review',
                        value: switch (alert.borderlineReviewStatus) {
                          'queued' || 'running' => 'Pending',
                          'positive' => 'Confirmed',
                          'negative' => 'Not confirmed',
                          'failed' => 'Failed',
                          'cancelled' => 'Cancelled',
                          'disabled' => 'Disabled',
                          'legacy_unknown' => 'Unknown',
                          _ => 'Not needed',
                        },
                        color: GuardAidColors.info,
                        icon: Icons.fact_check_outlined,
                      ),
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
                if (alert.sceneSummary != null ||
                    alert.sceneDescription != null ||
                    alert.descriptionStatus == 'pending') ...[
                  const SizedBox(height: 13),
                  AiSceneSummaryView(
                    summary: alert.sceneSummary,
                    legacy: alert.sceneDescription,
                    pending: alert.descriptionStatus == 'pending',
                    compact: true,
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

String _confidenceLabel(double confidence) {
  final percentage = confidence * 100;
  return percentage.roundToDouble() == percentage
      ? '${percentage.toStringAsFixed(0)}%'
      : '${percentage.toStringAsFixed(1)}%';
}

class _AlertMetric extends StatelessWidget {
  const _AlertMetric({
    required this.label,
    required this.value,
    required this.color,
    required this.icon,
  });

  final String label;
  final String value;
  final Color color;
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(minHeight: 58),
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.16)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 14, color: color),
              const SizedBox(width: 5),
              Expanded(
                child: Text(
                  label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: GuardAidColors.muted,
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            value,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              color: color,
              fontSize: 14,
              fontWeight: FontWeight.w900,
            ),
          ),
        ],
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
