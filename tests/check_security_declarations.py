"""Check security declarations without Odoo or a database.

Run: python tests/check_security_declarations.py
"""
import ast
import csv
import re
import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT.name


def model_declarations():
    for folder in ('models', 'wizards'):
        for path in (ROOT / folder).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not isinstance(node, ast.ClassDef):
                    continue
                if any(isinstance(base, ast.Attribute) and base.attr == 'AbstractModel'
                       for base in node.bases):
                    continue
                for statement in node.body:
                    if (isinstance(statement, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == '_name'
                                    for target in statement.targets)):
                        yield path, ast.literal_eval(statement.value)


class TestSecurityDeclarations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text(encoding='utf-8'))
        cls.data = cls.manifest['data']
        with (ROOT / 'security/ir.model.access.csv').open(encoding='utf-8', newline='') as source:
            cls.acls = list(csv.DictReader(source))
        cls.records = {
            element.attrib['id']: (filename, element.attrib.get('model'))
            for filename in cls.data if filename.endswith('.xml')
            for element in ElementTree.parse(ROOT / filename).iter()
            if 'id' in element.attrib
        }

    def test_all_security_files_are_loaded(self):
        for path in (ROOT / 'security').iterdir():
            if path.suffix in ('.xml', '.csv'):
                self.assertIn(path.relative_to(ROOT).as_posix(), self.data)

    def test_acl_models_are_declared(self):
        models = {'model_' + name.replace('.', '_') for _, name in model_declarations()}
        for row in self.acls:
            reference = row['model_id:id']
            if reference.startswith(MODULE + '.'):
                reference = reference[len(MODULE) + 1:]
            if '.' not in reference:
                self.assertIn(reference, models, row['id'])

    def test_concrete_models_have_access(self):
        acl_models = {row['model_id:id'].removeprefix(MODULE + '.') for row in self.acls}
        for path, model in model_declarations():
            self.assertIn('model_' + model.replace('.', '_'), acl_models, str(path))

    def test_acl_groups_exist_and_load_before_csv(self):
        for row in self.acls:
            group = row['group_id:id']
            if group.startswith(MODULE + '.') or (group and '.' not in group):
                group = group.removeprefix(MODULE + '.')
                self.assertIn(group, self.records, row['id'])
                filename, model = self.records[group]
                self.assertEqual(model, 'res.groups', group)
                self.assertLess(self.data.index(filename), self.data.index('security/ir.model.access.csv'))

    def test_referenced_local_groups_exist(self):
        references = []
        for folder in ('models', 'wizards'):
            for path in (ROOT / folder).glob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr in ('has_group', 'ref') and node.args
                            and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)):
                        references.append((str(path), node.args[0].value))
        for filename in self.data:
            if filename.endswith('.xml'):
                for element in ElementTree.parse(ROOT / filename).iter():
                    references.extend((filename, group.strip().lstrip('!'))
                                      for group in element.attrib.get('groups', '').split(','))
        for filename, reference in references:
            if reference.startswith(MODULE + '.group_'):
                group = reference.removeprefix(MODULE + '.')
                self.assertIn(group, self.records, f'{filename}: {reference}')
                self.assertEqual(self.records[group][1], 'res.groups', reference)

    def test_security_local_references_load_in_order(self):
        available = {'model_' + name.replace('.', '_') for _, name in model_declarations()}
        for filename in self.data:
            if not filename.endswith('.xml'):
                continue
            for element in ElementTree.parse(ROOT / filename).iter():
                if filename.startswith('security/'):
                    references = [element.attrib['ref']] if 'ref' in element.attrib else []
                    references += re.findall(r"ref\(['\"]([^'\"]+)['\"]\)", element.attrib.get('eval', ''))
                    for reference in references:
                        if reference.startswith(MODULE + '.') or '.' not in reference:
                            self.assertIn(reference.removeprefix(MODULE + '.'), available,
                                          f'{filename}: {reference}')
                if 'id' in element.attrib:
                    available.add(element.attrib['id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
