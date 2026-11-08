import 'package:flutter_test/flutter_test.dart';
import 'package:guardaid_mobile/src/repository.dart';

void main() {
  test('the offline demo does not ship a default password', () async {
    final repository = DemoGuardAidRepository();
    await expectLater(repository.signIn('admin@example.test', ''),
        throwsA(isA<FormatException>()));
    expect((await repository.refreshSession()).email, 'admin@example.test');
  });
}
