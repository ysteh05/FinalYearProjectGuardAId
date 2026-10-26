import 'package:flutter/material.dart';

import '../controller.dart';
import '../theme.dart';
import '../widgets.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  late final TextEditingController emailController;
  late final TextEditingController passwordController;
  bool obscurePassword = true;

  @override
  void initState() {
    super.initState();
    final isDemo = widget.controller.repository.isDemo;
    emailController = TextEditingController(
      text: isDemo ? 'admin@guardaid.demo' : '',
    );
    passwordController = TextEditingController(
      text: isDemo ? 'GuardAId123!' : '',
    );
  }

  @override
  void dispose() {
    emailController.dispose();
    passwordController.dispose();
    super.dispose();
  }

  Future<void> _signIn() async {
    FocusScope.of(context).unfocus();
    final success = await widget.controller.signIn(
      emailController.text.trim(),
      passwordController.text,
    );
    if (!success && mounted) {
      showMessage(
        context,
        widget.controller.error ?? 'Unable to sign in.',
        error: true,
      );
    }
  }

  Future<void> _resetPassword() async {
    final email = TextEditingController(text: emailController.text.trim());
    final shouldSend = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Reset password'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('Enter your authorised school email address.'),
            const SizedBox(height: 16),
            TextField(
              controller: email,
              keyboardType: TextInputType.emailAddress,
              decoration: const InputDecoration(labelText: 'Email address'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Send link'),
          ),
        ],
      ),
    );
    if (shouldSend != true) return;
    final success = await widget.controller.sendPasswordReset(
      email.text.trim(),
    );
    if (mounted) {
      showMessage(
        context,
        success
            ? 'Password reset link sent. Check your email.'
            : widget.controller.error ?? 'Unable to send the reset link.',
        error: !success,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            final keyboardInset = MediaQuery.viewInsetsOf(context).bottom;
            return SingleChildScrollView(
              padding: EdgeInsets.fromLTRB(24, 24, 24, 24 + keyboardInset),
              child: ConstrainedBox(
                constraints: BoxConstraints(
                  minHeight: (constraints.maxHeight - 48).clamp(0, double.infinity),
                  maxWidth: 460,
                ),
                child: Center(
                  child: Card(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: AutofillGroup(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                        const GuardAidBrand(),
                        const SizedBox(height: 22),
                        const Text(
                          'Welcome back',
                          style: TextStyle(
                            fontSize: 26,
                            fontWeight: FontWeight.w900,
                          ),
                        ),
                        const SizedBox(height: 7),
                        const Text(
                          'Sign in to review and respond to school safety alerts.',
                          style: TextStyle(
                            color: GuardAidColors.muted,
                            height: 1.4,
                          ),
                        ),
                        const SizedBox(height: 24),
                        TextField(
                          controller: emailController,
                          autofillHints: const [
                            AutofillHints.username,
                            AutofillHints.email,
                          ],
                          keyboardType: TextInputType.emailAddress,
                          decoration: const InputDecoration(
                            labelText: 'Email address',
                            prefixIcon: Icon(Icons.mail_outline_rounded),
                          ),
                        ),
                        const SizedBox(height: 14),
                        TextField(
                          controller: passwordController,
                          autofillHints: const [AutofillHints.password],
                          obscureText: obscurePassword,
                          onSubmitted: (_) => _signIn(),
                          decoration: InputDecoration(
                            labelText: 'Password',
                            prefixIcon: const Icon(Icons.lock_outline_rounded),
                            suffixIcon: IconButton(
                              tooltip: obscurePassword
                                  ? 'Show password'
                                  : 'Hide password',
                              onPressed: () => setState(
                                () => obscurePassword = !obscurePassword,
                              ),
                              icon: Icon(
                                obscurePassword
                                    ? Icons.visibility_outlined
                                    : Icons.visibility_off_outlined,
                              ),
                            ),
                          ),
                        ),
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton(
                            onPressed: _resetPassword,
                            child: const Text('Forgot password?'),
                          ),
                        ),
                        FilledButton.icon(
                          onPressed: widget.controller.isBusy ? null : _signIn,
                          icon: widget.controller.isBusy
                              ? const SizedBox.square(
                                  dimension: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Colors.white,
                                  ),
                                )
                              : const Icon(Icons.login_rounded),
                          label: Text(
                            widget.controller.isBusy
                                ? 'Signing in…'
                                : 'Sign in',
                          ),
                        ),
                        if (widget.controller.repository.isDemo) ...[
                          const SizedBox(height: 18),
                          Container(
                            padding: const EdgeInsets.all(13),
                            decoration: BoxDecoration(
                              color: const Color(0xFFEAF3F8),
                              borderRadius: BorderRadius.circular(12),
                            ),
                            child: const Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Icon(
                                  Icons.science_outlined,
                                  size: 20,
                                  color: GuardAidColors.navy,
                                ),
                                SizedBox(width: 10),
                                Expanded(
                                  child: Text(
                                    'Prototype demo mode\nCredentials are prefilled for local review only. This notice is not shown in live mode.',
                                    style: TextStyle(
                                      fontSize: 12,
                                      color: GuardAidColors.navy,
                                      height: 1.45,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ],
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );
  }
}

class PendingApprovalScreen extends StatelessWidget {
  const PendingApprovalScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(28),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.hourglass_top_rounded,
                        size: 58,
                        color: GuardAidColors.violence,
                      ),
                      const SizedBox(height: 18),
                      const Text(
                        'Approval pending',
                        style: TextStyle(
                          fontSize: 24,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                      const SizedBox(height: 10),
                      const Text(
                        'Your identity has been verified, but a School Administrator must assign your role before you can access alerts.',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          color: GuardAidColors.muted,
                          height: 1.5,
                        ),
                      ),
                      const SizedBox(height: 24),
                      OutlinedButton(
                        onPressed: controller.signOut,
                        child: const Text('Sign out'),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class AccessDisabledScreen extends StatelessWidget {
  const AccessDisabledScreen({super.key, required this.controller});

  final GuardAidController controller;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(28),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 440),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.block_rounded,
                        size: 58,
                        color: GuardAidColors.weapon,
                      ),
                      const SizedBox(height: 18),
                      const Text(
                        'Access disabled',
                        style: TextStyle(
                          fontSize: 24,
                          fontWeight: FontWeight.w900,
                        ),
                      ),
                      const SizedBox(height: 10),
                      const Text(
                        'This GuardAId account has been disabled. Contact a School Administrator if access should be restored.',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                          color: GuardAidColors.muted,
                          height: 1.5,
                        ),
                      ),
                      const SizedBox(height: 24),
                      OutlinedButton(
                        onPressed: controller.signOut,
                        child: const Text('Sign out'),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
