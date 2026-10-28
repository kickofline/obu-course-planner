import test from 'node:test';
import assert from 'node:assert/strict';
import { encodeShare, decodeShare, restoreShare } from '../src/scripts/courses/plan.js';

const offerings = {
  semesters: [{ term_id: '2027SP' }, { term_id: '2026FA' }],
  courses: {
    'MATH 1003': [{ term_id: '2027SP', seq: '001', hours: 3 }],
    'PHYS 2030': [{ term_id: '2027SP', seq: '25', hours: 0 }],
    'HIST 1013': [{ term_id: '2026FA', seq: '001', hours: 3 }],
  },
};
const plan = {
  version: 1, catalog_year: '2026-27', focus_term: '2027SP',
  programs: ['core', 'major-computer-science-bs', 'minor-history'],
  terms: [
    { id: 'TR', courses: [{ code: 'ENG 1113', grade: 'A', note: 'Private transfer note' }] },
    { id: '2026FA', courses: [{ code: 'HIST 1013', seq: '001', grade: 'B' }] },
    { id: '2027SP', courses: [{ code: 'MATH 1003', seq: '001', grade: 'A', note: 'Private current note' }, { code: 'PHYS 2030', seq: '25', hours: 0 }] },
  ],
  unplaced: [{ code: 'CSCI 1044', note: 'Private unplaced note' }],
};
const roundTrip = async (p, source = offerings) => restoreShare(await decodeShare(await encodeShare(p, source)), source);

test('shares only the focused schedule and programs, without grades, notes, or academic history', async () => {
  const restored = await roundTrip(plan);
  assert.equal(restored.catalog_year, '2026-27');
  assert.equal(restored.focus_term, '2027SP');
  assert.deepEqual(restored.programs, plan.programs);
  assert.deepEqual(restored.terms, [
    { id: 'TR', courses: [] },
    { id: '2027SP', courses: [{ code: 'MATH 1003', seq: '001' }, { code: 'PHYS 2030', seq: '25' }] },
  ]);
  assert.deepEqual(restored.unplaced, []);
  const header = await decodeShare(await encodeShare(plan, offerings));
  assert.deepEqual(header.entries, ['001', '25']); // regular classes need only their section numbers
});

test('preserves custom meetings, fractional/zero hours, unselected courses, and explicit hours overrides', async () => {
  const courses = [
    { code: 'MATH 1003', seq: '001', hours: 2 },
    { code: 'MUAP 1080.5', custom: true, hours: 0.5, title: 'Piano · étude', days: 'TTH', start: 450, end: 500 },
    { code: 'LAB 1000', custom: true, hours: 0 },
    { code: 'CSCI 1044' },
  ];
  const restored = await roundTrip({ ...plan, terms: [{ id: '2027SP', courses }] });
  assert.deepEqual(restored.terms.find((t) => t.id === '2027SP').courses, courses);
});

test('never resolves a missing section from another semester or silently drops it', async () => {
  const header = await decodeShare(await encodeShare(plan, offerings));
  const source = structuredClone(offerings);
  source.courses['MATH 1003'] = [];
  assert.throws(() => restoreShare(header, source), /Section 001 is missing/);
  assert.throws(() => restoreShare(header, { ...source, semesters: [] }), /semester is no longer/);
});

test('rejects a sequence that identifies two different courses in the shared semester', async () => {
  const header = await decodeShare(await encodeShare(plan, offerings));
  const source = structuredClone(offerings);
  source.courses['HIST 1013'].push({ term_id: '2027SP', seq: '001', hours: 3 });
  assert.throws(() => restoreShare(header, source), /Section 001 is missing or ambiguous/);
});

test('keeps the code for picks that were already missing from the schedule when shared', async () => {
  const courses = [{ code: 'CSCI 1044', seq: '999', hours: 4 }];
  const restored = await roundTrip({ ...plan, terms: [{ id: '2027SP', courses }] });
  assert.deepEqual(restored.terms.find((t) => t.id === '2027SP').courses, courses);
});

test('works without browser compression support', async () => {
  const original = globalThis.CompressionStream;
  try {
    globalThis.CompressionStream = undefined;
    const restored = await roundTrip(plan);
    assert.deepEqual(restored.terms.find((t) => t.id === '2027SP').courses, [
      { code: 'MATH 1003', seq: '001' }, { code: 'PHYS 2030', seq: '25' },
    ]);
  } finally {
    globalThis.CompressionStream = original;
  }
});
