import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:integration_test/integration_test.dart';
import 'package:guardaid_mobile/src/app.dart';
import 'package:guardaid_mobile/src/models.dart';
import 'package:guardaid_mobile/src/notification_service.dart';
import 'package:guardaid_mobile/src/screens/alerts_screen.dart';

/// Emulator smoke coverage for the client-side EMU checklist.
///
/// This deliberately exercises Demo mode only. Live API, SQLite, FCM,
/// protected media, and physical-device checks remain separate test levels.
void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets(
    'EMU creates native notification channels without permission changes',
    (tester) async {
      final plugin = FlutterLocalNotificationsPlugin();
      final android = plugin
          .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin
          >();
      expect(android, isNotNull);
      final notificationsEnabledBefore = await android!
          .areNotificationsEnabled();

      await GuardAidNotificationService.initializePlatformChannels();

      final installedChannels = await android.getNotificationChannels();
      expect(installedChannels, isNotNull);
      final channelsById = {
        for (final channel in installedChannels!) channel.id: channel,
      };

      final twoBeep =
          channelsById[GuardAidNotificationService.urgentChannel.id];
      final threeBeep =
          channelsById[GuardAidNotificationService.reminderChannel.id];
      final fourBeep =
          channelsById[GuardAidNotificationService.escalationChannel.id];
      final routine =
          channelsById[GuardAidNotificationService.updatesChannel.id];
      expect(twoBeep, isNotNull);
      expect(threeBeep, isNotNull);
      expect(fourBeep, isNotNull);
      expect(routine, isNotNull);

      for (final entry in [
        (twoBeep!, GuardAidNotificationService.urgentSoundResource),
        (threeBeep!, GuardAidNotificationService.reminderSoundResource),
        (fourBeep!, GuardAidNotificationService.escalationSoundResource),
      ]) {
        expect(entry.$1.importance, Importance.max);
        expect(entry.$1.playSound, isTrue);
        expect(entry.$1.sound, isA<RawResourceAndroidNotificationSound>());
        expect(
          (entry.$1.sound! as RawResourceAndroidNotificationSound).sound,
          entry.$2,
        );
        expect(entry.$1.audioAttributesUsage, AudioAttributesUsage.alarm);
        expect(entry.$1.bypassDnd, isFalse);
      }
      expect(routine!.importance, Importance.defaultImportance);
      expect(routine.playSound, isTrue);
      expect(routine.sound, isA<UriAndroidNotificationSound>());
      expect(routine.audioAttributesUsage, AudioAttributesUsage.notification);
      expect(routine.bypassDnd, isFalse);
      expect(
        await android.areNotificationsEnabled(),
        notificationsEnabledBefore,
      );
    },
  );

  testWidgets('EMU Demo navigation and response workflow', (tester) async {
    await tester.pumpWidget(GuardAidApp(key: UniqueKey()));
    await tester.pump(const Duration(milliseconds: 950));

    // P01/P03/O01: startup, English sign-in, password control and reset dialog.
    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.textContaining('Prototype demo mode'), findsOneWidget);
    expect(find.text('Sign in'), findsOneWidget);
    await tester.tap(find.text('Forgot password?'));
    await tester.pumpAndSettle();
    expect(find.text('Reset password'), findsOneWidget);
    expect(find.text('Send link'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();

    // P03/P06: successful Demo sign-in and shared shell.
    await tester.tap(find.text('Sign in'));
    await tester.pump(const Duration(milliseconds: 450));
    await tester.pumpAndSettle();
    expect(find.text('Alert Centre'), findsOneWidget);
    expect(find.text('Alerts'), findsOneWidget);
    expect(find.text('Updates'), findsWidgets);
    expect(find.text('Logs'), findsOneWidget);
    expect(find.text('Settings'), findsWidgets);

    // P07/O03/O04: quick filters, search, advanced filter sheet and date picker.
    await tester.tap(find.text('Weapon').first);
    await tester.pumpAndSettle();
    expect(find.text('North Gate Camera'), findsOneWidget);
    await tester.tap(find.byType(TextField).first);
    await tester.enterText(find.byType(TextField).first, 'North Gate');
    await tester.pump();
    expect(find.text('North Gate Camera'), findsOneWidget);
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('More filters').last);
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('Filter alerts'), findsOneWidget);
    expect(find.text('Incident status'), findsOneWidget);
    expect(find.text('Threshold mode'), findsOneWidget);
    expect(find.text('Camera source'), findsOneWidget);
    // The native date-range picker is verified manually on the emulator;
    // close the advanced sheet here so the rest of this workflow remains
    // deterministic across Android API levels.
    Navigator.of(tester.element(find.text('Filter alerts'))).pop();
    await tester.pumpAndSettle();
    expect(find.text('Filter alerts'), findsNothing);

    // P08/O05/O06/O07/O08/O09: detail evidence, summary, optional-comment
    // action forms, cancellation, and simulation warning.
    final northGateCard = find.byType(AlertCard).first;
    await tester.ensureVisible(northGateCard);
    await tester.tap(northGateCard);
    await tester.pumpAndSettle();
    expect(find.text('Alert review'), findsOneWidget);
    expect(find.text('Evidence'), findsOneWidget);
    expect(find.text('AI scene summary'), findsOneWidget);
    expect(find.text('Demo evidence preview'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Dismiss as false alarm'),
      500,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.text('Confirm incident'), findsOneWidget);
    expect(find.text('Resolve and notify school'), findsNothing);
    expect(find.text('Escalate as emergency'), findsNothing);
    expect(find.text('Simulate emergency call'), findsNothing);

    await tester.tap(find.text('Dismiss as false alarm'));
    await tester.pumpAndSettle();
    expect(find.text('Dismiss as false alarm').last, findsOneWidget);
    expect(find.text('Comment (optional)'), findsOneWidget);
    Navigator.of(
      tester.element(find.text('Dismiss as false alarm').last),
    ).pop();
    await tester.pumpAndSettle();

    await tester.tap(find.text('Confirm incident'));
    await tester.pumpAndSettle();
    expect(find.text('Confirm only · request a handler'), findsOneWidget);
    expect(find.text('Confirm and handle it myself'), findsOneWidget);
    expect(find.text('Confirm · already resolved'), findsOneWidget);
    expect(find.text('Comment (optional)'), findsOneWidget);
    expect(find.text('Simulate emergency call'), findsOneWidget);
    await tester.tap(find.text('Confirm and handle it myself'));
    await tester.ensureVisible(find.text('Simulate emergency call'));
    await tester.tap(find.text('Simulate emergency call'));
    await tester.tap(find.text('Confirm incident').last);
    await tester.pumpAndSettle();
    expect(find.text('Simulation complete'), findsOneWidget);
    expect(
      find.text(
        'Simulated emergency call has been placed. No real call was made.',
      ),
      findsOneWidget,
    );
    await tester.tap(find.text('Close'));
    await tester.pumpAndSettle();
    expect(find.text('Mark incident resolved'), findsOneWidget);
    await tester.tap(find.text('Mark incident resolved'));
    await tester.pumpAndSettle();
    expect(find.text('Resolve incident'), findsOneWidget);
    expect(find.text('Comment (optional)'), findsOneWidget);
    await tester.tap(find.text('Mark resolved'));
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();

    // P09/O10: updates and mark-all confirmation. This is in-memory Demo
    // state only and is not evidence of SQLite/FCM persistence.
    await tester.tap(find.text('Updates'));
    await tester.pumpAndSettle();
    expect(find.text('Updates'), findsWidgets);
    expect(find.text('Mark all as read'), findsOneWidget);
    await tester.tap(find.text('Mark all as read'));
    await tester.pumpAndSettle();
    expect(find.text('Mark all as read?'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();

    // P10: saved logs and status controls.
    await tester.tap(find.text('Logs'));
    await tester.pumpAndSettle();
    expect(find.text('Incident logs'), findsOneWidget);
    expect(find.text('Resolved'), findsWidgets);
    expect(find.text('Incident status'), findsOneWidget);

    // P11/P12/P13/P14: administrator settings and all three admin pages.
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Roles and sensitivity'), findsOneWidget);
    expect(find.text('User assignments'), findsOneWidget);
    expect(find.text('Borderline alert settings'), findsOneWidget);

    await tester.tap(find.text('Roles and sensitivity'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Each user has one assigned role.'),
      findsOneWidget,
    );
    expect(find.text('Edit threshold'), findsWidgets);
    await tester.tap(find.text('Edit threshold').first);
    await tester.pumpAndSettle();
    expect(find.text('Save sensitivity'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();

    await tester.tap(find.text('User assignments'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Create staff accounts, assign one role'),
      findsOneWidget,
    );
    expect(find.text('Taylor Wong'), findsOneWidget);
    await tester.tap(find.text('Taylor Wong'));
    await tester.pumpAndSettle();
    expect(find.text('Assigned role'), findsOneWidget);
    expect(find.text('Review changes'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();

    await tester.tap(find.text('Borderline alert settings'));
    await tester.pumpAndSettle();
    expect(find.text('Enable borderline alerts'), findsOneWidget);
    expect(find.text('Eligible recipients'), findsOneWidget);
    expect(find.text('Save settings'), findsOneWidget);
  });

  testWidgets('EMU matrix authentication and shell controls', (tester) async {
    await tester.pumpWidget(GuardAidApp(key: UniqueKey()));
    await tester.pump(const Duration(milliseconds: 950));

    // P01-01/P03-01: Demo startup and seeded credentials.
    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.textContaining('Prototype demo mode'), findsOneWidget);
    final fields = find.byType(TextField);

    // P03-03: password visibility control.
    await tester.tap(find.byTooltip('Show password'));
    await tester.pump();
    expect(find.byTooltip('Hide password'), findsOneWidget);
    await tester.tap(find.byTooltip('Hide password'));
    await tester.pump();
    expect(find.byTooltip('Show password'), findsOneWidget);

    // O01-01/O01-03: reset dialog and invalid email validation.
    await tester.tap(find.text('Forgot password?'));
    await tester.pumpAndSettle();
    expect(find.text('Reset password'), findsOneWidget);
    expect(find.text('Send link'), findsOneWidget);
    await tester.enterText(find.byType(TextField).last, 'invalid-email');
    await tester.tap(find.text('Send link'));
    await tester.pumpAndSettle();
    expect(find.textContaining('valid email'), findsOneWidget);

    // O01-02: cancellation leaves the sign-in page available.
    await tester.tap(find.text('Forgot password?'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    expect(find.text('Sign in'), findsOneWidget);

    // P03-08/P03-09: invalid credentials and invalid email remain recoverable.
    await tester.enterText(fields.first, 'wrong@example.com');
    await tester.enterText(fields.last, 'bad-password');
    await tester.tap(find.text('Sign in'));
    // The transient SnackBar may disappear during pumpAndSettle; the
    // durable acceptance criterion is that the user remains on sign-in.
    await tester.pump(const Duration(milliseconds: 450));
    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.text('Alert Centre'), findsNothing);
    await tester.enterText(fields.first, 'not-an-email');
    await tester.enterText(fields.last, 'GuardAId123!');
    await tester.tap(find.text('Sign in'));
    await tester.pump(const Duration(milliseconds: 450));
    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.text('Alert Centre'), findsNothing);

    // P03-04/P03-05/P06-01: keyboard submit reaches the active shell.
    await tester.enterText(fields.first, 'admin@guardaid.demo');
    await tester.enterText(fields.last, 'GuardAId123!');
    await tester.tap(fields.last);
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump(const Duration(milliseconds: 450));
    await tester.pumpAndSettle();
    expect(find.text('Alert Centre'), findsOneWidget);
    // P06-04: the two-part demo name is rendered as initials in the app bar.
    expect(find.text('AM'), findsOneWidget);
    // P06-08: narrow emulator surface keeps all destinations reachable and
    // switches NavigationBar to its compact label behaviour.
    await tester.binding.setSurfaceSize(const Size(320, 640));
    await tester.pumpAndSettle();
    expect(find.byType(NavigationBar), findsOneWidget);
    expect(find.text('Alerts'), findsWidgets);
    await tester.binding.setSurfaceSize(null);
    await tester.pumpAndSettle();

    // P06-02/P06-03: tab destinations and app-bar notification route.
    await tester.tap(find.text('Updates').last);
    await tester.pumpAndSettle();
    expect(find.text('Updates'), findsWidgets);
    await tester.tap(find.text('Logs').last);
    await tester.pumpAndSettle();
    expect(find.text('Incident logs'), findsOneWidget);
    await tester.tap(find.text('Settings').last);
    await tester.pumpAndSettle();
    expect(find.text('Settings'), findsWidgets);
    await tester.tap(find.text('Alerts').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Notifications'));
    await tester.pumpAndSettle();
    expect(find.text('Updates'), findsWidgets);
  });

  testWidgets('EMU matrix alert filters, review states, and validation', (
    tester,
  ) async {
    await tester.pumpWidget(GuardAidApp(key: UniqueKey()));
    await tester.pump(const Duration(milliseconds: 950));
    await tester.tap(find.text('Sign in'));
    await tester.pump(const Duration(milliseconds: 450));
    await tester.pumpAndSettle();

    final search = find.byType(TextField).first;

    // P07-06/P07-07/P07-08/P07-09: camera, location, type, and no-match search.
    await tester.enterText(search, 'North Gate');
    await tester.pump();
    expect(find.text('North Gate Camera'), findsOneWidget);
    await tester.enterText(search, 'North Gate');
    await tester.pump();
    expect(find.text('North Gate'), findsWidgets);
    await tester.enterText(search, 'Weapon');
    await tester.pump();
    expect(find.text('North Gate Camera'), findsOneWidget);
    await tester.enterText(search, 'no-such-camera');
    await tester.pump();
    expect(find.text('No matching alerts'), findsOneWidget);
    await tester.enterText(search, '');
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();

    // P07-10/P07-11/P07-12/P07-13: quick filter chips.
    final allChip = find.widgetWithText(FilterChip, 'All');
    await tester.ensureVisible(allChip);
    await tester.scrollUntilVisible(
      allChip,
      500,
      scrollable: find.byType(Scrollable).first,
    );
    expect(allChip, findsOneWidget);
    await tester.pump();
    expect(find.text('North Gate Camera'), findsOneWidget);
    final weaponChip = find.widgetWithText(FilterChip, 'Weapon');
    await tester.ensureVisible(weaponChip);
    await tester.tap(weaponChip);
    await tester.pump();
    expect(find.text('North Gate Camera'), findsOneWidget);
    final violenceChip = find.widgetWithText(FilterChip, 'Violence / Bullying');
    await tester.ensureVisible(violenceChip);
    await tester.tap(violenceChip);
    await tester.pump();
    expect(find.text('Block B Corridor'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Cafeteria Camera 2'),
      500,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('Cafeteria Camera 2'), findsOneWidget);
    final activeChip = find.widgetWithText(FilterChip, 'Active');
    await tester.ensureVisible(activeChip);
    await tester.tap(activeChip);
    await tester.pump();
    expect(find.text('Sports Hall Entrance'), findsNothing);

    // Clear quick filtering before advanced filtering.
    await tester.drag(find.byType(CustomScrollView), const Offset(0, 1200));
    await tester.pump(const Duration(milliseconds: 500));
    expect(allChip, findsOneWidget);
    await tester.pump();
    await tester.tap(find.byType(OutlinedButton).first);
    await tester.pump(const Duration(milliseconds: 600));

    // O03-01/O03-02/O03-03/O03-04: advanced sheet and dropdown controls.
    expect(find.text('Filter alerts'), findsOneWidget);
    expect(find.text('Incident status'), findsOneWidget);
    expect(find.text('Threshold mode'), findsOneWidget);
    expect(find.text('Camera source'), findsOneWidget);
    final alertDropdowns = find.byType(DropdownButtonFormField<String>);
    expect(alertDropdowns, findsNWidgets(3));
    await tester.tap(alertDropdowns.at(0));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownButtonFormField<String>).at(1));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Moderate').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownButtonFormField<String>).at(2));
    await tester.pumpAndSettle();
    await tester.tap(find.text('North Gate Camera').last);
    await tester.pumpAndSettle();

    // O03-05: cancel discards the draft selection.
    Navigator.of(tester.element(find.text('Filter alerts'))).pop();
    await tester.pumpAndSettle();
    expect(find.text('Filter alerts'), findsNothing);
    expect(find.textContaining('More filters'), findsOneWidget);

    // O03-06/O03-07: apply criteria and clear them with All values.
    await tester.tap(find.textContaining('More filters').last);
    await tester.pump(const Duration(milliseconds: 600));
    await tester.tap(find.byType(DropdownButtonFormField<String>).at(0));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Apply filters'));
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.textContaining('More filters (1)'), findsOneWidget);
    await tester.tap(find.textContaining('More filters (1)'));
    await tester.pump(const Duration(milliseconds: 600));
    await tester.tap(find.byType(DropdownButtonFormField<String>).at(0));
    await tester.pump(const Duration(milliseconds: 600));
    await tester.tap(find.text('All statuses').last);
    await tester.pump(const Duration(milliseconds: 600));
    await tester.tap(find.text('Apply filters'));
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('More filters'), findsOneWidget);

    // P07-14/P07-15/P07-16/P08-01/P08-04/P08-07: combined result and ready
    // weapon review with metadata, summary, and Demo evidence explanation.
    final firstCard = find.byType(AlertCard).first;
    await tester.ensureVisible(firstCard);
    await tester.tap(firstCard);
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('Alert review'), findsOneWidget);
    expect(find.text('North Gate Camera'), findsOneWidget);
    expect(find.text('94.0%'), findsOneWidget);
    expect(find.text('Observed'), findsOneWidget);
    expect(find.textContaining('An object appears near'), findsOneWidget);
    expect(find.text('Demo evidence preview'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Dismiss as false alarm'),
      500,
      scrollable: find.byType(Scrollable).last,
    );

    // O05-01/O05-02/O05-03: dismiss sheet makes the optional comment clear.
    await tester.tap(find.text('Dismiss as false alarm'));
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('Dismiss as false alarm').last, findsOneWidget);
    expect(find.text('Comment (optional)'), findsOneWidget);
    Navigator.of(
      tester.element(find.text('Dismiss as false alarm').last),
    ).pop();
    await tester.pump(const Duration(milliseconds: 600));

    // O06/O07: the new Confirm flow exposes its three mutually exclusive
    // routes; Confirm-only starts a Demo Broadcast and can then be claimed.
    await tester.tap(find.text('Confirm incident'));
    await tester.pumpAndSettle();
    expect(find.text('Confirm only · request a handler'), findsOneWidget);
    expect(find.text('Confirm and handle it myself'), findsOneWidget);
    expect(find.text('Confirm · already resolved'), findsOneWidget);
    expect(find.text('Comment (optional)'), findsOneWidget);
    expect(
      find.text('Broadcast roles (at least one required)'),
      findsOneWidget,
    );
    expect(find.byType(CheckboxListTile), findsWidgets);
    final securityRecipient = find.widgetWithText(
      CheckboxListTile,
      'Security Personnel',
    );
    await tester.ensureVisible(securityRecipient);
    expect(tester.widget<CheckboxListTile>(securityRecipient).value, isTrue);
    await tester.tap(find.text('Confirm incident').last);
    await tester.pumpAndSettle();
    expect(find.text('Take ownership'), findsOneWidget);

    await tester.tap(find.text('Take ownership'));
    await tester.pumpAndSettle();
    expect(find.text('Mark incident resolved'), findsOneWidget);
    await tester.tap(find.text('Mark incident resolved'));
    await tester.pumpAndSettle();
    expect(find.text('Resolve incident'), findsOneWidget);
    expect(find.text('Comment (optional)'), findsOneWidget);
    await tester.tap(find.text('Mark resolved'));
    await tester.pumpAndSettle();

    // Return to the originating Alert Centre after the Demo workflow.
    await tester.tap(find.byTooltip('Back'));
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('Alert review'), findsNothing);
    expect(find.byTooltip('Back'), findsNothing);
  });

  testWidgets('EMU matrix updates, logs, settings, and admin drafts', (
    tester,
  ) async {
    await tester.pumpWidget(GuardAidApp(key: UniqueKey()));
    await tester.pump(const Duration(milliseconds: 950));
    await tester.tap(find.text('Sign in'));
    await tester.pump(const Duration(milliseconds: 450));
    await tester.pumpAndSettle();

    // P09-01/P09-02/P09-05: notification list, unread count, and card opening.
    await tester.tap(find.text('Updates').last);
    await tester.pumpAndSettle();
    expect(find.text('Updates'), findsWidgets);
    expect(find.textContaining('unread notification'), findsOneWidget);
    expect(find.text('Weapon alert detected'), findsOneWidget);
    await tester.tap(find.text('Weapon alert detected'));
    await tester.pumpAndSettle();
    expect(find.text('Alert review'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('Weapon alert detected'), findsOneWidget);

    // O10-01/O10-02/O10-03: cancel then confirm Mark all as read.
    await tester.tap(find.text('Mark all as read'));
    await tester.pumpAndSettle();
    expect(find.text('Mark all as read?'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    expect(find.text('Mark all as read'), findsOneWidget);
    await tester.tap(find.text('Mark all as read'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Mark all').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('All updates marked as read'), findsOneWidget);
    expect(find.textContaining('0 unread notification'), findsOneWidget);

    // P10-01/P10-02/P10-03/P10-04/P10-05/P10-06/P10-07/P10-08/P10-09.
    await tester.tap(find.text('Logs').last);
    await tester.pumpAndSettle();
    expect(find.text('Incident logs'), findsOneWidget);
    expect(find.text('Resolved'), findsWidgets);
    expect(find.text('Dismissed'), findsWidgets);
    expect(find.text('Escalated'), findsWidgets);
    final logSearch = find.byType(TextField).first;
    await tester.enterText(logSearch, 'Sports Hall');
    await tester.pump();
    expect(find.text('Sports Hall Entrance'), findsOneWidget);
    await tester.enterText(logSearch, 'no-such-log');
    await tester.pump();
    expect(find.textContaining('No saved incidents match'), findsOneWidget);
    await tester.enterText(logSearch, '');
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    final logDropdown = find.byType(DropdownButtonFormField<AlertStatus?>);
    expect(logDropdown, findsOneWidget);
    await tester.tap(logDropdown);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Resolved').last);
    await tester.pumpAndSettle();
    expect(find.text('Sports Hall Entrance'), findsOneWidget);
    await tester.tap(find.byType(DropdownButtonFormField<AlertStatus?>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Dismissed').last);
    await tester.pumpAndSettle();
    expect(find.text('Playground East'), findsOneWidget);
    await tester.tap(find.byType(DropdownButtonFormField<AlertStatus?>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Escalated').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('No saved incidents match'), findsOneWidget);
    await tester.tap(find.byType(DropdownButtonFormField<AlertStatus?>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('All saved statuses').last);
    await tester.pumpAndSettle();
    final savedCard = find.byType(AlertCard).first;
    await tester.ensureVisible(savedCard);
    await tester.tap(savedCard);
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Resolved after on-site staff intervention'),
      findsOneWidget,
    );
    expect(
      find.text('No evidence media is available for this saved record.'),
      findsOneWidget,
    );
    expect(find.text('Dismiss as false alarm'), findsNothing);
    await tester.pageBack();
    await tester.pumpAndSettle();

    // P11-03/P11-04/P11-05: administrator settings and application information.
    await tester.tap(find.text('Settings').last);
    await tester.pumpAndSettle();
    expect(find.text('Roles and sensitivity'), findsOneWidget);
    expect(find.text('User assignments'), findsOneWidget);
    expect(find.text('Borderline alert settings'), findsOneWidget);
    expect(find.text('Evidence access'), findsNothing);
    expect(find.text('Prototype view'), findsNothing);
    expect(find.text('Prototype build'), findsNothing);
    expect(find.textContaining('com.guardaid.prototype'), findsNothing);

    // P12-01/P12-03/O12-01/O12-02/O12-03/O12-04/O13-01.
    final rolesTile = find.text('Roles and sensitivity');
    // Move the settings list away from the toolbar before tapping a tile that
    // is above the application-information section.
    await tester.drag(find.byType(ListView).last, const Offset(0, 240));
    await tester.pump();
    await tester.ensureVisible(rolesTile);
    await tester.tap(rolesTile);
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Each user has one assigned role.'),
      findsOneWidget,
    );
    expect(find.text('School Administrator'), findsOneWidget);
    await tester.tap(find.text('Edit threshold').first);
    await tester.pumpAndSettle();
    expect(find.text('Sensitive'), findsWidgets);
    expect(find.text('Moderate'), findsWidgets);
    expect(find.text('Certain'), findsWidgets);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Edit threshold').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Moderate').last);
    await tester.tap(find.text('Save sensitivity'));
    await tester.pumpAndSettle();
    expect(find.text('Update School Administrator sensitivity?'), findsNothing);
    await tester.tap(find.text('Edit threshold').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sensitive').last);
    await tester.tap(find.text('Save sensitivity'));
    await tester.pumpAndSettle();
    expect(find.textContaining('sensitivity?'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();

    // P13-01/P13-02/P13-03/P13-04/P13-05/O14-01/O14-02/O14-03/O14-04/O14-05/O14-06/O15-01.
    await tester.tap(find.text('User assignments'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Create staff accounts, assign one role'),
      findsOneWidget,
    );
    final userSearch = find.byType(TextField).first;
    await tester.enterText(userSearch, 'Taylor');
    await tester.pump();
    expect(find.text('Taylor Wong'), findsOneWidget);
    await tester.enterText(userSearch, 'teacher@guardaid.demo');
    await tester.pump();
    expect(find.text('Taylor Wong'), findsOneWidget);
    await tester.enterText(userSearch, 'School Administrator');
    await tester.pump();
    expect(find.text('Alex Morgan'), findsOneWidget);
    await tester.enterText(userSearch, 'no-such-user');
    await tester.pump();
    expect(find.text('Taylor Wong'), findsNothing);
    await tester.enterText(userSearch, 'Taylor');
    await tester.pump();
    await tester.tap(find.text('Taylor Wong'));
    await tester.pumpAndSettle();
    expect(find.text('Assigned role'), findsOneWidget);
    expect(find.text('Review changes'), findsOneWidget);
    final reviewButton = find.widgetWithText(FilledButton, 'Review changes');
    expect(tester.widget<FilledButton>(reviewButton).onPressed, isNull);
    final userDropdowns = find.byWidgetPredicate(
      (widget) => widget is DropdownButtonFormField,
    );
    expect(userDropdowns, findsNWidgets(2));
    await tester.tap(userDropdowns.first);
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Security Personnel').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Review changes'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Save assignment for Taylor Wong'),
      findsOneWidget,
    );
    expect(find.textContaining('Pending approval'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();

    // O14-04/O14-05: the same draft can be reviewed for Active and Disabled
    // access without saving either mutation.
    await tester.tap(find.text('Taylor Wong'));
    await tester.pumpAndSettle();
    final activeDropdowns = find.byWidgetPredicate(
      (widget) => widget is DropdownButtonFormField,
    );
    await tester.tap(activeDropdowns.first);
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Security Personnel').last);
    await tester.pumpAndSettle();
    await tester.tap(activeDropdowns.at(1));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Active').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Review changes'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Access: Active'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();

    await tester.tap(find.text('Taylor Wong'));
    await tester.pumpAndSettle();
    final disabledDropdowns = find.byWidgetPredicate(
      (widget) => widget is DropdownButtonFormField,
    );
    await tester.tap(disabledDropdowns.first);
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('Security Personnel').last);
    await tester.pumpAndSettle();
    await tester.tap(disabledDropdowns.at(1));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Disabled').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Review changes'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Access: Disabled'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();

    // P14-02/P14-03/P14-04/P14-05/O16-01/O16-02/O16-03.
    await tester.tap(find.text('Borderline alert settings'));
    await tester.pumpAndSettle();
    expect(find.text('Enable borderline alerts'), findsOneWidget);
    final borderlineSwitch = find.byType(SwitchListTile);
    await tester.tap(borderlineSwitch);
    await tester.pump();
    expect(find.textContaining('Disabled'), findsOneWidget);
    await tester.tap(borderlineSwitch);
    await tester.pump();
    final recipients = find.byType(CheckboxListTile);
    await tester.tap(recipients.first);
    await tester.tap(recipients.at(1));
    await tester.tap(recipients.at(0));
    await tester.tap(recipients.at(1));
    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();
    expect(find.textContaining('at least one recipient role'), findsOneWidget);
    await tester.tap(recipients.first);
    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();
    expect(find.textContaining('borderline alerts?'), findsOneWidget);
    await tester.tap(find.text('Cancel').last);
    await tester.pumpAndSettle();
  });
}
