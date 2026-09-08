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
                      helperText: 'This name is shown to other authorised staff.',
                      prefixIcon: Icon(Icons.badge_outlined),
                    ),
                  ),
                  const SizedBox(height: 12),
                  _ReadOnlyProfileRow(label: 'Email', value: user.email),
                  _ReadOnlyProfileRow(label: 'Role', value: user.roleName ?? 'No role assigned'),
                  _ReadOnlyProfileRow(label: 'Sensitivity', value: user.sensitivity?.label ?? 'Not set'),
                  _ReadOnlyProfileRow(label: 'Account status', value: user.status.name),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: saving ? null : _save,
                    icon: saving
                        ? const SizedBox.square(dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
                        : const Icon(Icons.save_outlined),
                    label: Text(saving ? 'Saving…' : 'Save profile'),
                  ),
                  const SizedBox(height: 10),
                  OutlinedButton.icon(
                    onPressed: () async {
                      final confirmed = await showConfirmDialog(
                        context,
                        title: 'Sign out?',
                        message: 'You will need to sign in again to review GuardAId alerts.',
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
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(width: 104, child: Text(label, style: const TextStyle(color: GuardAidColors.muted))),
        Expanded(child: Text(value, style: const TextStyle(fontWeight: FontWeight.w700))),
      ],
    ),
  );
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
        if (user.isAdmin && (user.permissions.manageRoles || user.permissions.manageUsers || user.permissions.manageBorderlineSettings)) ...[
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
  Future<void> _createRole() async {
    final result = await Navigator.push<_NewRoleDraft>(
      context,
      MaterialPageRoute<_NewRoleDraft>(builder: (_) => CreateRoleScreen(controller: widget.controller)),
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
      success ? 'Role created.' : widget.controller.error ?? 'Unable to create role.',
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
          '${role.sensitivity.label} → ${selected.label}. This affects ${role.userCount} assigned user${role.userCount == 1 ? '' : 's'} and changes which alerts they receive.',
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
            'Sensitivity is stored on the role. Every assigned user inherits the same alert threshold mode.',
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
                  const Icon(Icons.info_outline_rounded, color: GuardAidColors.info),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'System roles cannot be deleted. Custom roles can be created with only permissions already held by the current administrator.',
                      style: const TextStyle(color: GuardAidColors.muted, height: 1.4),
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
                            label: Text('${widget.controller.users.where((user) => user.roleId == role.id).length} assigned user${widget.controller.users.where((user) => user.roleId == role.id).length == 1 ? '' : 's'}'),
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
      ),
    );
  }
}

class _NewRoleDraft {
  const _NewRoleDraft({required this.name, required this.description, required this.sensitivity, required this.permissions});
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
      showMessage(context, 'Description must be 200 characters or fewer.', error: true);
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
      message: '$name\nSensitivity: ${sensitivity.label}\nPermissions: ${permissionLabels.isEmpty ? 'No management permissions' : permissionLabels.join(', ')}',
      confirmLabel: 'Create role',
    );
    if (confirmed && mounted) {
      Navigator.pop(context, _NewRoleDraft(name: name, description: description, sensitivity: sensitivity, permissions: permissions));
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Create role')),
    body: ListView(
      padding: pageListPadding(context, top: 8),
      children: [
        TextField(controller: nameController, maxLength: 60, decoration: const InputDecoration(labelText: 'Role name *')),
        const SizedBox(height: 8),
        TextField(controller: descriptionController, maxLength: 200, maxLines: 3, decoration: const InputDecoration(labelText: 'Description', alignLabelWithHint: true)),
        const SizedBox(height: 12),
        DropdownButtonFormField<SensitivityMode>(
          initialValue: sensitivity,
          decoration: const InputDecoration(labelText: 'Sensitivity'),
          items: SensitivityMode.values.map((mode) => DropdownMenuItem(value: mode, child: Text(mode.label))).toList(),
          onChanged: (value) => setState(() => sensitivity = value ?? SensitivityMode.moderate),
        ),
        const SizedBox(height: 14),
        const Text('Management permissions', style: TextStyle(fontWeight: FontWeight.w900)),
        SwitchListTile(title: const Text('Manage roles'), value: manageRoles, onChanged: widget.controller.currentUser?.permissions.manageRoles == true ? (value) => setState(() => manageRoles = value) : null),
        SwitchListTile(title: const Text('Manage users'), value: manageUsers, onChanged: widget.controller.currentUser?.permissions.manageUsers == true ? (value) => setState(() => manageUsers = value) : null),
        SwitchListTile(title: const Text('Manage borderline settings'), value: manageBorderline, onChanged: widget.controller.currentUser?.permissions.manageBorderlineSettings == true ? (value) => setState(() => manageBorderline = value) : null),
        const SizedBox(height: 12),
        FilledButton.icon(onPressed: _review, icon: const Icon(Icons.check_rounded), label: const Text('Review and create')),
      ],
    ),
  );
}

class AssignedUsersScreen extends StatefulWidget {
  const AssignedUsersScreen({super.key, required this.controller, required this.role});
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
    final users = controller.users.where((user) => user.roleId == role.id).toList();
    return Scaffold(
      appBar: AppBar(title: Text('${role.name} users', maxLines: 1, overflow: TextOverflow.ellipsis)),
      body: ListView(
        padding: pageListPadding(context, top: 8),
        children: [
          Text('${users.length} assigned user${users.length == 1 ? '' : 's'} · ${role.sensitivity.label} sensitivity', style: const TextStyle(color: GuardAidColors.muted)),
          const SizedBox(height: 12),
          if (users.isEmpty) const Card(child: Padding(padding: EdgeInsets.all(18), child: Text('No users are assigned to this role.'))),
          ...users.map((user) => Card(
            child: ListTile(
              title: Text(user.displayName, style: const TextStyle(fontWeight: FontWeight.w800)),
              subtitle: Text('${user.email}\n${user.status.name}'),
              isThreeLine: true,
              trailing: IconButton(
                tooltip: 'Revoke role',
                icon: const Icon(Icons.close_rounded, color: GuardAidColors.weapon),
                onPressed: () async {
                  final ok = await showConfirmDialog(context, title: 'Revoke ${role.name}?', message: 'The user will become Pending and all registered notification devices will be disabled.', confirmLabel: 'Revoke role', destructive: true);
                  if (!ok || !context.mounted) return;
                  final success = await controller.updateUserAssignment(user.id, null, UserStatus.pending);
                  if (!context.mounted) return;
                  if (success) {
                    setState(() {});
                    showMessage(context, 'Role revoked.');
                  } else {
                    showMessage(context, controller.error ?? 'Unable to revoke role.', error: true);
                  }
                },
              ),
            ),
          )),
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
          'Role: ${role.name} (${role.sensitivity.label})\nAccess: ${result.$2 == UserStatus.active ? 'Active' : result.$2 == UserStatus.disabled ? 'Disabled' : 'Pending approval'}',
      confirmLabel: 'Save assignment',
    );
    if (!confirmed || !mounted) return;
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
        padding: pageListPadding(context, top: 8),
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
                  trailing: Tooltip(
                    message: 'Edit ${user.displayName} assignment',
                    child: const Icon(Icons.edit_outlined),
                  ),
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
  bool saving = false;

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
    final confirmed = await showConfirmDialog(
      context,
      title: enabled ? 'Enable borderline alerts?' : 'Disable borderline alerts?',
      message: enabled
          ? 'VLM-positive borderline clips may increase the number of alerts sent to the selected roles. This can improve recall but may increase alert fatigue.'
          : 'Selected roles will no longer receive additional VLM-positive borderline alerts.',
      confirmLabel: 'Save settings',
    );
    if (!confirmed || !mounted) return;
    setState(() => saving = true);
    final success = await widget.controller.updateBorderlineSettings(
      enabled,
      recipients,
    );
    if (!mounted) return;
    if (!mounted) return;
    setState(() => saving = false);
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
              subtitle: Padding(
                padding: EdgeInsets.only(top: 5),
                child: Text(
                  '${enabled ? 'Enabled' : 'Disabled'} · Allow Qwen3-VL-assisted review to promote uncertain violence or bullying clips. Enabling this may increase alert volume.',
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
            onPressed: saving || widget.controller.isBusy ? null : _save,
            icon: saving
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.save_outlined),
            label: Text(saving ? 'Saving…' : 'Save settings'),
          ),
        ],
      ),
    );
  }
}
