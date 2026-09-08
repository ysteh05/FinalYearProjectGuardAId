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
  GuardAlert get alert =>
      widget.controller.alerts.firstWhere((item) => item.id == widget.alertId);

  Future<void> _showActionForm(AlertStatus targetStatus) async {
    final reasonController = TextEditingController();
    final isEscalation = targetStatus == AlertStatus.escalated;
    final selectedRecipients = <String>{'role_security_personnel'};
    final result = await showModalBottomSheet<_ActionResult>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => StatefulBuilder(
        builder: (context, setModalState) => Padding(
          padding: EdgeInsets.fromLTRB(
            20,
            4,
            20,
            20 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  switch (targetStatus) {
                    AlertStatus.dismissed => 'Dismiss false alarm',
                    AlertStatus.resolved => 'Resolve incident',
                    AlertStatus.escalated => 'Escalate emergency',
                    _ => 'Update alert',
                  },
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w900,
                  ),
                ),
                const SizedBox(height: 8),
                Text(
                  switch (targetStatus) {
                    AlertStatus.dismissed =>
                      'Explain why the AI alert is not a safety incident.',
                    AlertStatus.resolved =>
                      'Record how the incident was resolved for the saved log.',
                    AlertStatus.escalated =>
                      'Select authorised recipient groups and add a clear response message.',
                    _ => '',
                  },
                  style: const TextStyle(
                    color: GuardAidColors.muted,
                    height: 1.4,
                  ),
                ),
                if (isEscalation) ...[
                  const SizedBox(height: 18),
                  const Text(
                    'Broadcast to',
                    style: TextStyle(fontWeight: FontWeight.w800),
                  ),
                  const SizedBox(height: 6),
                  ...widget.controller.roles.map(
                    (role) => CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      title: Text(role.name),
                      value: selectedRecipients.contains(role.id),
                      onChanged: (selected) => setModalState(() {
                        if (selected == true) {
                          selectedRecipients.add(role.id);
                        } else {
                          selectedRecipients.remove(role.id);
                        }
                      }),
                    ),
                  ),
                ],
                const SizedBox(height: 14),
                TextField(
                  controller: reasonController,
                  minLines: 3,
                  maxLines: 5,
                  decoration: InputDecoration(
                    labelText: isEscalation
                        ? 'Escalation message'
                        : 'Reason and comments',
                    alignLabelWithHint: true,
                  ),
                ),
                const SizedBox(height: 18),
                FilledButton(
                  style: isEscalation
                      ? FilledButton.styleFrom(
                          backgroundColor: GuardAidColors.weapon,
                        )
                      : null,
                  onPressed: () {
                    final reason = reasonController.text.trim();
                    if (reason.isEmpty) {
                      showMessage(
                        context,
                        'Enter a reason or response message.',
                        error: true,
                      );
                      return;
                    }
                    if (isEscalation && selectedRecipients.isEmpty) {
                      showMessage(
                        context,
                        'Select at least one recipient group.',
                        error: true,
                      );
                      return;
                    }
                    Navigator.pop(
                      context,
                      _ActionResult(reason, selectedRecipients.toList()),
                    );
                  },
                  child: Text(
                    isEscalation ? 'Send escalation' : 'Confirm action',
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (result == null || !mounted) return;
    final success = await widget.controller.submitAlertAction(
      alert.id,
      targetStatus,
      result.reason,
      recipientRoleIds: result.recipientRoleIds,
    );
    if (!mounted) return;
    setState(() {});
    showMessage(
      context,
      success
          ? switch (targetStatus) {
              AlertStatus.dismissed =>
                'Alert dismissed and saved to the incident log.',
              AlertStatus.resolved =>
                'Incident resolved and relevant personnel notified.',
              AlertStatus.escalated =>
                'Emergency escalation sent to the selected groups.',
              _ => 'Alert updated.',
            }
          : widget.controller.error ?? 'Unable to update the alert.',
      error: !success,
    );
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
    final success = await widget.controller.simulateEmergencyCall(alert.id);
    if (!mounted) return;
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
    final canAct =
        current.status == AlertStatus.open ||
        current.status == AlertStatus.inReview;
    return Scaffold(
      appBar: AppBar(title: const Text('Alert review')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
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
                      label: current.status.label,
                      color: alertStatusColor(current.status),
                    ),
                  ),
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
                  _DetailRow(
                    icon: Icons.tune_rounded,
                    label: 'Threshold mode',
                    value: current.sensitivity.label,
                  ),
                  if (current.isProvisional)
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
          const SectionTitle('Evidence'),
          const SizedBox(height: 10),
          if (current.evidence.isEmpty)
            const _EvidenceUnavailable()
          else
            ...current.evidence.map(
              (item) => Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: EvidenceViewer(evidence: item, accent: color),
              ),
            ),
          const SizedBox(height: 8),
          const SectionTitle(
            'AI scene summary',
            subtitle: 'Generated by Qwen3-VL 2B for human decision support.',
          ),
          const SizedBox(height: 10),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: current.descriptionStatus == 'pending'
                  ? const Row(
                      children: [
                        SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                        SizedBox(width: 12),
                        Expanded(
                          child: Text(
                            'Scene description is being prepared. The alert remains available now.',
                          ),
                        ),
                      ],
                    )
                  : Text(
                      current.sceneDescription ??
                          'No scene description is available.',
                      style: const TextStyle(height: 1.55),
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
          if (current.actionNote != null) ...[
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
            OutlinedButton.icon(
              onPressed: () => _showActionForm(AlertStatus.dismissed),
              icon: const Icon(Icons.cancel_outlined),
              label: const Text('Dismiss as false alarm'),
            ),
            const SizedBox(height: 10),
            FilledButton.icon(
              style: FilledButton.styleFrom(
                backgroundColor: GuardAidColors.success,
              ),
              onPressed: () => _showActionForm(AlertStatus.resolved),
              icon: const Icon(Icons.check_circle_outline_rounded),
              label: const Text('Resolve and notify school'),
            ),
            const SizedBox(height: 10),
            FilledButton.icon(
              style: FilledButton.styleFrom(
                backgroundColor: GuardAidColors.weapon,
              ),
              onPressed: () => _showActionForm(AlertStatus.escalated),
              icon: const Icon(Icons.emergency_share_rounded),
              label: const Text('Escalate as emergency'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              style: OutlinedButton.styleFrom(
                foregroundColor: GuardAidColors.weapon,
              ),
              onPressed: _simulateEmergencyCall,
              icon: const Icon(Icons.phone_in_talk_outlined),
              label: const Text('Simulate emergency call'),
            ),
          ],
        ],
      ),
    );
  }
}

class _ActionResult {
  const _ActionResult(this.reason, this.recipientRoleIds);

  final String reason;
  final List<String> recipientRoleIds;
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
            width: 105,
            child: Text(
              label,
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
  const _EvidenceUnavailable();

  @override
  Widget build(BuildContext context) {
    return const Card(
      child: Padding(
        padding: EdgeInsets.all(22),
        child: Row(
          children: [
            Icon(Icons.hide_image_outlined, color: GuardAidColors.muted),
            SizedBox(width: 12),
            Expanded(
              child: Text(
                'No evidence media is available for this saved record.',
              ),
            ),
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
    return Card(
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          AspectRatio(
            aspectRatio: 16 / 9,
            child: ColoredBox(
              color: const Color(0xFF132733),
              child: _media(url),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(13),
            child: Row(
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
                  Text(
                    '${widget.evidence.startSeconds!.toStringAsFixed(1)}–${widget.evidence.endSeconds?.toStringAsFixed(1) ?? '?'}s',
                    style: const TextStyle(color: GuardAidColors.muted),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _media(String? url) {
    if (url == null) {
      return Center(
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
          ],
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
