import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../controller.dart';
import '../theme.dart';
import '../widgets.dart';
import 'alerts_screen.dart';
import 'alert_detail_screen.dart';
import 'logs_screen.dart';
import 'settings_screen.dart';
import 'updates_screen.dart';

class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int selectedIndex = 0;
  bool openingNotification = false;

  @override
  Widget build(BuildContext context) {
    _openRequestedAlertIfNeeded();
    final pages = [
      AlertsScreen(controller: widget.controller),
      UpdatesScreen(controller: widget.controller),
      LogsScreen(controller: widget.controller),
      SettingsScreen(controller: widget.controller),
    ];
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _confirmExit();
      },
      child: Scaffold(
        appBar: AppBar(
        title: const GuardAidBrand(compact: true),
        actions: [
          if (widget.controller.unreadCount > 0)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: Badge(
                label: Text('${widget.controller.unreadCount}'),
                child: IconButton(
                  tooltip: 'Notifications',
                  onPressed: () => setState(() => selectedIndex = 1),
                  icon: const Icon(Icons.notifications_none_rounded),
                ),
              ),
            ),
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: CircleAvatar(
              radius: 18,
              backgroundColor: GuardAidColors.navy,
              child: Text(
                _initials(widget.controller.currentUser?.displayName ?? 'User'),
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 12,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
          ),
        ],
        ),
        body: SafeArea(
          top: false,
          bottom: false,
          child: IndexedStack(index: selectedIndex, children: pages),
        ),
        bottomNavigationBar: Builder(
        builder: (context) {
          final compactLabels =
              MediaQuery.sizeOf(context).width < 360 ||
              MediaQuery.textScalerOf(context).scale(12) > 15;
          return NavigationBar(
            selectedIndex: selectedIndex,
            labelBehavior: compactLabels
                ? NavigationDestinationLabelBehavior.onlyShowSelected
                : NavigationDestinationLabelBehavior.alwaysShow,
            onDestinationSelected: (index) =>
                setState(() => selectedIndex = index),
            destinations: [
              const NavigationDestination(
                icon: Icon(Icons.warning_amber_rounded),
                selectedIcon: Icon(Icons.warning_rounded),
                label: 'Alerts',
              ),
              NavigationDestination(
                icon: Badge(
                  isLabelVisible: widget.controller.unreadCount > 0,
                  label: Text('${widget.controller.unreadCount}'),
                  child: const Icon(Icons.notifications_none_rounded),
                ),
                selectedIcon: const Icon(Icons.notifications_rounded),
                label: 'Updates',
              ),
              const NavigationDestination(
                icon: Icon(Icons.folder_open_outlined),
                selectedIcon: Icon(Icons.folder_rounded),
                label: 'Logs',
              ),
              const NavigationDestination(
                icon: Icon(Icons.settings_outlined),
                selectedIcon: Icon(Icons.settings_rounded),
                label: 'Settings',
              ),
            ],
          );
        },
        ),
      ),
    );
  }

  bool exitDialogOpen = false;

  Future<void> _confirmExit() async {
    if (exitDialogOpen || !mounted) return;
    exitDialogOpen = true;
    final confirmed = await showConfirmDialog(
      context,
      title: 'Leave GuardAId?',
      message: 'You will stop viewing the current school safety workspace.',
      confirmLabel: 'Exit app',
    );
    exitDialogOpen = false;
    if (confirmed && mounted) {
      await SystemNavigator.pop();
    }
  }

  void _openRequestedAlertIfNeeded() {
    final alertId = widget.controller.requestedAlertId;
    if (alertId == null || openingNotification) return;
    final exists = widget.controller.alerts.any((alert) => alert.id == alertId);
    widget.controller.clearRequestedAlert();
    if (!exists) return;
    openingNotification = true;
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => AlertDetailScreen(
            controller: widget.controller,
            alertId: alertId,
          ),
        ),
      );
      openingNotification = false;
    });
  }

  String _initials(String name) {
    final parts = name.trim().split(RegExp(r'\s+'));
    return parts
        .take(2)
        .map((part) => part.isEmpty ? '' : part[0])
        .join()
        .toUpperCase();
  }
}
