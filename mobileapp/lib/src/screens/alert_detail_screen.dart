import 'dart:async';

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';
import 'emergency_call_simulation_screen.dart';
import 'weapon_photo_gallery.dart';

class AlertDetailScreen extends StatefulWidget {
  const AlertDetailScreen({
    super.key,
    required this.controller,
    required this.alertId,
  });

  final GuardAidController controller;
  final String alertId;

  @override
  State<AlertDetailScreen> createState() => _AlertDetailScreenState();
}

class _AlertDetailScreenState extends State<AlertDetailScreen> {
  bool submitting = false;
  bool loadingDetails = false;
  Timer? detailRefreshTimer;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onControllerChanged);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!widget.controller.repository.isDemo) {
        _refreshDetails();
        detailRefreshTimer = Timer.periodic(
          const Duration(seconds: 10),
          (_) => _refreshDetails(),
        );
      }
      if (mounted) widget.controller.markAlertRead(widget.alertId);
    });
  }

  void _onControllerChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _refreshDetails() async {
    if (!mounted || loadingDetails) return;
    loadingDetails = true;
    try {
      await widget.controller.loadAlertDetails(widget.alertId);
    } finally {
      loadingDetails = false;
    }
  }

  @override
  void dispose() {
    detailRefreshTimer?.cancel();
    widget.controller.removeListener(_onControllerChanged);
    super.dispose();
  }

  GuardAlert get alert =>
      widget.controller.alerts.firstWhere((item) => item.id == widget.alertId);

  Future<void> _confirmIncident() async {
    List<BroadcastRoleOption> options;
    try {
      options = await widget.controller.loadBroadcastRoleOptions(alert.id);
    } catch (error) {
      if (mounted) {
        showMessage(
          context,
          'Could not load broadcast roles: $error',
          error: true,
        );
      }
      return;
    }
    if (!mounted) return;
    final selectedRoles = options
        .where((option) => option.defaultSelected)
        .map((option) => option.id)
        .toSet();
    var rememberedRoleSelection = Set<String>.of(selectedRoles);
    var choice = 'confirm_only';
    String? broadcastRoleError;
    var commentText = '';
    final result = await showModalBottomSheet<_ConfirmResult>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => StatefulBuilder(
        builder: (context, setModalState) {
          return Padding(
            padding: EdgeInsets.fromLTRB(
              20,
              6,
              20,
              20 + MediaQuery.viewInsetsOf(context).bottom,
            ),
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'Confirm incident',
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.w900),
                  ),
                  const SizedBox(height: 6),
                  const Text(
                    'Choose whether to request help, take ownership, or record that the incident is already resolved.',
                  ),
                  RadioGroup<String>(
                    groupValue: choice,
                    onChanged: (value) {
                      if (value != null) {
                        setModalState(() {
                          if (value == 'confirm_only') {
                            selectedRoles.addAll(rememberedRoleSelection);
                          } else {
                            if (selectedRoles.isNotEmpty) {
                              rememberedRoleSelection = Set<String>.of(
                                selectedRoles,
                              );
                            }
                            selectedRoles.clear();
                          }
                          choice = value;
                          broadcastRoleError = null;
                        });
                      }
                    },
                    child: const Column(
                      children: [
                        RadioListTile<String>(
                          contentPadding: EdgeInsets.zero,
                          title: Text('Confirm only · request a handler'),
                          subtitle: Text('Broadcast to selected roles.'),
                          value: 'confirm_only',
                        ),
                        RadioListTile<String>(
                          contentPadding: EdgeInsets.zero,
                          title: Text('Confirm and handle it myself'),
                          value: 'confirm_and_handle',
                        ),
                        RadioListTile<String>(
                          contentPadding: EdgeInsets.zero,
                          title: Text('Confirm · already resolved'),
                          value: 'confirm_resolved',
                        ),
                      ],
                    ),
                  ),
                  if (choice == 'confirm_only') ...[
                    const Text(
                      'Broadcast roles (at least one required)',
                      style: TextStyle(fontWeight: FontWeight.w800),
                    ),
                    ...options.map(
                      (option) => CheckboxListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        title: Text(option.name),
                        subtitle: Text(
                          'Sensitivity: ${option.sensitivity.label}',
                        ),
                        value: selectedRoles.contains(option.id),
                        onChanged: (value) => setModalState(() {
                          if (value == true) {
                            selectedRoles.add(option.id);
                          } else {
                            selectedRoles.remove(option.id);
                          }
                          rememberedRoleSelection = Set<String>.of(
                            selectedRoles,
                          );
                          broadcastRoleError = null;
                        }),
                      ),
                    ),
                  ],
                  TextField(
                    minLines: 2,
                    maxLines: 4,
                    maxLength: 1000,
                    onChanged: (value) => commentText = value,
                    decoration: const InputDecoration(
                      labelText: 'Comment (optional)',
                    ),
                  ),
                  if (broadcastRoleError != null) ...[
                    const SizedBox(height: 12),
                    Semantics(
                      liveRegion: true,
                      child: Container(
                        key: const ValueKey('broadcast-role-validation-error'),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: Theme.of(context).colorScheme.errorContainer,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Icon(
                              Icons.error_outline_rounded,
                              color: Theme.of(
                                context,
                              ).colorScheme.onErrorContainer,
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: Text(
                                broadcastRoleError!,
                                style: TextStyle(
                                  color: Theme.of(
                                    context,
                                  ).colorScheme.onErrorContainer,
                                  height: 1.35,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                  FilledButton(
                    onPressed: () {
                      if (choice == 'confirm_only' && selectedRoles.isEmpty) {
                        setModalState(
                          () => broadcastRoleError =
                              'Select at least one Broadcast Role.',
                        );
                        return;
                      }
                      Navigator.pop(
                        context,
                        _ConfirmResult(
                          choice,
                          commentText.trim(),
                          selectedRoles.toList(),
                        ),
                      );
                    },
                    child: const Text('Confirm incident'),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
    if (result == null || !mounted) return;
    await _submitWorkflow(
      result.action,
      comment: result.comment,
      roleIds: result.roleIds,
    );
  }

  Future<void> _dismissAsFalseAlarm() async {
    var commentText = '';
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          8,
          20,
          20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Dismiss as false alarm',
              style: TextStyle(fontSize: 21, fontWeight: FontWeight.w900),
            ),
            const SizedBox(height: 8),
            const Text('This closes the alert. A comment is optional.'),
            TextField(
              minLines: 2,
              maxLines: 4,
              maxLength: 1000,
              onChanged: (value) => commentText = value,
              decoration: const InputDecoration(
                labelText: 'Comment (optional)',
              ),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Dismiss alert'),
            ),
          ],
        ),
      ),
    );
    if (result != true || !mounted) return;
    await _submitWorkflow('dismiss_false_alarm', comment: commentText.trim());
  }

  Future<void> _submitWorkflow(
    String action, {
    String? comment,
    List<String> roleIds = const [],
  }) async {
    setState(() => submitting = true);
    final submission = await widget.controller.submitWorkflowAction(
      alert.id,
      action,
      comment: comment,
      recipientRoleIds: roleIds,
    );
    final success = submission != null;
    if (!mounted) return;
    setState(() => submitting = false);
    final successMessage = submission?.idempotent == true
        ? 'Your earlier response was already saved. The latest incident details are shown.'
        : action == 'confirm_only' && submission != null
        ? submission.idempotent
              ? 'The incident decision was already saved. Review the current alert status.'
              : submission.broadcastDispatchesQueued == 0
              ? 'Incident confirmed. The selected-role response phase is active.'
              : 'Incident confirmed. A Broadcast notification was queued for the selected roles.'
        : switch (action) {
            'dismiss_false_alarm' => 'Alert dismissed as a false alarm.',
            'confirm_and_handle' =>
              'Incident confirmed. You are now the handler.',
            'confirm_resolved' =>
              'Incident confirmed and recorded as resolved.',
            'take_ownership' => 'You are now handling this incident.',
            'resolve' => 'Incident resolved.',
            _ => 'Alert updated.',
          };
    showMessage(
      context,
      success
          ? successMessage
          : widget.controller.error ?? 'Unable to update the alert.',
      error: !success,
    );
  }

  Future<void> _resolveIncident() async {
    var commentText = '';
    final confirmed = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => Padding(
        padding: EdgeInsets.fromLTRB(
          20,
          8,
          20,
          20 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Resolve incident',
              style: TextStyle(fontSize: 21, fontWeight: FontWeight.w900),
            ),
            const Text(
              'Confirm that the incident has been handled. A comment is optional.',
            ),
            TextField(
              minLines: 2,
              maxLines: 4,
              maxLength: 1000,
              onChanged: (value) => commentText = value,
              decoration: const InputDecoration(
                labelText: 'Comment (optional)',
              ),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Mark resolved'),
            ),
          ],
        ),
      ),
    );
    if (confirmed != true || !mounted) return;
    await _submitWorkflow('resolve', comment: commentText.trim());
  }

  Future<void> _simulateEmergencyCall() async {
    setState(() => submitting = true);
    final success = await widget.controller.simulateEmergencyCall(alert.id);
    if (!mounted) return;
    setState(() => submitting = false);
    if (!success) {
      showMessage(
        context,
        widget.controller.error ?? 'Unable to start the call simulation.',
        error: true,
      );
      return;
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (_) => EmergencyCallSimulationScreen(
          location: alert.location,
          cameraName: alert.cameraName,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final current = alert;
    final color = alertTypeColor(current.type);
    final weaponEvidence = current.evidence
        .where((item) => item.mediaType == 'weapon_screenshot')
        .toList(growable: false);
    final otherEvidence = current.evidence
        .where((item) => item.mediaType != 'weapon_screenshot')
        .toList(growable: false);
    final broadcastNames = current.broadcastRoleIds
        .map(
          (id) => widget.controller.roles
              .where((role) => role.id == id)
              .map((role) => role.name)
              .firstOrNull,
        )
        .whereType<String>()
        .join(', ');
    final decisionEntries =
        current.actions.indexed.where((entry) {
          final item = entry.$2;
          return item.action != 'simulated_emergency_call' &&
              item.action != 'acknowledge' &&
              item.action != 'review';
        }).toList()..sort((a, b) {
          final left = a.$2.createdAt;
          final right = b.$2.createdAt;
          // Known timestamps first; preserve supplied order for equal/unknown dates.
          if (left == null && right == null) return a.$1.compareTo(b.$1);
          if (left == null) return 1;
          if (right == null) return -1;
          final byDate = right.compareTo(left);
          return byDate == 0 ? a.$1.compareTo(b.$1) : byDate;
        });
    final decisions = decisionEntries
        .map((entry) => entry.$2)
        .toList(growable: false);
    final simulatedActivities = current.actions
        .where((item) => item.action == 'simulated_emergency_call')
        .toList(growable: false);
    final canAct = switch (current.workflowStatus) {
      AlertWorkflowStatus.awaitingConfirmation ||
      AlertWorkflowStatus.confirmedAwaitingHandler => true,
      AlertWorkflowStatus.inProgress =>
        current.handlerUserId == widget.controller.currentUser?.id,
      _ => false,
    };
    return Scaffold(
      appBar: AppBar(title: const Text('Alert review')),
      body: ListView(
        padding: pageListPadding(context, top: 8),
        children: [
          if (widget.controller.pendingWorkflowRequest(current.id)
              case final pending?)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Response awaiting confirmation',
                      style: TextStyle(fontWeight: FontWeight.w800),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Your previous response could not be confirmed. Retry to check whether it was saved.',
                    ),
                    const SizedBox(height: 8),
                    FilledButton.icon(
                      onPressed: submitting
                          ? null
                          : () => _submitWorkflow(
                              pending.action,
                              comment: pending.comment,
                              roleIds: pending.roleIds,
                            ),
                      icon: const Icon(Icons.refresh_rounded),
                      label: Text(submitting ? 'Checking…' : 'Retry response'),
                    ),
                  ],
                ),
              ),
            ),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  AlertDetailHeading(
                    title: current.type.label,
                    status: current.workflowStatus.label,
                    color: alertStatusColor(current.status),
                  ),
                  if (decisions.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    _DecisionSummary(actions: decisions),
                  ] else ...[
                    const SizedBox(height: 16),
                    const _NoDecisionSummary(),
                  ],
                  if (simulatedActivities.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    _SimulatedActivitySummary(actions: simulatedActivities),
                  ],
                  const SizedBox(height: 16),
                  _DetailRow(
                    icon: Icons.videocam_outlined,
                    label: 'Camera',
                    value: current.cameraName,
                  ),
                  _DetailRow(
                    icon: Icons.location_on_outlined,
                    label: 'Location',
                    value: current.location,
                  ),
                  _DetailRow(
                    icon: Icons.schedule_rounded,
                    label: 'Detected',
                    value: formatDateTime(current.detectedAt),
                  ),
                  _DetailRow(
                    icon: Icons.analytics_outlined,
                    label: 'Confidence',
                    value: '${(current.confidence * 100).toStringAsFixed(1)}%',
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 14),
          const SectionTitle('Evidence'),
          const SizedBox(height: 10),
          if (current.evidence.isEmpty)
            _EvidenceUnavailable(status: current.evidenceStatus)
          else ...[
            if (weaponEvidence.isNotEmpty)
              _WeaponEvidenceGallery(evidence: weaponEvidence, accent: color),
            ...otherEvidence.map(
              (item) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: EvidenceViewer(evidence: item, accent: color),
              ),
            ),
          ],
          const SizedBox(height: 8),
          const SectionTitle(
            'AI scene summary',
            subtitle:
                'Generated by MiniCPM V4.7 1B for human decision support.',
          ),
          const SizedBox(height: 10),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: AiSceneSummaryView(
                summary: current.sceneSummary,
                legacy: current.sceneDescription,
                pending: current.descriptionStatus == 'pending',
              ),
            ),
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: const Color(0xFFFFF5E8),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: const Color(0xFFF0D2A8)),
            ),
            child: const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  Icons.info_outline_rounded,
                  color: GuardAidColors.violence,
                ),
                SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'This alert was generated by AI. Review all available evidence and follow school safety procedures before deciding.',
                    style: TextStyle(height: 1.45),
                  ),
                ),
              ],
            ),
          ),
          if (current.actionNote != null && current.actions.isEmpty) ...[
            const SizedBox(height: 14),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(
                      Icons.history_rounded,
                      color: GuardAidColors.muted,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        current.actionNote!,
                        style: const TextStyle(height: 1.4),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
          if (canAct &&
              widget.controller.pendingWorkflowRequest(current.id) == null) ...[
            const SizedBox(height: 22),
            const SectionTitle('Response actions'),
            const SizedBox(height: 10),
            if (submitting) ...[
              const LinearProgressIndicator(),
              const SizedBox(height: 10),
              const Text(
                'Saving response… Please wait.',
                style: TextStyle(color: GuardAidColors.muted),
              ),
              const SizedBox(height: 8),
            ],
            if (current.workflowStatus ==
                AlertWorkflowStatus.awaitingConfirmation) ...[
              OutlinedButton.icon(
                onPressed: submitting ? null : _dismissAsFalseAlarm,
                icon: const Icon(Icons.cancel_outlined),
                label: Text(submitting ? 'Saving…' : 'Dismiss as false alarm'),
              ),
              const SizedBox(height: 10),
              FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: GuardAidColors.success,
                ),
                onPressed: submitting ? null : _confirmIncident,
                icon: const Icon(Icons.verified_outlined),
                label: Text(submitting ? 'Saving…' : 'Confirm incident'),
              ),
            ] else if (current.workflowStatus ==
                AlertWorkflowStatus.confirmedAwaitingHandler) ...[
              Text(
                'Confirmed. Broadcast requested to ${broadcastNames.isEmpty ? 'the selected roles' : broadcastNames}. No handler has taken ownership yet.',
                style: const TextStyle(color: GuardAidColors.muted),
              ),
              const SizedBox(height: 10),
              FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: GuardAidColors.success,
                ),
                onPressed: submitting
                    ? null
                    : () => _submitWorkflow('take_ownership'),
                icon: const Icon(Icons.person_pin_circle_outlined),
                label: Text(submitting ? 'Saving…' : 'Take ownership'),
              ),
            ] else if (current.workflowStatus ==
                AlertWorkflowStatus.inProgress) ...[
              Text(
                'You are handling this incident${current.handlerName == null ? '' : ' · ${current.handlerName}'}.',
                style: const TextStyle(color: GuardAidColors.muted),
              ),
              const SizedBox(height: 10),
              FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: GuardAidColors.success,
                ),
                onPressed: submitting ? null : _resolveIncident,
                icon: const Icon(Icons.check_circle_outline_rounded),
                label: Text(submitting ? 'Saving…' : 'Mark incident resolved'),
              ),
            ],
            if (current.isWorkflowActive &&
                current.workflowStatus !=
                    AlertWorkflowStatus.awaitingConfirmation) ...[
              const SizedBox(height: 10),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: GuardAidColors.weapon,
                ),
                onPressed: submitting ? null : _simulateEmergencyCall,
                icon: const Icon(Icons.phone_in_talk_outlined),
                label: Text(submitting ? 'Saving…' : 'Simulate emergency call'),
              ),
            ],
          ],
          if (current.workflowStatus == AlertWorkflowStatus.inProgress &&
              current.handlerUserId != widget.controller.currentUser?.id) ...[
            const SizedBox(height: 18),
            Card(
              child: ListTile(
                leading: const Icon(Icons.person_pin_circle_outlined),
                title: const Text('Current handler'),
                subtitle: Text(current.handlerName ?? 'An authorised user'),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _ConfirmResult {
  const _ConfirmResult(this.action, this.comment, this.roleIds);

  final String action;
  final String comment;
  final List<String> roleIds;
}

class _NoDecisionSummary extends StatelessWidget {
  const _NoDecisionSummary();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: const Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            Icons.pending_actions_rounded,
            size: 18,
            color: GuardAidColors.muted,
          ),
          SizedBox(width: 8),
          Expanded(
            child: Text(
              'No decision recorded yet',
              style: TextStyle(
                fontSize: 13,
                fontWeight: FontWeight.w400,
                color: GuardAidColors.muted,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SimulatedActivitySummary extends StatelessWidget {
  const _SimulatedActivitySummary({required this.actions});

  final List<AlertActionRecord> actions;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: GuardAidColors.info.withValues(alpha: 0.05),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: GuardAidColors.info.withValues(alpha: 0.18)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Simulated activity',
            style: TextStyle(
              color: GuardAidColors.muted,
              fontSize: 12,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 7),
          for (final action in actions)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                '${action.actionLabel} by ${action.performedBy}${action.createdAt == null ? '' : ' · ${formatDateTime(action.createdAt!)}'}',
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
            ),
        ],
      ),
    );
  }
}

class _DecisionSummary extends StatelessWidget {
  const _DecisionSummary({required this.actions});

  final List<AlertActionRecord> actions;

  @override
  Widget build(BuildContext context) {
    final latest = actions.first;
    return Container(
      key: const ValueKey('latest-decision-card'),
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: GuardAidColors.navy.withValues(alpha: 0.045),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: GuardAidColors.navy.withValues(alpha: 0.12)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Latest decision',
            style: TextStyle(
              color: GuardAidColors.muted,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 10),
          _DecisionEntry(action: latest),
          if (actions.length > 1) ...[
            const Divider(height: 20),
            ExpansionTile(
              key: const ValueKey('previous-decisions'),
              tilePadding: EdgeInsets.zero,
              childrenPadding: EdgeInsets.zero,
              shape: const Border(),
              collapsedShape: const Border(),
              title: Text(
                'Previous decisions (${actions.length - 1})',
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                ),
              ),
              children: [
                for (var index = 1; index < actions.length; index++)
                  _DecisionEntry(
                    action: actions[index],
                    connectNext: index < actions.length - 1,
                    history: true,
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _DecisionEntry extends StatelessWidget {
  const _DecisionEntry({
    required this.action,
    this.connectNext = false,
    this.history = false,
  });

  final AlertActionRecord action;
  final bool connectNext;
  final bool history;

  (IconData, Color) get appearance => switch (action.action) {
    'dismiss' ||
    'dismiss_false_alarm' => (Icons.cancel_outlined, GuardAidColors.muted),
    'resolve' ||
    'confirm_resolved' => (Icons.task_alt_rounded, GuardAidColors.success),
    'confirm_only' ||
    'confirm_and_handle' => (Icons.verified_outlined, GuardAidColors.success),
    'take_ownership' => (Icons.person_pin_circle_outlined, GuardAidColors.info),
    'escalate' => (Icons.priority_high_rounded, GuardAidColors.weapon),
    _ => (Icons.history_rounded, GuardAidColors.muted),
  };

  @override
  Widget build(BuildContext context) {
    final (icon, color) = appearance;
    return Stack(
      children: [
        if (connectNext)
          Positioned(
            left: 12,
            top: 26,
            bottom: 0,
            child: Container(width: 1, color: GuardAidColors.border),
          ),
        Padding(
          padding: EdgeInsets.only(bottom: history ? 16 : 0),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: 26,
                height: 26,
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.09),
                  shape: BoxShape.circle,
                ),
                child: Icon(icon, size: 17, color: color),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      action.actionLabel,
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                        color: GuardAidColors.navy,
                        height: 1.3,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'By ${action.performedBy}',
                      style: const TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.w400,
                        color: GuardAidColors.navy,
                        height: 1.35,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      action.createdAt == null
                          ? 'Time not recorded'
                          : formatDateTime(action.createdAt!),
                      style: const TextStyle(
                        color: GuardAidColors.muted,
                        fontSize: 12,
                        fontWeight: FontWeight.w400,
                      ),
                    ),
                    if (action.note?.trim().isNotEmpty == true) ...[
                      const SizedBox(height: 10),
                      const Text(
                        'Comment',
                        style: TextStyle(
                          fontSize: 12,
                          color: GuardAidColors.muted,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        action.note!.trim(),
                        style: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w400,
                          height: 1.4,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({
    required this.icon,
    required this.label,
    required this.value,
  });

  final IconData icon;
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final caption = Text(
            '$label:',
            semanticsLabel: label == 'Confidence'
                ? 'Detection confidence'
                : label,
            style: const TextStyle(
              color: GuardAidColors.muted,
              fontSize: 14,
              fontWeight: FontWeight.w400,
            ),
          );
          final content = Text(
            value,
            style: const TextStyle(
              color: GuardAidColors.navy,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          );
          final stacked =
              MediaQuery.textScalerOf(context).scale(1) > 1.4 ||
              constraints.maxWidth < 240;
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, size: 19, color: GuardAidColors.muted),
              const SizedBox(width: 9),
              if (stacked)
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [caption, const SizedBox(height: 4), content],
                  ),
                )
              else ...[
                SizedBox(
                  width: constraints.maxWidth < 300 ? 96 : 126,
                  child: caption,
                ),
                Expanded(child: content),
              ],
            ],
          );
        },
      ),
    );
  }
}

class _EvidenceUnavailable extends StatelessWidget {
  const _EvidenceUnavailable({required this.status});

  final String status;

  String get message => switch (status) {
    'preparing' => 'Evidence is being prepared for this alert.',
    'failed' =>
      'Photos or video could not be saved. You can still review and respond to this alert.',
    'available' => 'No evidence media is currently attached to this record.',
    _ => 'Evidence availability was not recorded for this historical alert.',
  };

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(22),
        child: Row(
          children: [
            const Icon(Icons.hide_image_outlined, color: GuardAidColors.muted),
            const SizedBox(width: 12),
            Expanded(child: Text(message)),
          ],
        ),
      ),
    );
  }
}

class EvidenceViewer extends StatefulWidget {
  const EvidenceViewer({
    super.key,
    required this.evidence,
    required this.accent,
  });

  final EvidenceRecord evidence;
  final Color accent;

  @override
  State<EvidenceViewer> createState() => _EvidenceViewerState();
}

class _WeaponEvidenceGallery extends StatefulWidget {
  const _WeaponEvidenceGallery({required this.evidence, required this.accent});

  final List<EvidenceRecord> evidence;
  final Color accent;

  @override
  State<_WeaponEvidenceGallery> createState() => _WeaponEvidenceGalleryState();
}

class _WeaponEvidenceGalleryState extends State<_WeaponEvidenceGallery> {
  final PageController _pageController = PageController();
  int _page = 0;

  @override
  void didUpdateWidget(covariant _WeaponEvidenceGallery oldWidget) {
    super.didUpdateWidget(oldWidget);
    final selectedId = oldWidget.evidence[_page].id;
    final index = widget.evidence.indexWhere((item) => item.id == selectedId);
    final next = index >= 0 ? index : 0;
    if (next != _page) {
      _page = next;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _pageController.hasClients) {
          _pageController.jumpToPage(_page);
        }
      });
    }
  }

  Future<void> _openGallery(int initialIndex) async {
    final photos = List<EvidenceRecord>.unmodifiable(widget.evidence);
    var selected = initialIndex;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => WeaponPhotoGallery(
          evidence: photos,
          initialIndex: initialIndex,
          onPageChanged: (index) => selected = index,
        ),
      ),
    );
    if (!mounted) return;
    final index = widget.evidence.indexWhere(
      (item) => item.id == photos[selected].id,
    );
    if (index >= 0 && _pageController.hasClients) {
      _pageController.jumpToPage(index);
      setState(() => _page = index);
    }
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final evidence = widget.evidence;
    final item = evidence[_page];
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Card(
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(
              height: 220,
              child: PageView.builder(
                controller: _pageController,
                itemCount: evidence.length,
                onPageChanged: (index) => setState(() => _page = index),
                itemBuilder: (context, index) {
                  final photo = evidence[index];
                  final image = photo.url == null
                      ? const Center(
                          child: Icon(
                            Icons.image_outlined,
                            color: Colors.white70,
                            size: 52,
                          ),
                        )
                      : Image.network(
                          photo.url!,
                          headers: photo.httpHeaders,
                          fit: BoxFit.contain,
                          errorBuilder: (_, _, _) => const Center(
                            child: Text(
                              'Unable to load evidence.',
                              style: TextStyle(color: Colors.white),
                            ),
                          ),
                        );
                  return ColoredBox(
                    color: const Color(0xFF132733),
                    child: InkWell(
                      onTap: photo.url == null
                          ? null
                          : () => _openGallery(index),
                      child: SizedBox.expand(child: image),
                    ),
                  );
                },
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 11, 14, 13),
              child: Row(
                children: [
                  Icon(Icons.image_outlined, color: widget.accent),
                  const SizedBox(width: 9),
                  Expanded(
                    child: Text(
                      item.startSeconds == null
                          ? 'Weapon evidence image'
                          : 'Weapon evidence · ${item.startSeconds!.toStringAsFixed(1)}s',
                      style: const TextStyle(fontWeight: FontWeight.w800),
                    ),
                  ),
                  Text(
                    '${_page + 1} / ${evidence.length}',
                    style: const TextStyle(color: GuardAidColors.muted),
                  ),
                ],
              ),
            ),
            if (evidence.length > 1)
              Padding(
                padding: const EdgeInsets.fromLTRB(14, 0, 14, 13),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(5),
                  child: LinearProgressIndicator(
                    value: (_page + 1) / evidence.length,
                    minHeight: 3,
                    color: widget.accent,
                    backgroundColor: const Color(0xFFE6E9ED),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _EvidenceViewerState extends State<EvidenceViewer> {
  VideoPlayerController? videoController;
  Object? loadError;

  @override
  void initState() {
    super.initState();
    final url = widget.evidence.url;
    if (widget.evidence.isVideo && url != null) {
      final controller = VideoPlayerController.networkUrl(
        Uri.parse(url),
        httpHeaders: widget.evidence.httpHeaders,
      );
      videoController = controller;
      controller
          .initialize()
          .then((_) {
            if (mounted) setState(() {});
          })
          .catchError((Object error) {
            if (mounted) setState(() => loadError = error);
          });
    }
  }

  @override
  void dispose() {
    videoController?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final url = widget.evidence.url;
    return Semantics(
      label: widget.evidence.isVideo
          ? 'Incident evidence video'
          : 'Weapon evidence image',
      hint: url == null
          ? 'Demo evidence is not connected to a media file'
          : 'Evidence media preview',
      child: Card(
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: ColoredBox(color: Colors.black, child: _media(url)),
            ),
            Padding(
              padding: const EdgeInsets.all(13),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(
                        widget.evidence.isVideo
                            ? Icons.movie_outlined
                            : Icons.image_outlined,
                        color: widget.accent,
                      ),
                      const SizedBox(width: 9),
                      Expanded(
                        child: Text(
                          widget.evidence.isVideo
                              ? 'Incident evidence clip'
                              : 'Weapon evidence image',
                          style: const TextStyle(fontWeight: FontWeight.w800),
                        ),
                      ),
                      if (widget.evidence.startSeconds != null)
                        Flexible(
                          child: Text(
                            '${widget.evidence.startSeconds!.toStringAsFixed(1)}–${widget.evidence.endSeconds?.toStringAsFixed(1) ?? '?'}s',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            textAlign: TextAlign.end,
                            style: const TextStyle(color: GuardAidColors.muted),
                          ),
                        ),
                    ],
                  ),
                  if (url == null)
                    const Padding(
                      padding: EdgeInsets.only(top: 8),
                      child: Text(
                        'Demo evidence preview only. A connected evidence URL is required for playback.',
                        style: TextStyle(
                          color: GuardAidColors.muted,
                          fontSize: 12,
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _media(String? url) {
    if (url == null) {
      return Center(
        child: FittedBox(
          fit: BoxFit.scaleDown,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                widget.evidence.isVideo
                    ? Icons.play_circle_outline_rounded
                    : Icons.image_outlined,
                color: Colors.white70,
                size: 52,
              ),
              const SizedBox(height: 8),
              const Text(
                'Demo evidence preview',
                style: TextStyle(color: Colors.white70),
              ),
              const SizedBox(height: 4),
              const Text(
                'Playback becomes available when evidence is connected.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Colors.white54, fontSize: 11),
              ),
            ],
          ),
        ),
      );
    }
    if (!widget.evidence.isVideo) {
      return Image.network(
        url,
        headers: widget.evidence.httpHeaders,
        fit: BoxFit.contain,
        errorBuilder: (_, _, _) => const Center(
          child: Text(
            'Unable to load evidence.',
            style: TextStyle(color: Colors.white),
          ),
        ),
      );
    }
    final controller = videoController;
    if (loadError != null) {
      return const Center(
        child: Text(
          'Unable to load evidence video.',
          style: TextStyle(color: Colors.white),
        ),
      );
    }
    if (controller == null || !controller.value.isInitialized) {
      return const Center(
        child: CircularProgressIndicator(color: Colors.white),
      );
    }
    final videoAspectRatio = _safeVideoAspectRatio(
      controller.value.aspectRatio,
    );
    return Stack(
      children: [
        Positioned.fill(
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () => _openFullscreenVideo(controller),
            child: Center(
              child: AspectRatio(
                aspectRatio: videoAspectRatio,
                child: VideoPlayer(controller),
              ),
            ),
          ),
        ),
        Center(
          child: ValueListenableBuilder<VideoPlayerValue>(
            valueListenable: controller,
            builder: (context, value, _) => IconButton.filledTonal(
              tooltip: value.isPlaying ? 'Pause video' : 'Play video',
              iconSize: 32,
              onPressed: () =>
                  value.isPlaying ? controller.pause() : controller.play(),
              icon: Icon(
                value.isPlaying
                    ? Icons.pause_rounded
                    : Icons.play_arrow_rounded,
              ),
            ),
          ),
        ),
        Positioned(
          top: 8,
          right: 8,
          child: IconButton.filledTonal(
            tooltip: 'Expand incident video',
            onPressed: () => _openFullscreenVideo(controller),
            icon: const Icon(Icons.fullscreen_rounded),
          ),
        ),
      ],
    );
  }

  Future<void> _openFullscreenVideo(VideoPlayerController controller) async {
    if (!controller.value.isInitialized) return;
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (_) => _EvidenceVideoFullscreen(controller: controller),
      ),
    );
    if (mounted) setState(() {});
  }
}

class _EvidenceVideoFullscreen extends StatelessWidget {
  const _EvidenceVideoFullscreen({required this.controller});

  final VideoPlayerController controller;

  @override
  Widget build(BuildContext context) {
    final aspectRatio = _safeVideoAspectRatio(controller.value.aspectRatio);
    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Stack(
          fit: StackFit.expand,
          children: [
            Center(
              child: AspectRatio(
                aspectRatio: aspectRatio,
                child: VideoPlayer(controller),
              ),
            ),
            Positioned(
              top: 0,
              left: 0,
              right: 0,
              child: DecoratedBox(
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Colors.black87, Colors.transparent],
                  ),
                ),
                child: SizedBox(
                  height: 56,
                  child: Row(
                    children: [
                      IconButton(
                        tooltip: 'Close video',
                        color: Colors.white,
                        onPressed: () => Navigator.of(context).pop(),
                        icon: const Icon(Icons.arrow_back_rounded),
                      ),
                      const Expanded(
                        child: Text(
                          'Incident video',
                          style: TextStyle(
                            color: Colors.white,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              child: ValueListenableBuilder<VideoPlayerValue>(
                valueListenable: controller,
                builder: (context, value, _) {
                  final durationMs = value.duration.inMilliseconds.toDouble();
                  final sliderMax = durationMs > 0 ? durationMs : 1.0;
                  final positionMs = value.position.inMilliseconds
                      .toDouble()
                      .clamp(0.0, sliderMax)
                      .toDouble();
                  return DecoratedBox(
                    decoration: const BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.bottomCenter,
                        end: Alignment.topCenter,
                        colors: [Colors.black87, Colors.transparent],
                      ),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(12, 26, 12, 8),
                      child: Row(
                        children: [
                          IconButton(
                            tooltip: value.isPlaying
                                ? 'Pause video'
                                : 'Play video',
                            color: Colors.white,
                            onPressed: () => value.isPlaying
                                ? controller.pause()
                                : controller.play(),
                            icon: Icon(
                              value.isPlaying
                                  ? Icons.pause_rounded
                                  : Icons.play_arrow_rounded,
                            ),
                          ),
                          Text(
                            _formatVideoDuration(value.position),
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 12,
                            ),
                          ),
                          Expanded(
                            child: Slider(
                              value: positionMs,
                              max: sliderMax,
                              activeColor: Colors.white,
                              inactiveColor: Colors.white30,
                              onChanged: durationMs <= 0
                                  ? null
                                  : (position) => controller.seekTo(
                                      Duration(milliseconds: position.round()),
                                    ),
                            ),
                          ),
                          Text(
                            _formatVideoDuration(value.duration),
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 12,
                            ),
                          ),
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

double _safeVideoAspectRatio(double aspectRatio) =>
    aspectRatio.isFinite && aspectRatio > 0 ? aspectRatio : 16 / 9;

String _formatVideoDuration(Duration duration) {
  final seconds = duration.inSeconds < 0 ? 0 : duration.inSeconds;
  final hours = seconds ~/ 3600;
  final minutes = (seconds % 3600) ~/ 60;
  final remainder = seconds % 60;
  final mm = minutes.toString().padLeft(2, '0');
  final ss = remainder.toString().padLeft(2, '0');
  return hours > 0 ? '$hours:$mm:$ss' : '$mm:$ss';
}
