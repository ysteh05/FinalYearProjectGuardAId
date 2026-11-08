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
  final ScrollController _filterScrollController = ScrollController();
  final GlobalKey _viewportKey = GlobalKey();
  final Map<String, GlobalKey> _cardKeys = {};
  Timer? _pollTimer;
  Future<void>? _refreshFuture;
  List<GuardAlert> _presentedAlerts = const [];
  Set<String> _knownAlertIds = {};
  final Set<String> _pendingAlertIds = {};
  String? _userId;
  int _anchorRevision = 0;
  bool _preservingPosition = false;
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
    WidgetsBinding.instance.removeObserver(this);
    widget.controller.removeListener(_onFeedChanged);
    _scrollController.dispose();
    _filterScrollController.dispose();
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
  }

  void _onScroll() {
    if (!_preservingPosition && _nearTop && _pendingAlertIds.isNotEmpty) {
      setState(_pendingAlertIds.clear);
    }
  }

  void _onFeedChanged() {
    if (!mounted) return;
    final incoming = widget.controller.alerts;
    final ids = incoming.map((alert) => alert.id).toSet();
    final changedUser = _userId != widget.controller.currentUser?.id;
    final arrivals = ids.difference(_knownAlertIds);
    final atTop = _nearTop;
    final anchor = changedUser || atTop
        ? null
        : _captureAnchor(
            incoming.where(_matchesAlert).map((alert) => alert.id).toSet(),
          );
    final revision = ++_anchorRevision;
    setState(() {
      _userId = widget.controller.currentUser?.id;
      // New alerts are part of the list immediately, including while the user
      // is reading older records. The reminder only offers a shortcut to top.
      _presentedAlerts = List.of(incoming);
      if (changedUser || atTop || incoming.isEmpty) {
        _pendingAlertIds.clear();
      } else {
        _pendingAlertIds.retainAll(ids);
        _pendingAlertIds.addAll(arrivals);
      }
      _cardKeys.removeWhere((id, _) => !ids.contains(id));
      _knownAlertIds = ids;
    });
    if (anchor != null) _restoreAnchor(anchor, revision);
  }

  _FeedAnchor? _captureAnchor(Set<String> remainingIds) {
    if (!_scrollController.hasClients) return null;
    final viewport = _viewportKey.currentContext?.findRenderObject();
    if (viewport is! RenderBox || !viewport.hasSize) return null;
    final top = viewport.localToGlobal(Offset.zero).dy;
    final bottom = top + viewport.size.height;
    for (final alert in filteredAlerts) {
      if (!remainingIds.contains(alert.id)) continue;
      final box = _cardKeys[alert.id]?.currentContext?.findRenderObject();
      if (box is! RenderBox || !box.attached || !box.hasSize) continue;
      final y = box.localToGlobal(Offset.zero).dy;
      if (y < bottom && y + box.size.height > top) {
        return _FeedAnchor(
          alert.id,
          y,
          _scrollController.offset,
          _scrollController.position.maxScrollExtent,
        );
      }
    }
    return null;
  }

  void _restoreAnchor(
    _FeedAnchor anchor,
    int revision, [
    int attempt = 0,
    double appliedCorrection = 0,
  ]) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted ||
          revision != _anchorRevision ||
          !_scrollController.hasClients ||
          _nearTop) {
        return;
      }
      final position = _scrollController.position;
      final box = _cardKeys[anchor.id]?.currentContext?.findRenderObject();
      final canMeasure = box is RenderBox && box.attached && box.hasSize;
      // A large burst can move the anchor outside the lazy layout cache. Bring
      // it back into range first, then correct using its actual screen position.
      final correction = canMeasure
          ? box.localToGlobal(Offset.zero).dy -
                anchor.top +
                (position.pixels - anchor.scrollOffset - appliedCorrection)
          : position.maxScrollExtent - anchor.maxScrollExtent;
      final target = (position.pixels + correction)
          .clamp(position.minScrollExtent, position.maxScrollExtent)
          .toDouble();
      if ((target - position.pixels).abs() > 0.5) {
        appliedCorrection += target - position.pixels;
        _preservingPosition = true;
        _scrollController.jumpTo(target);
        _preservingPosition = false;
      }
      if (!canMeasure && attempt == 0) {
        _restoreAnchor(anchor, revision, attempt + 1, appliedCorrection);
      }
    });
  }

  Future<void> _viewNewAlerts() async {
    if (_scrollController.hasClients) {
      await _scrollController.animateTo(
        0,
        duration: const Duration(milliseconds: 350),
        curve: Curves.easeOut,
      );
    }
    if (mounted) setState(_pendingAlertIds.clear);
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
      key: _viewportKey,
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
                          compact: true,
                        );
                        final cameras = MetricCard(
                          label: 'Monitored cameras',
                          value: '${widget.controller.monitoredCameraCount ?? '—'}',
                          icon: Icons.videocam_outlined,
                          color: GuardAidColors.info,
                          compact: true,
                        );
                        return IntrinsicHeight(
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              Expanded(child: active),
                              const SizedBox(width: 10),
                              Expanded(child: cameras),
                            ],
                          ),
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
                    Scrollbar(
                      controller: _filterScrollController,
                      thumbVisibility: true,
                      trackVisibility: true,
                      thickness: 3,
                      radius: const Radius.circular(8),
                      child: SingleChildScrollView(
                        key: const ValueKey('alert-filter-horizontal-scroll'),
                        controller: _filterScrollController,
                        scrollDirection: Axis.horizontal,
                        physics: const BouncingScrollPhysics(),
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Row(
                          children: AlertFilter.values.map((item) {
                            final label = switch (item) {
                              AlertFilter.all => 'All',
                              AlertFilter.weapon => 'Weapon',
                              AlertFilter.violenceBullying =>
                                'Violence / Bullying',
                              AlertFilter.open => 'Active',
                            };
                            return Padding(
                              padding: const EdgeInsets.only(right: 8),
                              child: FilterChip(
                                label: Text(label, softWrap: false),
                                selected: filter == item,
                                onSelected: (_) =>
                                    setState(() => filter = item),
                              ),
                            );
                          }).toList(),
                        ),
                      ),
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
                          key: _cardKeys.putIfAbsent(alert.id, GlobalKey.new),
                          padding: const EdgeInsets.only(bottom: 12),
                          child: AlertCard(
                            key: ValueKey(alert.id),
                            alert: alert,
                            showUnreadIndicator: true,
                            onOpen: () async {
                              await Navigator.of(context).push<void>(
                                MaterialPageRoute<void>(
                                  builder: (_) => AlertDetailScreen(
                                    controller: widget.controller,
                                    alertId: alert.id,
                                  ),
                                ),
                              );
                              if (mounted) _poll();
                            },
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
              child: Semantics(
                key: const ValueKey('new-alerts-toast'),
                liveRegion: true,
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
          ),
        if (widget.controller.refreshErrors.isNotEmpty)
          Positioned(
            left: 16, right: 16, bottom: 8,
            child: IgnorePointer(
              child: Material(
                elevation: 2,
                borderRadius: BorderRadius.circular(8),
                color: Theme.of(context).colorScheme.surfaceContainerHigh,
                child: Padding(
                  padding: const EdgeInsets.all(8),
                  child: Text(
                    widget.controller.refreshErrors.keys.map((key) {
                      final last = widget.controller.lastRefreshSuccess[key];
                      return '$key: ${last == null ? 'unavailable' : 'out of date (last updated ${last.toLocal().toString().substring(11, 19)})'}';
                    }).join(' · '),
                    style: Theme.of(context).textTheme.bodySmall,
                    semanticsLabel: 'Some data could not be refreshed. Retrying automatically.',
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _FeedAnchor {
  const _FeedAnchor(this.id, this.top, this.scrollOffset, this.maxScrollExtent);

  final String id;
  final double top;
  final double scrollOffset;
  final double maxScrollExtent;
}

class _AdvancedAlertFilters {
  const _AdvancedAlertFilters({this.status, this.camera, this.dateRange});

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
                _AlertCardHeader(
                  alert: alert,
                  color: color,
                  showUnreadIndicator: showUnreadIndicator,
                ),
                const SizedBox(height: 12),
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
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 13,
                          color: GuardAidColors.navySoft,
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 11,
                  ),
                  decoration: BoxDecoration(
                    color: GuardAidColors.canvas,
                    borderRadius: BorderRadius.circular(13),
                  ),
                  child: _AlertConfidenceAndTime(
                    confidence: alert.confidence,
                    detectedAt: alert.detectedAt,
                  ),
                ),
                if (alert.sceneSummary != null ||
                    alert.sceneDescription != null ||
                    alert.descriptionStatus == 'pending') ...[
                  const SizedBox(height: 9),
                  const Divider(
                    height: 20,
                    thickness: 0.8,
                    color: GuardAidColors.border,
                  ),
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

class _AlertCardHeader extends StatelessWidget {
  const _AlertCardHeader({
    required this.alert,
    required this.color,
    required this.showUnreadIndicator,
  });

  final GuardAlert alert;
  final Color color;
  final bool showUnreadIndicator;

  @override
  Widget build(BuildContext context) {
    final textScale = MediaQuery.textScalerOf(context).scale(1);
    return LayoutBuilder(
      builder: (context, constraints) {
        final stackStatus = constraints.maxWidth < 270 || textScale > 1.25;
        final icon = Stack(
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
        );
        final title = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              alert.type.label,
              style: const TextStyle(
                fontSize: 17,
                height: 1.15,
                fontWeight: FontWeight.w900,
              ),
            ),
            const SizedBox(height: 3),
            Text(
              alert.cameraName,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: GuardAidColors.muted),
            ),
          ],
        );
        final status = _AlertStatusChip(status: alert.status);

        if (stackStatus) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  icon,
                  const SizedBox(width: 12),
                  Expanded(child: title),
                ],
              ),
              const SizedBox(height: 9),
              Align(alignment: Alignment.centerRight, child: status),
            ],
          );
        }

        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            icon,
            const SizedBox(width: 12),
            Expanded(child: title),
            const SizedBox(width: 8),
            status,
          ],
        );
      },
    );
  }
}

class _AlertStatusChip extends StatelessWidget {
  const _AlertStatusChip({required this.status});

  final AlertStatus status;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      AlertStatus.open => const Color(0xFFAA5A0A),
      AlertStatus.inReview => GuardAidColors.info,
      AlertStatus.resolved => GuardAidColors.success,
      AlertStatus.dismissed => GuardAidColors.muted,
      AlertStatus.escalated => const Color(0xFF7B2CBF),
    };

    return Container(
      constraints: BoxConstraints(
        maxWidth: (MediaQuery.sizeOf(context).width * 0.46)
            .clamp(96, 176)
            .toDouble(),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.09),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.22)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 7),
          Flexible(
            child: Text(
              status.label,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: color,
                fontSize: 12,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
        ],
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

class _AlertConfidenceAndTime extends StatelessWidget {
  const _AlertConfidenceAndTime({
    required this.confidence,
    required this.detectedAt,
  });

  final double confidence;
  final DateTime detectedAt;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final stackFields =
            constraints.maxWidth < 240 ||
            MediaQuery.textScalerOf(context).scale(1) > 1.25;
        final confidenceCell = _AlertInfoCell(
          label: 'Detection confidence',
          value: _confidenceLabel(confidence),
          prominent: true,
        );
        final detectedCell = _AlertInfoCell(
          label: 'Detected',
          value: formatDateTime(detectedAt),
          alignEnd: !stackFields,
        );

        if (stackFields) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              confidenceCell,
              const SizedBox(height: 10),
              detectedCell,
            ],
          );
        }

        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: confidenceCell),
            Container(
              width: 1,
              height: 40,
              margin: const EdgeInsets.symmetric(horizontal: 12),
              color: GuardAidColors.border.withValues(alpha: 0.6),
            ),
            Expanded(child: detectedCell),
          ],
        );
      },
    );
  }
}

class _AlertInfoCell extends StatelessWidget {
  const _AlertInfoCell({
    required this.label,
    required this.value,
    this.alignEnd = false,
    this.prominent = false,
  });

  final String label;
  final String value;
  final bool alignEnd;
  final bool prominent;

  @override
  Widget build(BuildContext context) {
    final alignment = alignEnd
        ? CrossAxisAlignment.end
        : CrossAxisAlignment.start;
    return Column(
      crossAxisAlignment: alignment,
      children: [
        Text(
          label,
          textAlign: alignEnd ? TextAlign.end : TextAlign.start,
          style: const TextStyle(
            color: GuardAidColors.muted,
            fontSize: 12,
            fontWeight: FontWeight.w400,
          ),
        ),
        const SizedBox(height: 3),
        Text(
          value,
          textAlign: alignEnd ? TextAlign.end : TextAlign.start,
          style: TextStyle(
            color: GuardAidColors.navy,
            fontSize: prominent ? 16 : 14,
            height: 1.35,
            fontWeight: prominent ? FontWeight.w600 : FontWeight.w500,
          ),
        ),
      ],
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
