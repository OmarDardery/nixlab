"""Installer target safety and source bootstrap; no disks or bootloaders touched."""
import importlib.util
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load('nixlab_stage_iso', BASE / 'iso/stage.py')


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'target'
        self.config = self.root / 'etc/nixos/configuration.nix'
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{ ... }: { imports = [ ./hardware-configuration.nix ]; }')
        (self.config.parent / 'hardware-configuration.nix').write_text('{}')
        self.module = Path(self.temp.name) / 'firstboot.nix'
        self.module.write_text('{ ... }: {}')

    def test_preserves_generated_config_and_is_idempotent(self):
        original = self.config.read_text()
        with patch.object(stage, 'mount_type', return_value='ext4'):
            self.assertTrue(stage.stage(self.root, self.module, str(self.root)))
            self.assertFalse(stage.stage(self.root, self.module, str(self.root)))
        self.assertEqual((self.config.parent / 'nixlab-base.nix').read_text(), original)
        self.assertEqual(self.config.read_text(), stage.WRAPPER)

    def test_refuses_live_root_wrong_confirmation_and_ram_filesystems(self):
        with self.assertRaises(ValueError):
            stage.stage('/', self.module, '/')
        with self.assertRaises(ValueError):
            stage.stage(self.root, self.module, '/different')
        for kind in ('tmpfs', 'overlay', 'squashfs', 'iso9660'):
            with patch.object(stage, 'mount_type', return_value=kind), self.assertRaises(ValueError):
                stage.stage(self.root, self.module, str(self.root))
        self.assertFalse((self.config.parent / 'nixlab-base.nix').exists())

    def test_refuses_symlinks_and_conflicting_staging(self):
        self.config.rename(self.config.parent / 'other.nix')
        self.config.symlink_to(self.config.parent / 'other.nix')
        with patch.object(stage, 'mount_type', return_value='ext4'), self.assertRaises(ValueError):
            stage.stage(self.root, self.module, str(self.root))
        self.config.unlink(); self.config.write_text('{}')
        (self.config.parent / 'nixlab-base.nix').write_text('keep')
        with patch.object(stage, 'mount_type', return_value='ext4'), self.assertRaises(ValueError):
            stage.stage(self.root, self.module, str(self.root))
        self.assertEqual((self.config.parent / 'nixlab-base.nix').read_text(), 'keep')

    def test_failed_wrapper_write_restores_base(self):
        original = self.config.read_text()
        atomic = stage.atomic
        def fail(path, content, mode=0o600):
            if path == self.config: raise OSError('write failed')
            return atomic(path, content, mode)
        with patch.object(stage, 'mount_type', return_value='ext4'), patch.object(stage, 'atomic', side_effect=fail):
            with self.assertRaises(OSError):
                stage.stage(self.root, self.module, str(self.root))
        self.assertEqual(self.config.read_text(), original)
        self.assertFalse((self.config.parent / 'nixlab-firstboot.nix').exists())


class EmbeddedSourceTests(unittest.TestCase):
    def test_live_media_conversion_is_refused_before_host_changes(self):
        installer = load('nixlab_iso_guard', BASE / 'installer/install.py')
        with patch.object(installer.os, 'geteuid', return_value=0), \
                patch.object(installer, 'Path') as paths, \
                patch.object(installer, 'choose_network') as network, \
                patch.object(installer, 'run') as run, \
                patch.object(installer.sys, 'argv', ['nixlab-install']), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            paths.return_value.exists.return_value = True
            with self.assertRaises(SystemExit) as result:
                installer.main()
        self.assertEqual(result.exception.code, 2)
        self.assertIn('live installation media', error.getvalue())
        network.assert_not_called()
        run.assert_not_called()

    def test_plain_source_becomes_local_repository_without_network_clone(self):
        installer = load('nixlab_iso_install', BASE / 'installer/install.py')
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / 'source', Path(folder) / 'target'
            source.mkdir()
            (source / 'flake.nix').write_text('{}')
            (source / 'flake.lock').write_text('{}')
            installer.copy_embedded_checkout(source, target, 'https://example.invalid/reviewed.git')
            result = subprocess.run(['git', '-C', str(target), 'ls-files'], check=True, capture_output=True, text=True)
            self.assertEqual(set(result.stdout.splitlines()), {'flake.nix', 'flake.lock'})
            self.assertTrue((target / '.git').is_dir())
