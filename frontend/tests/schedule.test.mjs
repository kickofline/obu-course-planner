import test from 'node:test';
import assert from 'node:assert/strict';
import { reviewSelections } from '../src/scripts/courses/schedule.js';

const offerings = {
  semesters: [{ term_id: '2027SP', label: 'Spring 2027' }],
  courses: {
    'ART 4062': [{ term_id: '2027SP', seq: '849' }, { term_id: '2026FA', seq: '848' }],
    'COMM 2053': [{ term_id: '2027SP', seq: '848' }],
    'MATH 1003': [{ term_id: '2027SP', seq: '001' }],
  },
};

test('flags a removed choice even when another section, semester, or course has a similar identity; never replaces it', () => {
  const entries = [Object.freeze({ code: 'ART 4062', seq: '848' })];
  const review = reviewSelections(entries, '2027SP', offerings);
  assert.deepEqual(review.issues.map(({ code, severity, kind }) => ({ code, severity, kind })), [
    { code: 'ART 4062', severity: 'error', kind: 'missing-section' },
  ]);
  assert.equal(entries[0].seq, '848');
  assert.deepEqual(reviewSelections([{ code: 'ART 4062', seq: '849' }], '2027SP', offerings).issues, []);
});

test('an uncached historical or future semester is unvalidated, not a removed course', () => {
  for (const term of ['2026SP', '2028FA', 'TR']) {
    assert.deepEqual(reviewSelections([{ code: 'ART 4062', seq: '848' }], term, offerings), { semester: null, issues: [] });
  }
});

test('distinguishes a missing course from an available course still needing a section; custom entries are exempt', () => {
  const review = reviewSelections([{ code: 'ART 4062' }, { code: 'HIST 1003' }, { code: 'TEST 9999', custom: true }], '2027SP', offerings);
  assert.deepEqual(review.issues.map(({ code, severity, kind }) => ({ code, severity, kind })), [
    { code: 'ART 4062', severity: 'warn', kind: 'unselected' },
    { code: 'HIST 1003', severity: 'error', kind: 'missing-course' },
  ]);
});

test('matches exact section numbers, including leading zeros', () => {
  assert.equal(reviewSelections([{ code: 'MATH 1003', seq: '1' }], '2027SP', offerings).issues[0].kind, 'missing-section');
  assert.deepEqual(reviewSelections([{ code: 'MATH 1003', seq: '001' }], '2027SP', offerings).issues, []);
});
