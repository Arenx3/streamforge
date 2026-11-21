from unittest import mock

from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError, EndpointConnectionError
from django.test import SimpleTestCase

from core.storage import (
    S3_RETRIES,
    PartialDeleteError,
    delete_prefix,
    is_transient,
    with_retries,
)

STORAGE_DOWN = EndpointConnectionError(endpoint_url="http://storage:9000")


def s3_error(code, http_status):
    return ClientError(
        {
            "Error": {"Code": code, "Message": code},
            "ResponseMetadata": {"HTTPStatusCode": http_status},
        },
        "S3Call",
    )


def upload_error_caused_by(cause):
    """boto3 raises S3UploadFailedError while handling the original ClientError."""
    try:
        try:
            raise cause
        except ClientError:
            raise S3UploadFailedError("upload failed")  # noqa: B904
    except S3UploadFailedError as error:
        return error


class IsTransientTests(SimpleTestCase):
    def test_server_and_rate_limit_errors_are_transient(self):
        for code, http_status in (
            ("InternalError", 500),
            ("ServiceUnavailable", 503),
            ("SlowDown", 503),
            ("RequestTimeout", 408),
            ("TooManyRequests", 429),
        ):
            with self.subTest(code):
                self.assertTrue(is_transient(s3_error(code, http_status)))

    def test_client_errors_are_permanent(self):
        for code, http_status in (("404", 404), ("AccessDenied", 403), ("NoSuchBucket", 404)):
            with self.subTest(code):
                self.assertFalse(is_transient(s3_error(code, http_status)))

    def test_connection_errors_are_transient(self):
        self.assertTrue(is_transient(STORAGE_DOWN))

    def test_reads_the_cause_of_a_wrapped_upload_error(self):
        self.assertFalse(is_transient(upload_error_caused_by(s3_error("AccessDenied", 403))))
        self.assertTrue(is_transient(upload_error_caused_by(s3_error("SlowDown", 503))))


@mock.patch("core.storage.time")
class WithRetriesTests(SimpleTestCase):
    def test_returns_without_retrying_on_success(self, fake_time):
        operation = mock.Mock(return_value="done")

        self.assertEqual(with_retries(operation), "done")
        operation.assert_called_once()
        fake_time.sleep.assert_not_called()

    def test_retries_until_it_succeeds(self, fake_time):
        operation = mock.Mock(side_effect=[STORAGE_DOWN, STORAGE_DOWN, "done"])

        with self.assertLogs("core.storage", level="WARNING"):
            self.assertEqual(with_retries(operation), "done")

        self.assertEqual(operation.call_count, 3)

    def test_backoff_grows_exponentially(self, fake_time):
        operation = mock.Mock(side_effect=STORAGE_DOWN)

        with (
            self.assertLogs("core.storage", level="WARNING"),
            self.assertRaises(EndpointConnectionError),
        ):
            with_retries(operation)

        delays = [call.args[0] for call in fake_time.sleep.call_args_list]
        self.assertEqual(len(delays), S3_RETRIES)
        for delay, base in zip(delays, (1, 2, 4), strict=True):
            self.assertGreaterEqual(delay, base)
            self.assertLess(delay, base + 1)

    def test_gives_up_after_three_retries(self, fake_time):
        operation = mock.Mock(side_effect=STORAGE_DOWN)

        with (
            self.assertLogs("core.storage", level="WARNING"),
            self.assertRaises(EndpointConnectionError),
        ):
            with_retries(operation)

        self.assertEqual(operation.call_count, S3_RETRIES + 1)

    def test_permanent_error_is_not_retried(self, fake_time):
        operation = mock.Mock(side_effect=s3_error("404", 404))

        with self.assertRaises(ClientError):
            with_retries(operation)

        operation.assert_called_once()
        fake_time.sleep.assert_not_called()

    def test_unrelated_errors_are_not_retried(self, fake_time):
        operation = mock.Mock(side_effect=ValueError("bug"))

        with self.assertRaises(ValueError):
            with_retries(operation)

        operation.assert_called_once()


class DeletePrefixTests(SimpleTestCase):
    def setUp(self):
        self.s3 = mock.Mock()
        self.s3.delete_objects.return_value = {}

    def stub_pages(self, *pages):
        self.s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": key} for key in keys]} if keys else {} for keys in pages
        ]

    def test_deletes_every_page(self):
        self.stub_pages(["a/1", "a/2"], ["a/3"])

        delete_prefix(self.s3, "bucket", "a/")

        self.s3.get_paginator.return_value.paginate.assert_called_once_with(
            Bucket="bucket", Prefix="a/"
        )
        deleted = [
            item["Key"]
            for call in self.s3.delete_objects.call_args_list
            for item in call.kwargs["Delete"]["Objects"]
        ]
        self.assertEqual(deleted, ["a/1", "a/2", "a/3"])

    def test_empty_prefix_deletes_nothing(self):
        self.stub_pages([])

        delete_prefix(self.s3, "bucket", "a/")

        self.s3.delete_objects.assert_not_called()

    def test_raises_if_some_objects_remain(self):
        self.stub_pages(["a/1"])
        self.s3.delete_objects.return_value = {"Errors": [{"Key": "a/1", "Code": "InternalError"}]}

        with self.assertRaises(PartialDeleteError):
            delete_prefix(self.s3, "bucket", "a/")
