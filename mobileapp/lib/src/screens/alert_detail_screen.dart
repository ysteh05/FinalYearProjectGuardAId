import 'dart:async';

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';

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
    super.dispose();
  }

  GuardAlert get alert =>
      widget.controller.alerts.firstWhere((item) => item.id == widget.alertId);

  String _reviewStatusLabel(String status) => switch (status) {
    'queued' => 'Review queued',
    'running' => 'Review in progress',
    'positive' => 'VLM confirmed',
    'negative' => 'VLM did not confirm',
    'failed' => 'Review failed',
    'disabled' => 'Review disabled',
    'cancelled' => 'Review cancelled',
    'legacy_unknown' => 'Historical review status unavailable',
    _ => 'No Borderline review required',
  };

  String _notificationStatusLabel(String status) => switch (status) {
    'provider_accepted' => 'FCM accepted the initial notification request',
    'pending' => 'Initial notification pending',
    'eligible' => 'Eligible for an initial notification',
    'not_sent' => 'Initial notification was not sent',
    _ => 'No historical first-notification record',
  };

  String _qualificationLabel(AlertDetectionEvent event) {
    if (event.qualifications.isEmpty) {
      return 'Qualification details unavailable';
    }
    final violence = event.qualifications['violence'];
    if (violence != null) {
      if (violence.isEmpty) return 'No role notification tier confirmed';
      return 'Violence tiers: ${violence.map(_tierLabel).join(', ')}';
    }
    final profiles = event.qualifications.entries
        .where((entry) => entry.key != 'weapon')
        .map(
          (entry) =>
              '${_tierLabel(entry.key)}${entry.value.isEmpty ? '' : ' · ${entry.value.map(_titleLabel).join(', ')}'}',
        )
        .toList(growable: false);
    return profiles.isEmpty
        ? 'Weapon qualification details unavailable'
        : profiles.join('  |  ');
  }

  String _tierLabel(String value) => switch (value.toLowerCase()) {
    'sensitive' => 'Sensitive',
    'moderate' => 'Moderate',
    'certain' => 'Certain',
    _ => _titleLabel(value),
  };

  String _titleLabel(String value) =>
      value.isEmpty ? value : '${value[0].toUpperCase()}${value.substring(1)}';

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
    var choice = 'confirm_only';
    var simulateCall = false;
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
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: simulateCall,
                    onChanged: (value) =>
                        setModalState(() => simulateCall = value ?? false),
                    title: const Text('Simulate emergency call'),
                    subtitle: const Text(
                      'No dialler opens and no real call is made.',
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
                          simulateCall,
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
      simulateCall: result.simulateCall,
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
    bool simulateCall = false,
  }) async {
    setState(() => submitting = true);
    final submission = await widget.controller.submitWorkflowAction(
      alert.id,
      action,
      comment: comment,
      recipientRoleIds: roleIds,
    );
    final success = submission != null;
    var simulated = true;
    if (success && simulateCall) {
      simulated = await widget.controller.simulateEmergencyCall(alert.id);
    }
    if (!mounted) return;
    setState(() => submitting = false);
    final successMessage = action == 'confirm_only' && submission != null
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
          ? simulated
                ? successMessage
                : 'Decision saved, but the simulated call event could not be recorded.'
          : widget.controller.error ?? 'Unable to update the alert.',
      error: !success || !simulated,
    );
    if (success && simulateCall && mounted) {
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          icon: Icon(
            simulated
                ? Icons.phone_in_talk_rounded
                : Icons.error_outline_rounded,
            color: simulated ? GuardAidColors.success : GuardAidColors.weapon,
            size: 42,
          ),
          title: Text(simulated ? 'Simulation complete' : 'Simulation failed'),
          content: Text(
            simulated
                ? 'Simulated emergency call has been placed. No real call was made.'
                : widget.controller.error ??
                      'Unable to record the simulated call.',
            textAlign: TextAlign.center,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Close'),
            ),
          ],
        ),
      );
    }
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
    final confirmed = await showConfirmDialog(
      context,
      title: 'Simulate emergency call?',
      message:
          'This prototype will not open the phone dialler or contact any emergency service. It will only record a simulated action.',
      confirmLabel: 'Run simulation',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => submitting = true);
    final success = await widget.controller.simulateEmergencyCall(alert.id);
    if (!mounted) return;
    setState(() => submitting = false);
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        icon: Icon(
          success ? Icons.phone_in_talk_rounded : Icons.error_outline_rounded,
          color: success ? GuardAidColors.success : GuardAidColors.weapon,
          size: 42,
        ),
        title: Text(success ? 'Simulation complete' : 'Simulation failed'),
        content: Text(
          success
              ? 'Simulated emergency call has been placed. No real call was made.'
              : widget.controller.error ??
                    'Unable to record the simulated call.',
          textAlign: TextAlign.center,
        ),
        actions: [
          FilledButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Done'),
          ),
        ],
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
    final decisions = current.actions
        .where(
          (item) =>
              item.action != 'simulated_emergency_call' &&
              item.action != 'acknowledge' &&
              item.action != 'review',
        )
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
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          current.type.label,
                          style: const TextStyle(
                            fontSize: 23,
                            fontWeight: FontWeight.w900,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Align(
                    alignment: Alignment.centerRight,
                    child: StatusBadge(
                      label: current.workflowStatus.label,
                      color: alertStatusColor(current.status),
                    ),
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
                    label: 'AI confidence',
                    value: '${(current.confidence * 100).toStringAsFixed(1)}%',
                  ),
                  if (current.isProvisional &&
                      (current.borderlineReviewStatus == 'queued' ||
                          current.borderlineReviewStatus == 'running'))
                    const Padding(
                      padding: EdgeInsets.only(top: 8),
                      child: StatusBadge(
                        label: 'Provisional · VLM review pending',
                        color: GuardAidColors.violence,
                        icon: Icons.auto_awesome_rounded,
                      ),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 14),
          const SectionTitle(
            'Detection and notification evidence',
            subtitle:
                'Detection decisions are separate from push delivery. FCM acceptance does not confirm that a phone displayed the notification or played sound.',
          ),
          const SizedBox(height: 10),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          'Borderline Review · ${_reviewStatusLabel(current.borderlineReviewStatus)}',
                          style: const TextStyle(fontWeight: FontWeight.w800),
                        ),
                      ),
                    ],
                  ),
                  if (current.borderlineReviewAvailability
                      case final availability?) ...[
                    const SizedBox(height: 6),
                    Text(
                      availability.available
                          ? availability.enabledForAccount
                                ? 'Borderline Review is enabled for your account.'
                                : 'Borderline Review is available but not enabled for your account.'
                          : 'Borderline Review is unavailable. Ask an administrator to enable it in Web Dashboard Config; your account preference is retained.',
                      style: const TextStyle(color: GuardAidColors.muted),
                    ),
                  ],
                  if (current.notificationEligibility
                      case final eligibility?) ...[
                    const SizedBox(height: 12),
                    Text(
                      _notificationStatusLabel(eligibility.status),
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    if (eligibility.qualificationReasons.isNotEmpty)
                      Text(
                        'Reason: ${eligibility.qualificationReasons.map(_titleLabel).join(', ')}',
                        style: const TextStyle(color: GuardAidColors.muted),
                      ),
                    if (eligibility.note != null)
                      Text(
                        eligibility.note!,
                        style: const TextStyle(color: GuardAidColors.muted),
                      ),
                  ],
                  const SizedBox(height: 14),
                  const Text(
                    'Recent detector decisions',
                    style: TextStyle(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 6),
                  if (current.detectionEvents.isEmpty)
                    const Text(
                      'Historical detection detail is unavailable for this alert.',
                      style: TextStyle(color: GuardAidColors.muted),
                    )
                  else
                    ...current.detectionEvents.reversed
                        .take(5)
                        .map(
                          (event) => Padding(
                            padding: const EdgeInsets.only(bottom: 8),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  '${(event.confidence * 100).toStringAsFixed(1)}% · ${event.decisionSource.replaceAll('_', ' ')}',
                                  style: const TextStyle(
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                                Text(
                                  _qualificationLabel(event),
                                  style: const TextStyle(
                                    color: GuardAidColors.muted,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                  if (current.detectionEvents.length > 5)
                    Text(
                      '${current.detectionEvents.length - 5} earlier detector decisions are available in the audit record.',
                      style: const TextStyle(color: GuardAidColors.muted),
                    ),
                  if (current.borderlineReviewTasks.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    const Text(
                      'Review windows',
                      style: TextStyle(fontWeight: FontWeight.w800),
                    ),
                    ...current.borderlineReviewTasks.map(
                      (task) => Padding(
                        padding: const EdgeInsets.only(top: 6),
                        child: Text(
                          '${task.sourceStartSeconds.toStringAsFixed(1)}–${task.sourceEndSeconds.toStringAsFixed(1)}s · ${_reviewStatusLabel(task.status)}${task.briefReason == null ? '' : ' · ${task.briefReason}'}${task.errorCode == null ? '' : ' · ${task.errorCode}'}',
                          style: const TextStyle(color: GuardAidColors.muted),
                        ),
                      ),
                    ),
                  ],
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
            subtitle: 'Generated by Qwen3-VL 2B for human decision support.',
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
          if (canAct) ...[
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
  const _ConfirmResult(
    this.action,
    this.comment,
    this.roleIds,
    this.simulateCall,
  );

  final String action;
  final String comment;
  final List<String> roleIds;
  final bool simulateCall;
}

class _NoDecisionSummary extends StatelessWidget {
  const _NoDecisionSummary();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: GuardAidColors.canvas,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: GuardAidColors.border),
      ),
      child: const Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.pending_actions_rounded, color: GuardAidColors.muted),
          SizedBox(width: 10),
          Expanded(
            child: Text(
              'No decision recorded yet',
              style: TextStyle(fontWeight: FontWeight.w800),
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
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            '${latest.actionLabel} by ${latest.performedBy}',
            style: const TextStyle(fontWeight: FontWeight.w900),
          ),
          if (latest.createdAt != null) ...[
            const SizedBox(height: 3),
            Text(
              formatDateTime(latest.createdAt!),
              style: const TextStyle(color: GuardAidColors.muted, fontSize: 12),
            ),
          ],
          if (latest.note?.trim().isNotEmpty == true) ...[
            const SizedBox(height: 8),
            Text(latest.note!, style: const TextStyle(height: 1.4)),
          ],
          if (actions.length > 1) ...[
            const Divider(height: 24),
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              childrenPadding: EdgeInsets.zero,
              title: Text(
                'Decision history (${actions.length})',
                style: const TextStyle(fontWeight: FontWeight.w800),
              ),
              children: [
                for (final action in actions.skip(1))
                  _DecisionHistoryRow(action: action),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _DecisionHistoryRow extends StatelessWidget {
  const _DecisionHistoryRow({required this.action});

  final AlertActionRecord action;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.history_rounded,
            size: 17,
            color: GuardAidColors.muted,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${action.actionLabel} by ${action.performedBy}',
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                if (action.createdAt != null)
                  Text(
                    formatDateTime(action.createdAt!),
                    style: const TextStyle(
                      color: GuardAidColors.muted,
                      fontSize: 12,
                    ),
                  ),
                if (action.note?.trim().isNotEmpty == true)
                  Text(action.note!, style: const TextStyle(height: 1.35)),
              ],
            ),
          ),
        ],
      ),
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
      child: Row(
        children: [
          Icon(icon, size: 19, color: GuardAidColors.muted),
          const SizedBox(width: 9),
          SizedBox(
            width: 126,
            child: Text(
              '$label:',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: GuardAidColors.muted),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
          ),
        ],
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
      'Evidence capture or storage failed. The detector alert is still saved; review the available decision details.',
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
                          : () => Navigator.of(context).push<void>(
                              MaterialPageRoute<void>(
                                builder: (_) =>
                                    _WeaponPhotoFullscreen(evidence: photo),
                              ),
                            ),
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

class _WeaponPhotoFullscreen extends StatelessWidget {
  const _WeaponPhotoFullscreen({required this.evidence});

  final EvidenceRecord evidence;

  @override
  Widget build(BuildContext context) {
    final url = evidence.url;
    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Stack(
          children: [
            Positioned.fill(
              child: LayoutBuilder(
                builder: (context, constraints) => InteractiveViewer(
                  minScale: 1,
                  maxScale: 6,
                  boundaryMargin: const EdgeInsets.all(80),
                  child: SizedBox(
                    width: constraints.maxWidth,
                    height: constraints.maxHeight,
                    child: url == null
                        ? const Center(
                            child: Icon(
                              Icons.image_outlined,
                              color: Colors.white70,
                              size: 64,
                            ),
                          )
                        : Image.network(
                            url,
                            headers: evidence.httpHeaders,
                            fit: BoxFit.contain,
                            errorBuilder: (_, _, _) => const Center(
                              child: Text(
                                'Unable to load evidence.',
                                style: TextStyle(color: Colors.white),
                              ),
                            ),
                          ),
                  ),
                ),
              ),
            ),
            Positioned(
              top: 4,
              left: 4,
              child: IconButton.filledTonal(
                tooltip: 'Close image',
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.close_rounded),
              ),
            ),
            Positioned(
              left: 16,
              right: 16,
              bottom: 12,
              child: Text(
                evidence.startSeconds == null
                    ? 'Pinch to zoom · drag to inspect'
                    : 'Source ${evidence.startSeconds!.toStringAsFixed(1)}s · Pinch to zoom · drag to inspect',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  color: Colors.white70,
                  shadows: [Shadow(blurRadius: 8, color: Colors.black)],
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
    final controller = videoController;
    final nativeAspectRatio = controller?.value.isInitialized == true
        ? controller!.value.aspectRatio
        : 16 / 9;
    final aspectRatio = nativeAspectRatio.isFinite && nativeAspectRatio > 0
        ? nativeAspectRatio
        : 16 / 9;
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
              aspectRatio: widget.evidence.isVideo ? aspectRatio : 16 / 9,
              child: ColoredBox(
                color: const Color(0xFF132733),
                child: _media(url),
              ),
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
    return Stack(
      alignment: Alignment.center,
      children: [
        VideoPlayer(controller),
        IconButton.filledTonal(
          iconSize: 32,
          onPressed: () {
            setState(() {
              controller.value.isPlaying
                  ? controller.pause()
                  : controller.play();
            });
          },
          icon: Icon(
            controller.value.isPlaying
                ? Icons.pause_rounded
                : Icons.play_arrow_rounded,
          ),
        ),
      ],
    );
  }
}
