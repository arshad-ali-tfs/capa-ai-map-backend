import hashlib
import json

import pytest

from capa_map.bootstrap import initialize_bundled_source, verify_bundle, verify_python


@pytest.mark.parametrize('version', [(3, 11, 5), (3, 11, 9), (3, 11, 18)])
def test_supported_python_patch_versions(version):
    assert verify_python(version, 64)['python'] == '.'.join(map(str, version))


@pytest.mark.parametrize('version,bits', [((3, 11, 0), 64), ((3, 11, 4), 64), ((3, 10, 16), 64),
                                          ((3, 12, 0), 64), ((3, 11, 9), 32)])
def test_old_python_patches_other_minors_and_32_bit_are_rejected(version, bits):
    with pytest.raises(ValueError, match='recreate .venv'):
        verify_python(version, bits)


def bundle(root, content=b'fictional model bytes'):
    path = root / 'data/model.bin'
    path.parent.mkdir()
    path.write_bytes(content)
    manifest = {'embedding_model': 'fixture', 'embedding_revision': 'fixture-revision',
                'files': [{'path': 'data/model.bin', 'size_bytes': len(content),
                           'sha256': hashlib.sha256(content).hexdigest()}]}
    (root / 'runtime-assets.json').write_text(json.dumps(manifest), encoding='utf-8')
    return path


def test_complete_bundle_is_verified_without_loading_models(tmp_path):
    bundle(tmp_path)
    result = verify_bundle(tmp_path)
    assert result['verified_files'] == 1
    assert result['embedding_revision'] == 'fixture-revision'


@pytest.mark.parametrize('failure', ['missing', 'pointer', 'changed'])
def test_incomplete_or_changed_bundle_is_rejected_with_recovery_steps(tmp_path, failure):
    path = bundle(tmp_path)
    if failure == 'missing':
        path.unlink()
    elif failure == 'pointer':
        path.write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:example\nsize 22\n')
    else:
        path.write_bytes(b'changed model bytes!!')
    with pytest.raises(ValueError, match='git lfs pull'):
        verify_bundle(tmp_path)


def test_manifest_cannot_read_outside_checkout(tmp_path):
    bundle(tmp_path)
    manifest_path = tmp_path / 'runtime-assets.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'][0]['path'] = '../outside.bin'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='inside the repository'):
        verify_bundle(tmp_path)


@pytest.mark.parametrize('configured', ['', './data/sources/REPLACE_WITH_SNAPSHOT_ID.sqlite3', './data/sources/existing.sqlite3'])
def test_first_setup_imports_source_but_rerun_preserves_existing_configuration(tmp_path, monkeypatch, configured):
    from capa_map import cli

    (tmp_path / '.env').write_text(f'CAPA_SOURCE_SNAPSHOT={configured}\nMAP_DASHBOARD_RUN_ID=existing-run\n')
    calls = []
    monkeypatch.setattr(cli, 'ROOT', tmp_path)
    monkeypatch.setattr(cli, 'import_source', lambda argv: calls.append(argv))
    initialize_bundled_source(tmp_path)
    expected = [] if configured.endswith('existing.sqlite3') else [[str(tmp_path / 'data/sources/trackwise_records.csv')]]
    assert calls == expected
    assert 'MAP_DASHBOARD_RUN_ID=existing-run' in (tmp_path / '.env').read_text()
