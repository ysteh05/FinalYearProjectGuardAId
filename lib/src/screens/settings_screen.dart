import 'package:flutter/material.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  Widget build(BuildContext context) {
    final user = controller.currentUser!;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 24),
      children: [
        const SectionTitle(
          'Settings',
          subtitle: 'Account, alert preferences and administration.',
        ),
        const SizedBox(height: 16),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Row(
              children: [
                const CircleAvatar(
                  radius: 29,
                  backgroundColor: GuardAidColors.navy,
                  child: Icon(
                    Icons.person_rounded,
                    color: Colors.white,
                    size: 30,
                  ),
                ),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        user.displayName,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        user.email,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: GuardAidColors.muted),
                      ),
                      if (user.roleName != null) ...[
                        const SizedBox(height: 8),
                        StatusBadge(
                          label:
                              '${user.roleName} · ${user.sensitivity?.label ?? 'No threshold'}',
                          color: GuardAidColors.info,
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        if (user.isAdmin) ...[
          const SizedBox(height: 22),
          const Text(
            'Administration',
            style: TextStyle(
              fontSize: 14,
              color: GuardAidColors.muted,
              fontWeight: FontWeight.w800,
            ),
          ),
          const SizedBox(height: 9),
          _SettingsTile(
            icon: Icons.admin_panel_settings_outlined,
            title: 'Roles and sensitivity',
            subtitle:
                'Manage role-based Certain, Moderate and Sensitive modes.',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => RoleManagementScreen(controller: controller),
              ),
            ),
          ),
          _SettingsTile(
            icon: Icons.manage_accounts_outlined,
            title: 'User assignments',
            subtitle: 'Approve users, assign roles and disable access.',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => UserAssignmentScreen(controller: controller),
              ),
            ),
          ),
          _SettingsTile(
            icon: Icons.auto_awesome_outlined,
            title: 'Borderline alert settings',
            subtitle:
                'Select which roles receive VLM-assisted borderline alerts.',
            onTap: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) =>
                    BorderlineSettingsScreen(controller: controller),
              ),
            ),
          ),
        ],
        const SizedBox(height: 22),
        const Text(
          'Application',
          style: TextStyle(
            fontSize: 14,
            color: GuardAidColors.muted,
            fontWeight: FontWeight.w800,
          ),
        ),
        const SizedBox(height: 9),
        const _InfoTile(
          icon: Icons.security_rounded,
          title: 'Evidence access',
          subtitle:
              'Evidence remains on the GuardAId server and requires an authorised session.',
        ),
        const _InfoTile(
          icon: Icons.info_outline_rounded,
          title: 'Prototype build',
          subtitle:
              'GuardAId Mobile 1.0.0 · Android application ID com.guardaid.prototype',
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: () async {
            final confirmed = await showConfirmDialog(
              context,
              title: 'Sign out?',
              message:
                  'You will need to sign in again to review GuardAId alerts.',
              confirmLabel: 'Sign out',
            );
            if (confirmed) await controller.signOut();
          },
          icon: const Icon(Icons.logout_rounded),
          label: const Text('Sign out'),
        ),
      ],
    );
  }
}

class _SettingsTile extends StatelessWidget {
  const _SettingsTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Card(
        child: ListTile(
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 7,
          ),
          leading: Icon(icon, color: GuardAidColors.navy),
          title: Text(
            title,
            style: const TextStyle(fontWeight: FontWeight.w800),
          ),
          subtitle: Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              subtitle,
              style: const TextStyle(color: GuardAidColors.muted),
            ),
          ),
          trailing: const Icon(Icons.chevron_right_rounded),
          onTap: onTap,
        ),
      ),
    );
  }
}

class _InfoTile extends StatelessWidget {
  const _InfoTile({
    required this.icon,
    required this.title,
    required this.subtitle,
  });

  final IconData icon;
  final String title;
  final String subtitle;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Card(
        child: ListTile(
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 16,
            vertical: 7,
          ),
          leading: Icon(icon, color: GuardAidColors.muted),
          title: Text(
            title,
            style: const TextStyle(fontWeight: FontWeight.w800),
          ),
          subtitle: Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              subtitle,
              style: const TextStyle(color: GuardAidColors.muted),
            ),
          ),
        ),
      ),
    );
  }
}

class RoleManagementScreen extends StatefulWidget {
  const RoleManagementScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<RoleManagementScreen> createState() => _RoleManagementScreenState();
}

class _RoleManagementScreenState extends State<RoleManagementScreen> {
  Future<void> _editRole(GuardRole role) async {
    var mode = role.sensitivity;
    final selected = await showModalBottomSheet<SensitivityMode>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => StatefulBuilder(
        builder: (context, setModalState) => SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  role.name,
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w900,
                  ),
                ),
                const SizedBox(height: 7),
                const Text(
                  'Choose the alert sensitivity inherited by every user assigned to this role.',
                  style: TextStyle(color: GuardAidColors.muted),
                ),
                const SizedBox(height: 14),
                RadioGroup<SensitivityMode>(
                  groupValue: mode,
                  onChanged: (value) => setModalState(() => mode = value!),
                  child: Column(
                    children: SensitivityMode.values
                        .map(
                          (item) => RadioListTile<SensitivityMode>(
                            contentPadding: EdgeInsets.zero,
                            title: Text(
                              item.label,
                              style: const TextStyle(
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                            subtitle: Text(_sensitivityDescription(item)),
                            value: item,
                          ),
                        )
                        .toList(),
                  ),
                ),
                const SizedBox(height: 10),
                FilledButton(
                  onPressed: () => Navigator.pop(context, mode),
                  child: const Text('Save sensitivity'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (selected == null || !mounted) return;
    final success = await widget.controller.updateRoleSensitivity(
      role.id,
      selected,
    );
    if (!mounted) return;
    setState(() {});
    showMessage(
      context,
      success
          ? '${role.name} now uses ${selected.label} sensitivity.'
          : widget.controller.error ?? 'Unable to update the role.',
      error: !success,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Roles and sensitivity',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          const Text(
            'Sensitivity is stored on the role. Every assigned user inherits the same alert threshold mode.',
            style: TextStyle(color: GuardAidColors.muted, height: 1.45),
          ),
          const SizedBox(height: 16),
          ...widget.controller.roles.map(
            (role) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(17),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              role.name,
                              style: const TextStyle(
                                fontSize: 18,
                                fontWeight: FontWeight.w900,
                              ),
                            ),
                          ),
                          const SizedBox(width: 8),
                          Flexible(
                            child: Align(
                              alignment: Alignment.centerRight,
                              child: StatusBadge(
                                label: role.sensitivity.label,
                                color: GuardAidColors.info,
                                icon: Icons.tune_rounded,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(
                        role.description,
                        style: const TextStyle(
                          color: GuardAidColors.muted,
                          height: 1.4,
                        ),
                      ),
                      const SizedBox(height: 12),
                      Wrap(
                        alignment: WrapAlignment.spaceBetween,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        spacing: 8,
                        runSpacing: 4,
                        children: [
                          Text(
                            '${role.userCount} assigned user${role.userCount == 1 ? '' : 's'}',
                          ),
                          TextButton.icon(
                            onPressed: () => _editRole(role),
                            icon: const Icon(Icons.edit_outlined, size: 18),
                            label: const Text('Edit threshold'),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

String _sensitivityDescription(SensitivityMode mode) => switch (mode) {
  SensitivityMode.sensitive =>
    'Includes lower-confidence and VLM-assisted borderline alerts.',
  SensitivityMode.moderate =>
    'Balances incident recall with unnecessary alert reduction.',
  SensitivityMode.certain =>
    'Prioritises only the highest-confidence detector alerts.',
};

class UserAssignmentScreen extends StatefulWidget {
  const UserAssignmentScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<UserAssignmentScreen> createState() => _UserAssignmentScreenState();
}

class _UserAssignmentScreenState extends State<UserAssignmentScreen> {
  String query = '';

  Future<void> _editUser(AppUser user) async {
    var roleId = user.roleId ?? widget.controller.roles.first.id;
    var status = user.status == UserStatus.disabled
        ? UserStatus.disabled
        : UserStatus.active;
    final result = await showModalBottomSheet<(String, UserStatus)>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (context) => StatefulBuilder(
        builder: (context, setModalState) => SafeArea(
          child: SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  user.displayName,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w900,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  user.email,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: GuardAidColors.muted),
                ),
                const SizedBox(height: 18),
                DropdownButtonFormField<String>(
                  initialValue: roleId,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Assigned role'),
                  items: widget.controller.roles
                      .map(
                        (role) => DropdownMenuItem(
                          value: role.id,
                          child: Text(
                            role.name,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      )
                      .toList(),
                  onChanged: (value) => setModalState(() => roleId = value!),
                ),
                const SizedBox(height: 13),
                DropdownButtonFormField<UserStatus>(
                  initialValue: status,
                  decoration: const InputDecoration(
                    labelText: 'Account access',
                  ),
                  items: const [
                    DropdownMenuItem(
                      value: UserStatus.active,
                      child: Text('Active'),
                    ),
                    DropdownMenuItem(
                      value: UserStatus.disabled,
                      child: Text('Disabled'),
                    ),
                  ],
                  onChanged: (value) => setModalState(() => status = value!),
                ),
                const SizedBox(height: 18),
                FilledButton(
                  onPressed: () => Navigator.pop(context, (roleId, status)),
                  child: const Text('Save assignment'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (result == null || !mounted) return;
    final success = await widget.controller.updateUserAssignment(
      user.id,
      result.$1,
      result.$2,
    );
    if (!mounted) return;
    setState(() {});
    showMessage(
      context,
      success
          ? 'User role and access updated.'
          : widget.controller.error ?? 'Unable to update the user.',
      error: !success,
    );
  }

  @override
  Widget build(BuildContext context) {
    final normalized = query.toLowerCase().trim();
    final users = widget.controller.users.where((user) {
      return normalized.isEmpty ||
          user.displayName.toLowerCase().contains(normalized) ||
          user.email.toLowerCase().contains(normalized) ||
          (user.roleName?.toLowerCase().contains(normalized) ?? false);
    });
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'User assignments',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          const Text(
            'New Firebase identities appear as Pending until an administrator assigns a role and activates access.',
            style: TextStyle(color: GuardAidColors.muted, height: 1.45),
          ),
          const SizedBox(height: 14),
          TextField(
            onChanged: (value) => setState(() => query = value),
            decoration: const InputDecoration(
              hintText: 'Search staff',
              prefixIcon: Icon(Icons.search_rounded),
            ),
          ),
          const SizedBox(height: 14),
          ...users.map(
            (user) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Card(
                child: ListTile(
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 8,
                  ),
                  leading: CircleAvatar(
                    backgroundColor: user.status == UserStatus.pending
                        ? GuardAidColors.violence.withValues(alpha: 0.12)
                        : GuardAidColors.navy.withValues(alpha: 0.1),
                    child: Icon(
                      user.status == UserStatus.pending
                          ? Icons.hourglass_top_rounded
                          : Icons.person_outline_rounded,
                      color: user.status == UserStatus.pending
                          ? GuardAidColors.violence
                          : GuardAidColors.navy,
                    ),
                  ),
                  title: Text(
                    user.displayName,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontWeight: FontWeight.w900),
                  ),
                  subtitle: Padding(
                    padding: const EdgeInsets.only(top: 5),
                    child: Text(
                      '${user.email}\n${user.roleName ?? 'No role assigned'} · ${user.status.name}',
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(height: 1.4),
                    ),
                  ),
                  isThreeLine: true,
                  trailing: const Icon(Icons.edit_outlined),
                  onTap: () => _editUser(user),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class BorderlineSettingsScreen extends StatefulWidget {
  const BorderlineSettingsScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<BorderlineSettingsScreen> createState() =>
      _BorderlineSettingsScreenState();
}

class _BorderlineSettingsScreenState extends State<BorderlineSettingsScreen> {
  late bool enabled;
  late Set<String> recipients;

  @override
  void initState() {
    super.initState();
    enabled = widget.controller.borderlineEnabled;
    recipients = Set.of(widget.controller.borderlineRecipientRoleIds);
  }

  Future<void> _save() async {
    if (enabled && recipients.isEmpty) {
      showMessage(context, 'Select at least one recipient role.', error: true);
      return;
    }
    final success = await widget.controller.updateBorderlineSettings(
      enabled,
      recipients,
    );
    if (!mounted) return;
    showMessage(
      context,
      success
          ? 'Borderline alert settings saved.'
          : widget.controller.error ?? 'Unable to save settings.',
      error: !success,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Borderline alert settings',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        children: [
          Card(
            child: SwitchListTile(
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 17,
                vertical: 8,
              ),
              title: const Text(
                'Enable borderline alerts',
                style: TextStyle(fontWeight: FontWeight.w900),
              ),
              subtitle: const Padding(
                padding: EdgeInsets.only(top: 5),
                child: Text(
                  'Allow Qwen3-VL-assisted review to promote uncertain violence or bullying clips.',
                ),
              ),
              value: enabled,
              onChanged: (value) => setState(() => enabled = value),
            ),
          ),
          const SizedBox(height: 18),
          const SectionTitle(
            'Eligible recipients',
            subtitle: 'Only selected roles receive promoted borderline alerts.',
          ),
          const SizedBox(height: 10),
          Card(
            child: Column(
              children: widget.controller.roles.map((role) {
                return CheckboxListTile(
                  title: Text(
                    role.name,
                    style: const TextStyle(fontWeight: FontWeight.w800),
                  ),
                  subtitle: Text('${role.sensitivity.label} sensitivity'),
                  value: recipients.contains(role.id),
                  onChanged: enabled
                      ? (selected) => setState(() {
                          selected == true
                              ? recipients.add(role.id)
                              : recipients.remove(role.id);
                        })
                      : null,
                );
              }).toList(),
            ),
          ),
          const SizedBox(height: 18),
          FilledButton.icon(
            onPressed: _save,
            icon: const Icon(Icons.save_outlined),
            label: const Text('Save settings'),
          ),
        ],
      ),
    );
  }
}
