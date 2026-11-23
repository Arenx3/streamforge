import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.settings import load_secret_key


class LoadSecretKeyTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.key_file = Path(directory.name) / "django_secret_key"

    def test_environment_takes_priority(self):
        self.key_file.write_text("generated")

        key = load_secret_key(
            {"DJANGO_SECRET_KEY": "from-env", "DJANGO_SECRET_KEY_FILE": str(self.key_file)}
        )

        self.assertEqual(key, "from-env")

    def test_empty_value_falls_back_to_the_generated_file(self):
        self.key_file.write_text("generated\n")

        key = load_secret_key(
            {"DJANGO_SECRET_KEY": "", "DJANGO_SECRET_KEY_FILE": str(self.key_file)}
        )

        self.assertEqual(key, "generated")

    def test_missing_key_stops_the_app(self):
        with self.assertRaises(ImproperlyConfigured):
            load_secret_key({})

    def test_empty_file_stops_the_app(self):
        self.key_file.write_text("")

        with self.assertRaises(ImproperlyConfigured):
            load_secret_key({"DJANGO_SECRET_KEY_FILE": str(self.key_file)})

    def test_unreadable_file_stops_the_app(self):
        with self.assertRaises(ImproperlyConfigured):
            load_secret_key({"DJANGO_SECRET_KEY_FILE": str(self.key_file)})

    def test_rejects_the_old_placeholder(self):
        with self.assertRaises(ImproperlyConfigured):
            load_secret_key({"DJANGO_SECRET_KEY": "change-me"})
