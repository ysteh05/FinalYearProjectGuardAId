import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;

import 'repository.dart';

/// The text shown to a signed-in person, plus the machine code that still has
/// to drive a UI branch.
class GuardAidFailureMessage {
  const GuardAidFailureMessage(this.message, {this.code});

  final String message;
  final String? code;

  @override
  String toString() => code == null ? message : '$message [$code]';
}

/// Stable text used when no operation-specific sentence is supplied.
const guardAidGenericFailure =
    'Unable to complete the request. Please try again.';
const guardAidNetworkFailure =
    'Unable to connect. Check your internet connection and try again.';
const guardAidTimeoutFailure = 'The request timed out. Please try again.';
const guardAidSessionExpired = 'Your session has expired. Sign in again.';
const guardAidPermissionDenied =
    'You do not have permission to do that. Ask an administrator if you need access.';
const guardAidNotFound =
    'This item is no longer available. Refresh and try again.';
const guardAidConflict =
    'This information changed while you were working. Review the latest details and try again.';
const guardAidValidation =
    'Some of the details are not valid. Check them and try again.';
const guardAidRateLimited = 'Too many requests. Wait a moment and try again.';
const guardAidServerUnavailable =
    'GuardAId is temporarily unavailable. Please try again shortly.';
const guardAidIncompleteResponse =
    'GuardAId returned an incomplete response. Please try again.';
const guardAidDeviceFailure =
    'This device could not complete the request. Please try again.';
const guardAidStartupFailure =
    'GuardAId could not finish starting. Check the device configuration and '
    'try again. If this keeps happening, contact your administrator.';

final _machineCodePattern = RegExp(r'^[a-z][a-z0-9_]{2,63}$');
final _statusCodePattern = RegExp(r'\b[1-5]\d{2}\b');
final _endsLikeSentence = RegExp(r'[.!?]$');
final _startsWithWord = RegExp(r'^[A-Za-z0-9]');

/// Fragments that only appear in exception text, transport dumps, parser
/// output or absolute paths. A user-facing sentence never needs them.
const _unsafeFragments = <String>[
  'exception',
  'stack trace',
  'traceback',
  'bad state',
  'dev.flutter',
  'pigeon',
  'socket',
  'platform',
  'clientexception',
  'checkpoint',
  'file://',
  'http://',
  'https://',
  'package:',
  '.dart',
  'asynchronous suspension',
  'at character',
  'at offset',
  'null check',
  'json',
  'typeerror',
  'nosuchmethod',
  'future<',
  'error:',
  'stacktrace',
];

bool looksLikeMachineCode(String? value) =>
    value != null && _machineCodePattern.hasMatch(value.trim());

/// True only for a short, single-line sentence that is safe to show as-is.
///
/// This deliberately rejects the old `GuardAId server returned 400.` text,
/// snake_case codes, JSON/HTML fragments, absolute paths and parser output.
bool looksLikeSafeUserMessage(String? value) =>
    _isSafeText(value, requireSentenceEnd: true);

/// Service-provided business text often omits the final full stop. Accept it
/// when it is otherwise safe and normalise the punctuation for display.
String? readableServiceDetail(String? value) {
  final text = value?.trim();
  if (!_isSafeText(text, requireSentenceEnd: false)) return null;
  if (text == null) return null;
  return _endsLikeSentence.hasMatch(text) ? text : '$text.';
}

bool _isSafeText(String? value, {required bool requireSentenceEnd}) {
  final text = value?.trim();
  if (text == null || text.length < 4 || text.length > 200) return false;
  if (text.contains('\n') || text.contains('\r')) return false;
  final lower = text.toLowerCase();
  if (_unsafeFragments.any(lower.contains)) return false;
  if (_statusCodePattern.hasMatch(text)) return false;
  if (text.contains('{') ||
      text.contains('}') ||
      text.contains('[') ||
      text.contains(']') ||
      text.contains('"')) {
    return false;
  }
  if (text.contains('_') || text.contains('/') || text.contains(r'\')) {
    return false;
  }
  if (!_startsWithWord.hasMatch(text)) return false;
  if (requireSentenceEnd && !_endsLikeSentence.hasMatch(text)) return false;
  return true;
}

/// Machine codes that a screen still branches on. They are never displayed.
const identityMachineCodes = <String>{
  'firebase_identity_exists',
  'firebase_identity_disabled',
  'firebase_identity_not_found',
  'user_already_registered',
  'account_created_but_local_setup_failed',
};

String _messageForStatus(int status, String? safeDetail, String fallback) {
  if (status == 401) return guardAidSessionExpired;
  if (status == 429) return guardAidRateLimited;
  if (status >= 500) return guardAidServerUnavailable;
  final detail = readableServiceDetail(safeDetail);
  return switch (status) {
    403 => detail ?? guardAidPermissionDenied,
    404 => detail ?? guardAidNotFound,
    409 => detail ?? guardAidConflict,
    422 => detail ?? guardAidValidation,
    >= 400 => detail ?? fallback,
    _ => detail ?? fallback,
  };
}

/// Converts any failure raised by the controller into public text.
///
/// The original exception is not returned; callers keep their own diagnostics.
GuardAidFailureMessage describeUserFacingError(
  Object error, {
  String fallbackMessage = guardAidGenericFailure,
}) {
  if (error is GuardAidConflictException) {
    final message =
        readableServiceDetail(error.message) ?? guardAidConflict;
    return GuardAidFailureMessage(message, code: error.code);
  }
  if (error is GuardAidRequestException) {
    return GuardAidFailureMessage(
      _messageForStatus(error.statusCode, error.message, fallbackMessage),
      code: error.code,
    );
  }
  if (error is TimeoutException) {
    // Only a sentence that already reads like product copy is kept; a raw
    // "Future not completed" or plugin text is replaced.
    final message = error.message;
    if (looksLikeSafeUserMessage(message)) {
      return GuardAidFailureMessage(message!.trim());
    }
    return const GuardAidFailureMessage(guardAidTimeoutFailure);
  }
  if (error is SocketException ||
      error is http.ClientException ||
      error is HandshakeException ||
      error is TlsException ||
      error is HttpException) {
    return const GuardAidFailureMessage(guardAidNetworkFailure);
  }
  if (error is PlatformException) {
    // The message/plugin payload is diagnostic only.
    return const GuardAidFailureMessage(guardAidDeviceFailure);
  }
  if (error is TypeError ||
      error is NoSuchMethodError ||
      error is RangeError ||
      error is IndexError) {
    return const GuardAidFailureMessage(guardAidIncompleteResponse);
  }
  if (error is FormatException) {
    final message = error.message;
    if (looksLikeSafeUserMessage(message)) {
      return GuardAidFailureMessage(message.trim());
    }
    return const GuardAidFailureMessage(guardAidIncompleteResponse);
  }
  // StateError carries deliberate controller sentences; it never carries a
  // parser or transport dump. Anything else keeps the caller's context.
  if (error is StateError && looksLikeSafeUserMessage(error.message)) {
    return GuardAidFailureMessage(error.message.trim());
  }
  return GuardAidFailureMessage(fallbackMessage);
}

/// Public text for a start-up failure. The raw error stays available for the
/// labelled technical-details section on the error screen.
String describeStartupFailure(Object error) => guardAidStartupFailure;

/// Reads an HTTP failure body and returns text that can be shown safely.
///
/// Supports a plain string detail, an object detail with `message`/`code`, a
/// validation array, a missing detail and a non-JSON body.
GuardAidFailureMessage describeApiFailure({
  required int statusCode,
  required String body,
}) {
  String? detailText;
  String? code;
  if (body.trim().isNotEmpty) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map) {
        final rawCode = decoded['code'];
        if (rawCode is String && rawCode.trim().isNotEmpty) {
          code = rawCode.trim();
        }
        final detail = decoded['detail'];
        if (detail is String) {
          detailText = detail;
        } else if (detail is Map) {
          final message = detail['message'];
          if (message is String) detailText = message;
          final detailCode = detail['code'];
          if (detailCode is String && detailCode.trim().isNotEmpty) {
            code ??= detailCode.trim();
          }
        } else if (detail is List) {
          detailText = _validationSummary(detail);
        }
      }
    } catch (_) {
      // Non-JSON body (HTML error page, empty body, truncated payload).
    }
  }
  if (detailText != null && looksLikeMachineCode(detailText)) {
    code ??= detailText.trim();
    detailText = null;
  }
  return GuardAidFailureMessage(
    _messageForStatus(statusCode, detailText, guardAidGenericFailure),
    code: code ?? (statusCode == 409 ? 'workflow_changed' : null),
  );
}

const _fieldLabels = <String, String>{
  'weapon': 'Weapon settings',
  'violence': 'Violence setting',
  'vlm': 'VLM settings',
  'nRequired': 'Weapon N',
  'mWindow': 'Weapon M window',
  'sampleFps': 'Weapon sample rate',
  'segmentSeconds': 'Violence segment',
  'strideSeconds': 'Violence stride',
  'detectorInputFrames': 'Detector input frames',
  'inputFps': 'VLM input rate',
  'maxOutputTokens': 'VLM output limit',
  'configVersion': 'Configuration version',
  'displayName': 'Display name',
  'email': 'Email address',
  'roleId': 'Role',
};

String? _validationSummary(List<dynamic> detail) {
  final collected = <({String message, Object? loc, bool business})>[];
  for (final item in detail) {
    if (item is! Map) continue;
    final message = item['msg'];
    if (message is! String) continue;
    final isBusiness =
        item['type'] == 'value_error' ||
        message.trimLeft().toLowerCase().startsWith('value error,');
    final cleaned = message.replaceFirst(RegExp(r'^Value error,\s*'), '').trim();
    if (cleaned.isEmpty || cleaned.length > 180) continue;
    final lower = cleaned.toLowerCase();
    if (_unsafeFragments.any(lower.contains)) continue;
    if (cleaned.contains('{') || cleaned.contains('}')) continue;
    collected.add((message: cleaned, loc: item['loc'], business: isBusiness));
  }
  if (collected.isEmpty) return null;
  final chosen = collected.firstWhere(
    (item) => item.business,
    orElse: () => collected.first,
  );
  final label = _fieldLabel(chosen.loc);
  if (label != null &&
      !chosen.message.toLowerCase().startsWith(label.toLowerCase())) {
    return '$label: ${chosen.message}';
  }
  return chosen.message;
}

String? _fieldLabel(Object? loc) {
  if (loc is! List) return null;
  for (final part in loc.reversed) {
    if (part is! String) continue;
    final label = _fieldLabels[part];
    if (label != null) return label;
  }
  return null;
}
