const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  describeHttpFailure, describeError, describeJobFailure, isSafeUserMessage,
} = require('../app/static/error_messages.js');

test('safe business detail is kept', () => {
  assert.equal(describeHttpFailure(400, 'Bad Request', 'Enter a valid display name.').message, 'Enter a valid display name.');
  assert.equal(describeHttpFailure(403, 'Forbidden', 'School Administrator access is required').message, 'School Administrator access is required.');
});

test('the old status phrase is never echoed', () => {
  for (const detail of ['400 Bad Request', 'GuardAId server returned 400.', undefined]) {
    const message = describeHttpFailure(400, 'Bad Request', detail).message;
    assert.doesNotMatch(message, /400|server returned/);
  }
});

test('status families map to actionable sentences', () => {
  assert.match(describeHttpFailure(401, 'Unauthorized', undefined).message, /session has expired/i);
  assert.match(describeHttpFailure(403, 'Forbidden', undefined).message, /do not have permission/i);
  assert.match(describeHttpFailure(404, 'Not Found', undefined).message, /no longer available/i);
  assert.match(describeHttpFailure(409, 'Conflict', undefined).message, /changed while you were working/i);
  assert.match(describeHttpFailure(422, 'Unprocessable Entity', undefined).message, /details are not valid/i);
  assert.match(describeHttpFailure(429, 'Too Many Requests', undefined).message, /too many requests/i);
  assert.match(describeHttpFailure(500, 'Internal Server Error', 'RuntimeError: checkpoint missing at C:\\models\\mc3.pt').message, /temporarily unavailable/i);
});

test('object detail keeps its message and code', () => {
  const failure = describeHttpFailure(409, 'Conflict', {
    code: 'config_version_conflict',
    message: 'Configuration changed since you started editing. Review the latest settings before saving.',
  });
  assert.equal(failure.code, 'config_version_conflict');
  assert.match(failure.message, /configuration changed/i);
});

test('validation array explains the concrete input problem', () => {
  const failure = describeHttpFailure(422, 'Unprocessable Entity', [
    { loc: ['body', 'weapon', 'nRequired'], msg: 'Input should be a valid integer', type: 'int_parsing' },
    { loc: ['body', 'weapon'], msg: 'Value error, Weapon N must be less than or equal to M.', type: 'value_error' },
  ]);
  assert.match(failure.message, /Weapon N must be less than or equal to M/);
  assert.doesNotMatch(failure.message, /422|Value error/);
});

test('validation array explains a stride problem', () => {
  const failure = describeHttpFailure(422, 'Unprocessable Entity', [
    { loc: ['body', 'violence'], msg: 'Value error, Violence stride cannot exceed segment duration.', type: 'value_error' },
  ]);
  assert.match(failure.message, /stride cannot exceed segment duration/);
});

test('machine code detail is kept out of the visible message', () => {
  const failure = describeHttpFailure(409, 'Conflict', 'firebase_identity_exists');
  assert.equal(failure.code, 'firebase_identity_exists');
  assert.doesNotMatch(failure.message, /firebase_identity/);
});

test('fetch and abort failures become readable', () => {
  assert.match(describeError(new TypeError('Failed to fetch')), /unable to reach/i);
  const abort = new Error('aborted');
  abort.name = 'AbortError';
  assert.match(describeError(abort), /timed out/i);
  assert.equal(describeError(new Error('boom'), 'The request could not be completed. Please try again.'), 'The request could not be completed. Please try again.');
});

test('job failures never leak the raw exception or path', () => {
  const raw = 'RuntimeError: checkpoint missing at C:\\models\\mc3.pt';
  const message = describeJobFailure(raw, 'failed', 0);
  assert.match(message, /detection model is unavailable/i);
  assert.doesNotMatch(message, /RuntimeError|checkpoint|C:\\/);
  assert.match(describeJobFailure('Camera not found', 'failed', 0), /camera is no longer available/i);
  assert.match(describeJobFailure('ValueError: something odd', 'failed', 42), /Analysis stopped/);
});

test('safety predicate rejects technical text', () => {
  for (const value of ['422 Unprocessable Entity', 'firebase_identity_exists', 'Bad state: No element', '{"detail":"bad"}', 'Failed to fetch']) {
    assert.equal(isSafeUserMessage(value), false, value);
  }
  assert.equal(isSafeUserMessage('Alert not found.'), true);
});
