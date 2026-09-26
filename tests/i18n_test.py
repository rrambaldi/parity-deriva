"""
Tests for web/i18n.py and the built-in catalogues: a language is its texts
for the pages' English ones, checked on the way in.
"""

import html
import json
import os
import re
import shutil
import tempfile
import types
import unittest
from unittest import mock

from parity_deriva.web import i18n

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'static')


def sources():
    """
    The pages' text as the browser gets it: entities and \\u escapes read,
    a string the code joins with + one string, white space one space.
    """
    out = []
    for name in os.listdir(STATIC):
        if name.endswith(('.html', '.js')) and name != 'i18n.js':
            with open(os.path.join(STATIC, name), encoding='utf-8') as handle:
                text = handle.read()
            text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), text)
            text = html.unescape(text) if name.endswith('.html') else re.sub(
                r"""['"`]\s*\+\s*['"`]""", '', text).replace("\\'", "'")
            out.append(re.sub(r'\s+', ' ', text))
    return '\n'.join(out)


#: where the pages' code puts a text together out of pieces
JOINS = re.compile(r'\{\w+\}| · |: |\(|\)|\n| - |, |\? ')


def chunks(key):
    """
    The pieces of a text as the code writes them, the ones long enough to
    mean something: each of them is in some page, or the text is not.
    """
    return [p for p in (' '.join(part.split()).strip(' .') for part in JOINS.split(key)) if len(p) > 3]


#: texts the check cannot see whole: a plural the code builds with a ternary
#: (set${n === 1 ? '' : 's'}), a percentage put after its number
COMPOSED = {
    'Delete the {n} stopped sets? It cannot be undone.',
    'margin: n/a, {n} run has no saved trades - simulate together runs them again',
    'margin: n/a, {n} runs have no saved trades - simulate together runs them again',
    'recording {n} feed every {every} min', 'recording {n} feeds every {every} min',
    '{name} · {share}% translated', '{n} live session running', '{n} live sessions running',
    # the ramp's words go in the middle of the real money question (live.js)
    'REAL MONEY: {n} session, each risking {risk}% a trade of a capital of {capital} - in ramp, until {t} trades or {d} days. Start?',
    'REAL MONEY: {n} sessions, each risking {risk}% a trade of a capital of {capital} - in ramp, until {t} trades or {d} days. Start?',
}


class BuiltInTest(unittest.TestCase):

    def test_every_built_in_text_is_on_a_page_and_keeps_its_values(self):
        pages = sources()
        for code in i18n._codes(i18n.BUILTIN):
            held = i18n._read(os.path.join(i18n.BUILTIN, code + '.json'))
            self.assertIsNotNone(held, code)
            missing = []
            for key, value in held['texts'].items():
                self.assertIsInstance(value, str, key)
                self.assertEqual(sorted(i18n.PLACE.findall(key)), sorted(i18n.PLACE.findall(value)), key)
                if key not in COMPOSED and not all(part in pages for part in chunks(key)):
                    missing.append(key)
            self.assertEqual(missing, [], "%s: texts no page has" % code)


class UploadTest(unittest.TestCase):

    def setUp(self):
        self.builtin = tempfile.mkdtemp()
        self.data = tempfile.mkdtemp()
        for folder in (self.builtin, self.data):
            self.addCleanup(shutil.rmtree, folder, True)
        with open(os.path.join(self.builtin, 'it.json'), 'w') as handle:
            json.dump({'name': 'italiano', 'texts': {'save': 'salva', '{n} runs of {m}': '{n} run su {m}',
                                                     'close': ''}}, handle)
        patch = mock.patch.object(i18n, 'BUILTIN', self.builtin)
        patch.start()
        self.addCleanup(patch.stop)
        self.setup = types.SimpleNamespace(DATA_DIR=self.data)

    def test_a_language_is_uploaded_checked_and_laid_over_the_built_in_one(self):
        self.assertEqual(i18n.template(), ['close', 'save', '{n} runs of {m}'])
        self.assertEqual([(l['code'], l['share']) for l in i18n.languages(self.setup)],
                         [('en', 1.0), ('it', 0.667)])
        for code, body in (('en', {'texts': {}}), ('x', {'texts': {}}), ('fr', {'save': 'x'}),
                           ('fr', {'texts': {'nothing like it': 'x'}}),
                           ('fr', {'texts': {'{n} runs of {m}': '{n} runs'}})):
            with self.assertRaises(i18n.I18nError):
                i18n.check(code, body)
        i18n.save('fr', {'name': 'français', 'texts': {'save': 'enregistrer', 'close': ''}}, self.setup)
        i18n.save('it', {'name': 'italiano', 'texts': {'close': 'chiudi'}}, self.setup)
        held = i18n.languages(self.setup)
        self.assertEqual([(l['code'], l['uploaded'], l['share']) for l in held],
                         [('en', False, 1.0), ('fr', True, 0.333), ('it', True, 1.0)])
        # the upload over the built-in: both, the upload winning
        self.assertEqual(i18n.catalogue('it', self.setup)['texts'],
                         {'save': 'salva', '{n} runs of {m}': '{n} run su {m}', 'close': 'chiudi'})
        self.assertEqual(i18n.download('template', self.setup)['texts'],
                         {'close': '', 'save': '', '{n} runs of {m}': ''})
        self.assertEqual(i18n.download('fr', self.setup)['texts']['save'], 'enregistrer')
        i18n.drop('it', self.setup)
        self.assertEqual(i18n.catalogue('it', self.setup)['texts'].get('close'), None)
        with self.assertRaises(i18n.I18nError):
            i18n.catalogue('de', self.setup)


if __name__ == '__main__':
    unittest.main()
