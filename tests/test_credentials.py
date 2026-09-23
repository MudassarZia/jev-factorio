import os
from pathlib import Path
import tempfile
import unittest

from jev_factorio.credentials import KeyStore, KeyStoreError


@unittest.skipUnless(os.name == "nt", "Windows DPAPI integration checks")
class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'private/api-key.dpapi'
        self.store = KeyStore(self.path)

    def test_windows_encrypted_round_trip_and_replacement(self):
        self.assertIsNone(self.store.load())
        value = 'dummy-only-never-sent-to-any-service-12345'
        self.store.save(value)
        self.assertNotIn(value.encode(), self.path.read_bytes())
        self.assertEqual(KeyStore(self.path).load(), value)
        self.store.save('replacement-dummy-key')
        self.assertEqual(KeyStore(self.path).load(), 'replacement-dummy-key')
        self.assertFalse(self.path.with_suffix('.tmp').exists())

    def test_invalid_input_preserves_previous_saved_key(self):
        self.store.save('valid-dummy-key')
        for value in ('', 'spaces not allowed', 'line\nbreak', 'nul\0byte'):
            with self.assertRaises(KeyStoreError):
                self.store.save(value)
            self.assertEqual(self.store.load(), 'valid-dummy-key')

    def test_corrupt_saved_blob_fails_without_returning_plaintext(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b'not-a-windows-protected-key')
        with self.assertRaises(KeyStoreError):
            self.store.load()

    def test_forget_removes_saved_copy(self):
        self.store.save('dummy-key')
        self.store.forget()
        self.store.forget()
        self.assertFalse(self.path.exists())
        self.assertIsNone(self.store.load())


if __name__ == '__main__':
    unittest.main()
