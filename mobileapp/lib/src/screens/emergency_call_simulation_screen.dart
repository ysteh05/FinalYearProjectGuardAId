import 'dart:async';

import 'package:flutter/material.dart';

class EmergencyCallSimulationScreen extends StatefulWidget {
  const EmergencyCallSimulationScreen({
    super.key,
    required this.location,
    required this.cameraName,
  });

  final String location;
  final String cameraName;

  @override
  State<EmergencyCallSimulationScreen> createState() =>
      _EmergencyCallSimulationScreenState();
}

class _EmergencyCallSimulationScreenState
    extends State<EmergencyCallSimulationScreen> {
  Timer? _timer;
  int _elapsedSeconds = 0;
  bool _muted = false;
  bool _speakerOn = false;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() => _elapsedSeconds++);
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  String get _elapsedLabel {
    final minutes = (_elapsedSeconds ~/ 60).toString().padLeft(2, '0');
    final seconds = (_elapsedSeconds % 60).toString().padLeft(2, '0');
    return '$minutes:$seconds';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF15251F),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 20),
          child: Column(
            children: [
              const Align(
                alignment: Alignment.centerLeft,
                child: Icon(Icons.lock_outline_rounded, color: Colors.white70),
              ),
              const Spacer(flex: 2),
              Container(
                width: 112,
                height: 112,
                decoration: const BoxDecoration(
                  color: Color(0xFF31463D),
                  shape: BoxShape.circle,
                ),
                child: const Icon(
                  Icons.local_police_outlined,
                  color: Colors.white,
                  size: 58,
                ),
              ),
              const SizedBox(height: 24),
              const Text(
                'Emergency services',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 27,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                widget.location.isEmpty ? widget.cameraName : widget.location,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70, fontSize: 15),
              ),
              const SizedBox(height: 18),
              const Text(
                'SIMULATED CALL · NO REAL CALL IS BEING MADE',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Color(0xFFB9D9C4),
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 1,
                ),
              ),
              const SizedBox(height: 20),
              Semantics(
                label: 'Call duration $_elapsedLabel',
                child: Text(
                  _elapsedLabel,
                  style: const TextStyle(color: Colors.white70, fontSize: 18),
                ),
              ),
              const Spacer(flex: 2),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  _CallControl(
                    key: const ValueKey('call-mute-control'),
                    icon: _muted
                        ? Icons.mic_off_rounded
                        : Icons.mic_none_rounded,
                    label: _muted ? 'Unmute' : 'Mute',
                    selected: _muted,
                    onTap: () => setState(() => _muted = !_muted),
                  ),
                  _CallControl(
                    key: const ValueKey('call-speaker-control'),
                    icon: _speakerOn
                        ? Icons.volume_up_rounded
                        : Icons.volume_down_rounded,
                    label: _speakerOn ? 'Speaker on' : 'Speaker',
                    selected: _speakerOn,
                    onTap: () => setState(() => _speakerOn = !_speakerOn),
                  ),
                ],
              ),
              const Spacer(),
              Semantics(
                button: true,
                label: 'End simulated call',
                child: SizedBox.square(
                  dimension: 84,
                  child: FilledButton(
                    key: const ValueKey('end-simulated-call'),
                    style: FilledButton.styleFrom(
                      padding: EdgeInsets.zero,
                      backgroundColor: const Color(0xFFD33F45),
                      foregroundColor: Colors.white,
                      fixedSize: const Size(84, 84),
                      shape: const CircleBorder(),
                    ),
                    onPressed: () => Navigator.of(context).pop(),
                    child: const Icon(Icons.call_end_rounded, size: 30),
                  ),
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                'End call',
                style: TextStyle(color: Colors.white70, fontSize: 14),
              ),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }
}

class _CallControl extends StatelessWidget {
  const _CallControl({
    super.key,
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconButton.filledTonal(
          tooltip: label,
          onPressed: onTap,
          style: IconButton.styleFrom(
            backgroundColor: selected
                ? const Color(0xFFB9D9C4)
                : const Color(0xFF34463E),
            foregroundColor: selected ? const Color(0xFF15251F) : Colors.white,
            minimumSize: const Size(62, 62),
          ),
          icon: Icon(icon),
        ),
        const SizedBox(height: 8),
        Text(label, style: const TextStyle(color: Colors.white70)),
      ],
    );
  }
}
