import 'dart:async';
import 'dart:io';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:guardaid_mobile/src/controller.dart';
import 'package:guardaid_mobile/src/firebase_api_repository.dart';
import 'package:guardaid_mobile/src/screens/alerts_screen.dart';
import 'package:guardaid_mobile/src/theme.dart';
import 'package:guardaid_mobile/src/workflow_requests.dart';

class _RealHttp extends HttpOverrides {}

class _User extends Fake implements User {
  @override
  Future<String?> getIdToken([bool forceRefresh=false]) async => 'admin-token';
}
class _Auth extends Fake implements FirebaseAuth {
  @override
  User get currentUser => _User();
}
class _Messaging extends Fake implements FirebaseMessaging {}
class _Repository extends FirebaseApiGuardAidRepository {
  _Repository(http.Client client) : super(baseUrl:'http://127.0.0.1:18843',
    auth:_Auth(),messaging:_Messaging(),client:client);
  @override
  Stream<String> get openedAlertIds => const Stream.empty();
}
void main() {
  const enabled = bool.fromEnvironment('CORRECTNESS_HTTP');
  testWidgets('synthetic SQLite HTTP camera count reaches the real mobile widget', (tester) async {
    late http.Client client;
    late GuardAidController controller;
    await tester.runAsync(() async {
      await HttpOverrides.runWithHttpOverrides(() async {
        client=http.Client(); final repo=_Repository(client);
        controller=GuardAidController(repo,workflowRequestStore:MemoryWorkflowRequestStore());
        final count=await repo.loadMonitoredCameraCount();
        expect(count,greaterThanOrEqualTo(2));
        controller.monitoredCameraCount=count;
      },_RealHttp());
    });
    await tester.pumpWidget(MaterialApp(theme:buildGuardAidTheme(),home:Scaffold(body:AlertsScreen(controller:controller))));
    await tester.pump();
    expect(find.text('Monitored cameras'),findsOneWidget);
    expect(find.text('${controller.monitoredCameraCount}'),findsWidgets);
    expect(tester.takeException(),isNull);
    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose(); client.close();
  }, skip: !enabled);
}
