import 'package:flutter/material.dart';

import '../controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets.dart';

class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  late final TextEditingController nameController;
  bool saving = false;

  @override
  void initState() {
    super.initState();
    nameController = TextEditingController(
      text: widget.controller.currentUser?.displayName == 'Name not provided'
          ? ''
          : widget.controller.currentUser?.displayName ?? '',
    );
  }

  @override
  void dispose() {
    nameController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final name = nameController.text.trim();
    if (name.isEmpty) {
      showMessage(context, 'Enter a display name.', error: true);
      return;
    }
    setState(() => saving = true);
    final success = await widget.controller.updateProfile(name);
    if (!mounted) return;
    setState(() => saving = false);
    showMessage(
      context,
      success
          ? 'Profile updated.'
          : widget.controller.error ?? 'Unable to update profile.',
      error: !success,
    );
  }

  @override
  Widget build(BuildContext context) {
    final user = widget.controller.currentUser!;
    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: ListView(
        padding: pageListPadding(context, top: 8),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(18),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  TextField(
                    controller: nameController,
                    maxLength: 80,
                    textInputAction: TextInputAction.done,
                    decoration: const InputDecoration(
                      labelText: 'Display name',
                      helperText:
                          'This name is shown to other authorised staff.',
                      prefixIcon: Icon(Icons.badge_outlined),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _ReadOnlyProfileRow(label: 'Email', value: user.email),
                  _ReadOnlyProfileRow(
                    label: 'Role',
                    value: user.roleName ?? 'No role assigned',
                  ),
                  _ReadOnlyProfileRow(
                    label: 'Sensitivity',
                    value: user.roleSensitivities.isEmpty
                        ? user.sensitivity?.label ?? 'Not set'
                        : user.roleSensitivities
                              .map((item) => item.label)
                              .join(', '),
                  ),
                  _ReadOnlyProfileRow(
                    label: 'Account status',
                    value: user.status.name,
                  ),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: saving ? null : _save,
                    icon: saving
                        ? const SizedBox.square(
                            dimension: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.save_outlined),
                    label: Text(saving ? 'Saving…' : 'Save profile'),
                  ),
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: () async {
                      final confirmed = await showConfirmDialog(
                        context,
                        title: 'Sign out?',
                        message:
                            'You will need to sign in again to review GuardAId alerts.',
                        confirmLabel: 'Sign out',
                      );
                      if (!confirmed || !context.mounted) return;
                      await widget.controller.signOut();
                      if (context.mounted) Navigator.pop(context);
                    },
                    icon: const Icon(Icons.logout_rounded),
                    label: const Text('Sign out'),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ReadOnlyProfileRow extends StatelessWidget {
  const _ReadOnlyProfileRow({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final displayValue = switch (value) {
      'active' => 'Active',
      'pending' => 'Pending',
      'disabled' => 'Disabled',
      _ => value,
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              color: GuardAidColors.muted,
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.2,
            ),
          ),
          const SizedBox(height: 5),
          Text(
            displayValue,
            softWrap: true,
            style: const TextStyle(fontWeight: FontWeight.w700, height: 1.35),
          ),
        ],
      ),
    );
  }
}

class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  Widget build(BuildContext context) {
    final user = controller.currentUser!;
    return ListView(
      padding: pageListPadding(context),
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
        if (user.permissions.manageRoles ||
            user.permissions.manageUsers ||
            user.permissions.manageBorderlineSettings) ...[
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
          if (user.permissions.manageRoles)
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
          if (user.permissions.manageUsers)
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
          if (user.permissions.manageBorderlineSettings)
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

class RoleManagementScreen extends StatefulWidget {
  const RoleManagementScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<RoleManagementScreen> createState() => _RoleManagementScreenState();
}

class _RoleManagementScreenState extends State<RoleManagementScreen> {
  Future<void> _createRole() async {
    final result = await Navigator.push<_NewRoleDraft>(
      context,
      MaterialPageRoute<_NewRoleDraft>(
        builder: (_) => CreateRoleScreen(controller: widget.controller),
      ),
    );
    if (result == null || !mounted) return;
    final success = await widget.controller.createRole(
      name: result.name,
      description: result.description,
      sensitivity: result.sensitivity,
      permissions: result.permissions,
    );
    if (!mounted) return;
    showMessage(
      context,
      success
          ? 'Role created.'
          : widget.controller.error ?? 'Unable to create role.',
      error: !success,
    );
    if (success) setState(() {});
  }

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
                  'Choose which alert notifications users in this role receive. All active users can view all incidents.',
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
                        onPressed: () => Navigator.pop(context, mode),
                        child: const Text('Save sensitivity'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (selected == null || !mounted || selected == role.sensitivity) return;
    final confirmed = await showConfirmDialog(
      context,
      title: 'Update ${role.name} sensitivity?',
      message:
          '${role.sensitivity.label} → ${selected.label}. This changes alert notifications for ${role.userCount} assigned user${role.userCount == 1 ? '' : 's'}. All incidents remain available to view.',
      confirmLabel: 'Update sensitivity',
    );
    if (!confirmed || !mounted) return;
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

  Future<void> _deleteRole(GuardRole role) async {
    if (role.isSystem) return;
    final assignedCount = role.userCount;
    final assignmentWarning = assignedCount == 0
        ? 'No users are currently assigned to this role.'
        : '$assignedCount user${assignedCount == 1 ? '' : 's'} currently have this role. Each will lose it. GuardAId currently assigns one role per user, so affected users will become Pending, lose access to alerts and evidence, and have their registered notification devices disabled until an administrator assigns a role again.';
    final confirmed = await showConfirmDialog(
      context,
      title: 'Delete ${role.name}?',
      message:
          '$assignmentWarning\n\nThe role will also be removed from borderline alert recipients. If it was the last recipient role, borderline alert promotion will be disabled. This action cannot be undone.',
      confirmLabel: 'Delete role',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    final success = await widget.controller.deleteRole(role.id);
    if (!mounted) return;
    if (success) setState(() {});
    showMessage(
      context,
      success
          ? (assignedCount == 0
                ? '${role.name} deleted.'
                : '${role.name} deleted. Affected users now require a role assignment.')
          : widget.controller.error ?? 'Unable to delete the role.',
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
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Role sensitivity controls alert notifications. All active users can view all incidents and evidence.',
              style: TextStyle(color: GuardAidColors.muted, height: 1.45),
            ),
            const SizedBox(height: 10),
            Card(
              color: const Color(0xFFF6FBFE),
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(
                      Icons.info_outline_rounded,
                      color: GuardAidColors.info,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        'Each user has one assigned role. Deleting a custom role removes that assignment; users who lose their only role become Pending. System roles are protected.',
                        style: const TextStyle(
                          color: GuardAidColors.muted,
                          height: 1.4,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: _createRole,
              icon: const Icon(Icons.add_rounded),
              label: const Text('Create role'),
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
                        Text(
                          role.name,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w900,
                          ),
                        ),
                        const SizedBox(height: 8),
                        StatusBadge(
                          label: role.sensitivity.label,
                          color: GuardAidColors.info,
                          icon: Icons.tune_rounded,
                        ),
                        const SizedBox(height: 10),
                        Text(
                          role.description.isEmpty
                              ? 'No description provided.'
                              : role.description,
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
                            TextButton.icon(
                              onPressed: () async {
                                await Navigator.push<bool>(
                                  context,
                                  MaterialPageRoute<bool>(
                                    builder: (_) => AssignedUsersScreen(
                                      controller: widget.controller,
                                      role: role,
                                    ),
                                  ),
                                );
                                if (mounted) setState(() {});
                              },
                              icon: const Icon(Icons.people_outline, size: 18),
                              label: Text(
                                '${role.userCount} assigned user${role.userCount == 1 ? '' : 's'}',
                              ),
                            ),
                            TextButton.icon(
                              onPressed: () => _editRole(role),
                              icon: const Icon(Icons.edit_outlined, size: 18),
                              label: const Text('Edit threshold'),
                            ),
                            if (!role.isSystem)
                              TextButton.icon(
                                onPressed: () => _deleteRole(role),
                                style: TextButton.styleFrom(
                                  foregroundColor: GuardAidColors.weapon,
                                ),
                                icon: const Icon(
                                  Icons.delete_outline_rounded,
                                  size: 18,
                                ),
                                label: const Text('Delete role'),
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
      ),
    );
  }
}

class _NewRoleDraft {
  const _NewRoleDraft({
    required this.name,
    required this.description,
    required this.sensitivity,
    required this.permissions,
  });
  final String name;
  final String description;
  final SensitivityMode sensitivity;
  final RolePermissions permissions;
}

class CreateRoleScreen extends StatefulWidget {
  const CreateRoleScreen({super.key, required this.controller});
  final GuardAidController controller;

  @override
  State<CreateRoleScreen> createState() => _CreateRoleScreenState();
}

class _CreateRoleScreenState extends State<CreateRoleScreen> {
  final nameController = TextEditingController();
  final descriptionController = TextEditingController();
  SensitivityMode sensitivity = SensitivityMode.moderate;
  bool manageRoles = false;
  bool manageUsers = false;
  bool manageBorderline = false;

  @override
  void dispose() {
    nameController.dispose();
    descriptionController.dispose();
    super.dispose();
  }

  Future<void> _review() async {
    final name = nameController.text.trim();
    final description = descriptionController.text.trim();
    if (name.length < 2 || name.length > 60) {
      showMessage(context, 'Role name must be 2–60 characters.', error: true);
      return;
    }
    if (description.length > 200) {
      showMessage(
        context,
        'Description must be 200 characters or fewer.',
        error: true,
      );
      return;
    }
    final permissions = RolePermissions(
      manageRoles: manageRoles,
      manageUsers: manageUsers,
      manageBorderlineSettings: manageBorderline,
    );
    final permissionLabels = <String>[
      if (manageRoles) 'Manage roles',
      if (manageUsers) 'Manage users',
      if (manageBorderline) 'Manage borderline settings',
    ];
    final confirmed = await showConfirmDialog(
      context,
      title: 'Create this role?',
      message:
          '$name\nSensitivity: ${sensitivity.label}\nPermissions: ${permissionLabels.isEmpty ? 'No management permissions' : permissionLabels.join(', ')}',
      confirmLabel: 'Create role',
    );
    if (confirmed && mounted) {
      Navigator.pop(
        context,
        _NewRoleDraft(
          name: name,
          description: description,
          sensitivity: sensitivity,
          permissions: permissions,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Create role')),
    body: ListView(
      padding: pageListPadding(context, top: 8),
      children: [
        TextField(
          controller: nameController,
          maxLength: 60,
          decoration: const InputDecoration(labelText: 'Role name *'),
        ),
        const SizedBox(height: 8),
        TextField(
          controller: descriptionController,
          maxLength: 200,
          maxLines: 3,
          decoration: const InputDecoration(
            labelText: 'Description',
            alignLabelWithHint: true,
          ),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<SensitivityMode>(
          initialValue: sensitivity,
          decoration: const InputDecoration(labelText: 'Sensitivity'),
          items: SensitivityMode.values
              .map(
                (mode) =>
                    DropdownMenuItem(value: mode, child: Text(mode.label)),
              )
              .toList(),
          onChanged: (value) =>
              setState(() => sensitivity = value ?? SensitivityMode.moderate),
        ),
        const SizedBox(height: 14),
        const Text(
          'Management permissions',
          style: TextStyle(fontWeight: FontWeight.w900),
        ),
        SwitchListTile(
          title: const Text('Manage roles'),
          value: manageRoles,
          onChanged:
              widget.controller.currentUser?.permissions.manageRoles == true
              ? (value) => setState(() => manageRoles = value)
              : null,
        ),
        SwitchListTile(
          title: const Text('Manage users'),
          value: manageUsers,
          onChanged:
              widget.controller.currentUser?.permissions.manageUsers == true
              ? (value) => setState(() => manageUsers = value)
              : null,
        ),
        SwitchListTile(
          title: const Text('Manage borderline settings'),
          value: manageBorderline,
          onChanged:
              widget
                      .controller
                      .currentUser
                      ?.permissions
                      .manageBorderlineSettings ==
                  true
              ? (value) => setState(() => manageBorderline = value)
              : null,
        ),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: _review,
          icon: const Icon(Icons.check_rounded),
          label: const Text('Review and create'),
        ),
      ],
    ),
  );
}

class AssignedUsersScreen extends StatefulWidget {
  const AssignedUsersScreen({
    super.key,
    required this.controller,
    required this.role,
  });
  final GuardAidController controller;
  final GuardRole role;

  @override
  State<AssignedUsersScreen> createState() => _AssignedUsersScreenState();
}

class _AssignedUsersScreenState extends State<AssignedUsersScreen> {
  GuardAidController get controller => widget.controller;
  GuardRole get role => widget.role;

  @override
  Widget build(BuildContext context) {
    final users = controller.users
        .where((user) => user.roleId == role.id)
        .toList();
    return Scaffold(
      appBar: AppBar(
        title: Text(
          '${role.name} users',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
      ),
      body: ListView(
        padding: pageListPadding(context, top: 8),
        children: [
          Text(
            '${users.length} assigned user${users.length == 1 ? '' : 's'} · ${role.sensitivity.label} sensitivity',
            style: const TextStyle(color: GuardAidColors.muted),
          ),
          const SizedBox(height: 12),
          if (users.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(18),
                child: Text('No users are assigned to this role.'),
              ),
            ),
          ...users.map(
            (user) => Card(
              child: ListTile(
                title: Text(
                  user.displayName,
                  style: const TextStyle(fontWeight: FontWeight.w800),
                ),
                subtitle: Text('${user.email}\n${user.status.name}'),
                isThreeLine: true,
                trailing: IconButton(
                  tooltip: 'Revoke role',
                  icon: const Icon(
                    Icons.close_rounded,
                    color: GuardAidColors.weapon,
                  ),
                  onPressed: () async {
                    final ok = await showConfirmDialog(
                      context,
                      title: 'Revoke ${role.name}?',
                      message:
                          'The user will become Pending and all registered notification devices will be disabled.',
                      confirmLabel: 'Revoke role',
                      destructive: true,
                    );
                    if (!ok || !context.mounted) return;
                    final success = await controller.updateUserAssignment(
                      user.id,
                      null,
                      UserStatus.pending,
                    );
                    if (!context.mounted) return;
                    if (success) {
                      setState(() {});
                      showMessage(context, 'Role revoked.');
                    } else {
                      showMessage(
                        context,
                        controller.error ?? 'Unable to revoke role.',
                        error: true,
                      );
                    }
                  },
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
    'Notifications for Sensitive, Moderate and Certain alerts, subject to borderline settings.',
  SensitivityMode.moderate => 'Notifications for Moderate and Certain alerts.',
  SensitivityMode.certain => 'Notifications for Certain alerts only.',
};

String _rolePermissionsLabel(GuardRole role) {
  final permissions = <String>[
    if (role.permissions.manageRoles) 'Manage roles',
    if (role.permissions.manageUsers) 'Manage users',
    if (role.permissions.manageBorderlineSettings) 'Manage borderline settings',
  ];
  return permissions.isEmpty ? 'Standard access' : permissions.join(', ');
}

class UserAssignmentScreen extends StatefulWidget {
  const UserAssignmentScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<UserAssignmentScreen> createState() => _UserAssignmentScreenState();
}

class _UserAssignmentScreenState extends State<UserAssignmentScreen> {
  String query = '';
  bool processing = false;

  Future<void> _addUser() async {
    if (widget.controller.currentUser?.permissions.manageUsers != true) return;
    final result = await Navigator.of(context).push<UserProvisioningResult>(
      MaterialPageRoute<UserProvisioningResult>(
        builder: (_) => AddUserScreen(controller: widget.controller),
      ),
    );
    if (!mounted || result == null) return;
    setState(() {});
    if (result.alreadyExists) {
      await _editUser(result.user);
      if (!mounted) return;
      showMessage(
        context,
        'This email is already in GuardAId. The existing user record was opened; no duplicate was created.',
      );
      return;
    }
    if (result.isDemo) {
      showMessage(
        context,
        'Demo user added. No Firebase account or email was created.',
      );
      return;
    }
    if (result.setupEmailRequested) {
      showMessage(
        context,
        result.identityLinked
            ? 'Existing Firebase account linked and setup email requested.'
            : 'User created and activated. Password setup email requested.',
      );
      return;
    }
    final retry = await showConfirmDialog(
      context,
      title: 'Account created, email not sent',
      message:
          'The account is active, but the password setup email could not be requested. ${result.setupEmailError ?? ''} You can retry now or use Resend setup email from this user’s menu later.',
      confirmLabel: 'Retry email',
    );
    if (!retry || !mounted) return;
    setState(() => processing = true);
    final sent = await widget.controller.resendUserSetupEmail(
      result.user.email,
    );
    if (!mounted) return;
    setState(() => processing = false);
    showMessage(
      context,
      sent
          ? 'Password setup email request accepted.'
          : widget.controller.error ?? 'Unable to request the setup email.',
      error: !sent,
    );
  }

  Future<void> _deactivateUser(AppUser user) async {
    final confirmed = await showConfirmDialog(
      context,
      title: 'Deactivate ${user.displayName}?',
      message:
          'This account will lose access to alerts and evidence, and its registered notification devices will be disabled. The user and their history will be retained, and an administrator can reactivate the account later.',
      confirmLabel: 'Deactivate account',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    if (processing) return;
    setState(() => processing = true);
    final success = await widget.controller.deactivateUser(user.id);
    if (!mounted) return;
    setState(() => processing = false);
    showMessage(
      context,
      success
          ? 'Account deactivated.'
          : widget.controller.error ?? 'Unable to deactivate this account.',
      error: !success,
    );
  }

  Future<void> _reactivateUser(AppUser user) async {
    final roleId = user.roleId;
    if (roleId == null) {
      showMessage(
        context,
        'Assign a role before reactivating this account.',
        error: true,
      );
      return;
    }
    final role = widget.controller.roles.firstWhere(
      (item) => item.id == roleId,
    );
    final confirmed = await showConfirmDialog(
      context,
      title: 'Reactivate ${user.displayName}?',
      message:
          'This restores access with ${role.name} role and ${role.sensitivity.label} sensitivity. Permissions: ${_rolePermissionsLabel(role)}. The user’s phone will register for notifications when the App next refreshes its session.',
      confirmLabel: 'Reactivate account',
    );
    if (!confirmed || !mounted) return;
    if (processing) return;
    setState(() => processing = true);
    final success = await widget.controller.updateUserAssignment(
      user.id,
      role.id,
      UserStatus.active,
    );
    if (!mounted) return;
    setState(() => processing = false);
    showMessage(
      context,
      success
          ? 'Account reactivated.'
          : widget.controller.error ?? 'Unable to reactivate this account.',
      error: !success,
    );
  }

  Future<void> _revokeRole(AppUser user) async {
    if (processing || user.roleId == null) return;
    final confirmed = await showConfirmDialog(
      context,
      title: 'Revoke ${user.displayName}’s role?',
      message:
          'This removes the assigned role and changes the account to Pending, so the user loses access to alerts and evidence and their registered notification devices are disabled. The Firebase account and user history are retained. Use Deactivate account instead if you want to preserve the role for later reactivation.',
      confirmLabel: 'Revoke role',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => processing = true);
    final success = await widget.controller.updateUserAssignment(
      user.id,
      null,
      UserStatus.pending,
    );
    if (!mounted) return;
    setState(() => processing = false);
    showMessage(
      context,
      success
          ? 'Role revoked. The account is Pending.'
          : widget.controller.error ?? 'Unable to revoke this role.',
      error: !success,
    );
  }

  Future<void> _resendSetupEmail(AppUser user) async {
    if (widget.controller.repository.isDemo) {
      showMessage(context, 'Demo Mode does not send real setup emails.');
      return;
    }
    if (processing) return;
    setState(() => processing = true);
    final sent = await widget.controller.resendUserSetupEmail(user.email);
    if (!mounted) return;
    setState(() => processing = false);
    showMessage(
      context,
      sent
          ? 'Password setup email request accepted.'
          : widget.controller.error ?? 'Unable to request the setup email.',
      error: !sent,
    );
  }

  Future<void> _editUser(AppUser user) async {
    String? roleId = user.roleId;
    var status = user.status;
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
                  items: [
                    const DropdownMenuItem<String>(
                      value: null,
                      child: Text('No role assigned'),
                    ),
                    ...widget.controller.roles.map(
                      (role) => DropdownMenuItem(
                        value: role.id,
                        child: Text(
                          '${role.name} · ${role.sensitivity.label}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ),
                  ],
                  onChanged: (value) => setModalState(() => roleId = value),
                ),
                const SizedBox(height: 13),
                DropdownButtonFormField<UserStatus>(
                  initialValue: status,
                  decoration: const InputDecoration(
                    labelText: 'Account access',
                  ),
                  items: const [
                    DropdownMenuItem(
                      value: UserStatus.pending,
                      child: Text('Pending approval'),
                    ),
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
                        onPressed: roleId == null
                            ? null
                            : () => Navigator.pop(context, (roleId!, status)),
                        child: const Text('Review changes'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (result == null || !mounted) return;
    final role = widget.controller.roles.firstWhere(
      (item) => item.id == result.$1,
    );
    final confirmed = await showConfirmDialog(
      context,
      title: 'Save assignment for ${user.displayName}?',
      message:
          'Role: ${role.name} (${role.sensitivity.label})\nAccess: ${result.$2 == UserStatus.active
              ? 'Active'
              : result.$2 == UserStatus.disabled
              ? 'Disabled'
              : 'Pending approval'}',
      confirmLabel: 'Save assignment',
    );
    if (!confirmed || !mounted) return;
    if (processing) return;
    setState(() => processing = true);
    final success = await widget.controller.updateUserAssignment(
      user.id,
      result.$1,
      result.$2,
    );
    if (!mounted) return;
    setState(() => processing = false);
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
        actions: [
          if (widget.controller.currentUser?.permissions.manageUsers == true)
            IconButton(
              tooltip: 'Add user',
              onPressed: processing ? null : _addUser,
              icon: const Icon(Icons.person_add_alt_1_rounded),
            ),
          const SizedBox(width: 4),
        ],
      ),
      body: ListView(
        padding: pageListPadding(context, top: 8),
        children: [
          const Text(
            'Create staff accounts, assign one role, and manage access. New users are activated with the selected role.',
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
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      IconButton(
                        tooltip: user.id == widget.controller.currentUser?.id
                            ? 'Your own role and status cannot be changed here'
                            : 'Edit ${user.displayName} assignment',
                        onPressed:
                            user.id == widget.controller.currentUser?.id ||
                                processing
                            ? null
                            : () => _editUser(user),
                        icon: const Icon(Icons.edit_outlined),
                      ),
                      PopupMenuButton<String>(
                        tooltip: 'More user actions',
                        onSelected: (action) {
                          switch (action) {
                            case 'deactivate':
                              _deactivateUser(user);
                              break;
                            case 'reactivate':
                              _reactivateUser(user);
                              break;
                            case 'revoke':
                              _revokeRole(user);
                              break;
                            case 'resend':
                              _resendSetupEmail(user);
                              break;
                          }
                        },
                        enabled:
                            user.id != widget.controller.currentUser?.id &&
                            !processing,
                        itemBuilder: (_) => [
                          if (user.status != UserStatus.disabled)
                            const PopupMenuItem(
                              value: 'deactivate',
                              child: Text('Deactivate account'),
                            ),
                          if (user.status == UserStatus.disabled &&
                              user.roleId != null)
                            const PopupMenuItem(
                              value: 'reactivate',
                              child: Text('Reactivate account'),
                            ),
                          if (user.roleId != null)
                            const PopupMenuItem(
                              value: 'revoke',
                              child: Text('Revoke role'),
                            ),
                          const PopupMenuItem(
                            value: 'resend',
                            child: Text('Resend setup email'),
                          ),
                        ],
                      ),
                    ],
                  ),
                  onTap: user.id == widget.controller.currentUser?.id
                      ? null
                      : () => _editUser(user),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class AddUserScreen extends StatefulWidget {
  const AddUserScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<AddUserScreen> createState() => _AddUserScreenState();
}

class _AddUserScreenState extends State<AddUserScreen> {
  final formKey = GlobalKey<FormState>();
  final nameController = TextEditingController();
  final emailController = TextEditingController();
  String? selectedRoleId;
  bool saving = false;

  @override
  void dispose() {
    nameController.dispose();
    emailController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (saving || !formKey.currentState!.validate()) return;
    final roleId = selectedRoleId;
    if (roleId == null) {
      showMessage(
        context,
        'Choose a role before creating the account.',
        error: true,
      );
      return;
    }
    final role = widget.controller.roles.firstWhere(
      (item) => item.id == roleId,
    );
    final confirmed = await showConfirmDialog(
      context,
      title: 'Create and activate this user?',
      message: _createConfirmation(role),
      confirmLabel: 'Create user',
    );
    if (!confirmed || !mounted) return;
    await _create(linkExisting: false);
  }

  Future<void> _create({required bool linkExisting}) async {
    final roleId = selectedRoleId;
    if (roleId == null) return;
    setState(() => saving = true);
    final result = await widget.controller.createUser(
      displayName: nameController.text.trim(),
      email: emailController.text.trim(),
      roleId: roleId,
      linkExisting: linkExisting,
    );
    if (!mounted) return;
    setState(() => saving = false);
    if (result != null) {
      Navigator.of(context).pop(result);
      return;
    }

    final code = widget.controller.errorCode;
    if (code == 'firebase_identity_exists' && !linkExisting) {
      final link = await showConfirmDialog(
        context,
        title: 'Link existing Firebase account?',
        message:
            'A Firebase account already uses this email. If this is the intended staff member, GuardAId can link that existing sign-in and activate it with the selected role. No password will be changed.',
        confirmLabel: 'Link account',
      );
      if (link && mounted) await _create(linkExisting: true);
      return;
    }
    showMessage(
      context,
      _friendlyError(code) ??
          widget.controller.error ??
          'Unable to create this user.',
      error: true,
    );
  }

  String _createConfirmation(GuardRole role) {
    final setupNote = widget.controller.repository.isDemo
        ? 'Demo mode only: no Firebase account or email will be created.'
        : 'A password setup email will be requested after the account is created.';
    return '${nameController.text.trim()}\n${emailController.text.trim()}\n\n'
        'Role: ${role.name}\n'
        'Sensitivity: ${role.sensitivity.label}\n'
        'Access: Active\n'
        'Permissions: ${_rolePermissionsLabel(role)}\n\n'
        '$setupNote';
  }

  String? _friendlyError(String? code) => switch (code) {
    'user_already_registered' =>
      'A GuardAId user with this email already exists. Find the user in the assignments list.',
    'firebase_identity_exists' =>
      'A Firebase sign-in already uses this email. Review the existing account before trying again.',
    'firebase_identity_disabled' =>
      'This Firebase account is disabled and cannot be linked from GuardAId.',
    'firebase_identity_not_found' =>
      'The Firebase account was not found. Check the email and try again.',
    'account_created_but_local_setup_failed' =>
      'Firebase created the sign-in, but GuardAId could not finish saving the user. Ask an administrator to check the account before retrying.',
    _ => null,
  };

  @override
  Widget build(BuildContext context) {
    final roles = widget.controller.roles;
    final selectedRole = roles
        .where((role) => role.id == selectedRoleId)
        .firstOrNull;
    return Scaffold(
      appBar: AppBar(title: const Text('Add user')),
      body: SafeArea(
        child: Form(
          key: formKey,
          child: ListView(
            padding: pageListPadding(context, top: 8),
            children: [
              const Text(
                'Create a staff sign-in and assign one role. The user will be active after creation.',
                style: TextStyle(color: GuardAidColors.muted, height: 1.45),
              ),
              if (widget.controller.repository.isDemo) ...[
                const SizedBox(height: 12),
                Card(
                  color: GuardAidColors.info.withValues(alpha: 0.07),
                  child: const Padding(
                    padding: EdgeInsets.all(14),
                    child: Text(
                      'Demo mode: this only simulates adding a user. It will not create a Firebase account or send an email.',
                      style: TextStyle(height: 1.4),
                    ),
                  ),
                ),
              ],
              const SizedBox(height: 18),
              TextFormField(
                controller: nameController,
                textCapitalization: TextCapitalization.words,
                maxLength: 80,
                decoration: const InputDecoration(
                  labelText: 'Display name',
                  prefixIcon: Icon(Icons.person_outline_rounded),
                ),
                validator: (value) => value == null || value.trim().isEmpty
                    ? 'Enter the user’s name.'
                    : null,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: emailController,
                keyboardType: TextInputType.emailAddress,
                textInputAction: TextInputAction.next,
                autocorrect: false,
                maxLength: 254,
                decoration: const InputDecoration(
                  labelText: 'Email address',
                  prefixIcon: Icon(Icons.email_outlined),
                ),
                validator: (value) {
                  final email = value?.trim() ?? '';
                  if (!RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(email)) {
                    return 'Enter a valid email address.';
                  }
                  return null;
                },
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: selectedRoleId,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Assigned role',
                  prefixIcon: Icon(Icons.admin_panel_settings_outlined),
                ),
                items: roles
                    .map(
                      (role) => DropdownMenuItem(
                        value: role.id,
                        child: Text(
                          '${role.name} · ${role.sensitivity.label}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    )
                    .toList(growable: false),
                onChanged: saving
                    ? null
                    : (value) => setState(() => selectedRoleId = value),
                validator: (value) => value == null ? 'Choose a role.' : null,
              ),
              if (selectedRole != null) ...[
                const SizedBox(height: 12),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          selectedRole.name,
                          style: const TextStyle(fontWeight: FontWeight.w900),
                        ),
                        if (selectedRole.description.isNotEmpty) ...[
                          const SizedBox(height: 5),
                          Text(
                            selectedRole.description,
                            style: const TextStyle(height: 1.4),
                          ),
                        ],
                        const SizedBox(height: 8),
                        Text('Sensitivity: ${selectedRole.sensitivity.label}'),
                        const SizedBox(height: 4),
                        Text(
                          'Permissions: ${_rolePermissionsLabel(selectedRole)}',
                          style: const TextStyle(
                            color: GuardAidColors.info,
                            fontWeight: FontWeight.w700,
                            height: 1.35,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
              const SizedBox(height: 16),
              if (!widget.controller.repository.isDemo)
                Card(
                  color: GuardAidColors.info.withValues(alpha: 0.07),
                  child: const Padding(
                    padding: EdgeInsets.all(15),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          Icons.mark_email_read_outlined,
                          color: GuardAidColors.info,
                        ),
                        SizedBox(width: 11),
                        Expanded(
                          child: Text(
                            'GuardAId never displays or sends the initial password. It requests a Firebase password setup email; delivery is controlled by Firebase and the recipient’s mail provider.',
                            style: TextStyle(height: 1.4),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              const SizedBox(height: 20),
              FilledButton.icon(
                onPressed: saving ? null : _submit,
                icon: saving
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.person_add_alt_1_rounded),
                label: Text(saving ? 'Creating account…' : 'Create user'),
              ),
              const SizedBox(height: 12),
            ],
          ),
        ),
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
  final Set<String> _savingTargets = {};

  bool _roleBusy(BorderlineRoleGroup role) =>
      _savingTargets.contains(role.id) ||
      role.users.any((user) => _savingTargets.contains(user.id));

  Future<void> _manageRole(BorderlineRoleGroup role, bool enabled) async {
    if (_roleBusy(role)) return;
    if (role.users.any((user) => user.settingSource == 'individual')) {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text('${enabled ? 'Enable' : 'Disable'} all members?'),
          content: Text(
            'This applies to all ${role.userCount} members of ${role.name} and replaces their individual settings.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(enabled ? 'Enable all' : 'Disable all'),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted || _roleBusy(role)) return;
    }
    await _setRole(role, enabled);
  }

  Widget _roleHeader(BorderlineRoleGroup role) {
    final busy = _roleBusy(role);
    final state = role.userCount == 0
        ? 'No members'
        : switch (role.selectionState) {
            'on' => 'All enabled',
            'mixed' => 'Mixed',
            _ => 'All disabled',
          };
    final menu = PopupMenuButton<bool>(
      key: ValueKey('role-manage-${role.id}'),
      tooltip: 'Manage recipients for ${role.name}',
      enabled: !busy && role.userCount > 0,
      onSelected: (enabled) => _manageRole(role, enabled),
      itemBuilder: (context) => const [
        PopupMenuItem(value: true, child: Text('Enable all')),
        PopupMenuItem(value: false, child: Text('Disable all')),
      ],
      child: ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 48, minWidth: 48),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                busy ? 'Saving…' : 'Manage',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: busy ? GuardAidColors.muted : GuardAidColors.navy,
                ),
              ),
              const SizedBox(width: 3),
              const Icon(Icons.expand_more, size: 18),
            ],
          ),
        ),
      ),
    );
    final title = Text(
      role.name,
      style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
    );
    final summary = Text(
      '${role.sensitivity.label} · ${role.enabledUserCount} of ${role.userCount} enabled',
      style: const TextStyle(fontSize: 12, color: GuardAidColors.muted),
    );
    final status = Text(
      state,
      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w500),
    );
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 8, 8, 8),
      child: LayoutBuilder(
        builder: (context, size) {
          final stacked =
              size.maxWidth < 250 ||
              MediaQuery.textScalerOf(context).scale(1) > 1.3;
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (stacked) ...[
                title,
                Wrap(
                  spacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [status, menu],
                ),
                summary,
              ] else ...[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Padding(
                        padding: const EdgeInsets.only(top: 10),
                        child: title,
                      ),
                    ),
                    menu,
                  ],
                ),
                summary,
                const SizedBox(height: 3),
                status,
              ],
            ],
          );
        },
      ),
    );
  }

  Future<void> _setRole(BorderlineRoleGroup role, bool enabled) async {
    if (!_savingTargets.add(role.id)) return;
    setState(() {});
    final success = await widget.controller.setBorderlineRoleEnabled(
      role.id,
      enabled,
    );
    if (!mounted) return;
    _savingTargets.remove(role.id);
    setState(() {});
    showMessage(
      context,
      success
          ? '${role.name} members ${enabled ? 'enabled' : 'disabled'} for Borderline Review.'
          : widget.controller.error ?? 'Unable to save settings.',
      error: !success,
    );
  }

  Future<void> _setUser(BorderlineUserSetting user, bool enabled) async {
    if (!_savingTargets.add(user.id)) return;
    setState(() {});
    final success = await widget.controller.setBorderlineUserEnabled(
      user.id,
      enabled,
    );
    if (!mounted) return;
    _savingTargets.remove(user.id);
    setState(() {});
    showMessage(
      context,
      success
          ? '${user.displayName} ${enabled ? 'enabled' : 'disabled'} for Borderline Review.'
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
      body: ListenableBuilder(
        listenable: widget.controller,
        builder: (context, _) {
          final settings = widget.controller.borderlineSettings;
          if (settings == null) {
            return const Center(child: CircularProgressIndicator());
          }
          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
            children: [
              Card(
                color: settings.reviewEnabled
                    ? null
                    : Theme.of(context).colorScheme.errorContainer,
                child: ListTile(
                  leading: Icon(
                    settings.reviewEnabled
                        ? Icons.check_circle_outline
                        : Icons.info_outline,
                    color: settings.reviewEnabled
                        ? GuardAidColors.success
                        : GuardAidColors.violence,
                  ),
                  title: Text(
                    settings.reviewEnabled
                        ? 'Borderline Review is available'
                        : 'Borderline Review is currently unavailable',
                    style: const TextStyle(fontWeight: FontWeight.w900),
                  ),
                  subtitle: Text(
                    settings.reviewEnabled
                        ? 'The Web Dashboard master switch is on. Account settings below determine who can receive VLM-confirmed supplemental notifications.'
                        : 'Turn it on in Web Dashboard → Config to use this feature. Saved account settings are retained; you can still configure them here.',
                  ),
                ),
              ),
              const SizedBox(height: 18),
              const SectionTitle(
                'Recipients by role',
                subtitle:
                    'Manage a whole role or adjust individual recipients below.',
              ),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  style: TextButton.styleFrom(minimumSize: const Size(48, 48)),
                  onPressed: () => showDialog<void>(
                    context: context,
                    builder: (context) => AlertDialog(
                      title: const Text('How recipient settings work'),
                      content: const Text(
                        'Enable all and Disable all replace every member setting in that role. Individual switches create exceptions until the next bulk action.',
                      ),
                      actions: [
                        TextButton(
                          onPressed: () => Navigator.pop(context),
                          child: const Text('Got it'),
                        ),
                      ],
                    ),
                  ),
                  icon: const Icon(Icons.info_outline, size: 18),
                  label: const Text('How this works'),
                ),
              ),
              const SizedBox(height: 10),
              ...settings.roles.map(
                (role) => Card(
                  margin: const EdgeInsets.only(bottom: 12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      _roleHeader(role),
                      const Divider(height: 1),
                      ...role.users.map(
                        (user) => SwitchListTile(
                          title: Text(user.displayName),
                          subtitle: Text(
                            '${user.status} · ${user.settingSource == 'individual' ? 'individual setting' : 'role default'}',
                          ),
                          value: user.enabled,
                          onChanged: _roleBusy(role)
                              ? null
                              : (enabled) => _setUser(user, enabled),
                        ),
                      ),
                      if (role.users.isEmpty)
                        const Padding(
                          padding: EdgeInsets.fromLTRB(16, 0, 16, 14),
                          child: Text(
                            'No members are currently assigned to this role.',
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}
