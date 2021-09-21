"""
Tests for the ``IStorageServer`` interface.

Note that for performance, in the future we might want the same node to be
reused across tests, so each test should be careful to generate unique storage
indexes.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from future.utils import PY2, bchr

if PY2:
    # fmt: off
    from future.builtins import filter, map, zip, ascii, chr, hex, input, next, oct, open, pow, round, super, bytes, dict, list, object, range, str, max, min  # noqa: F401
    # fmt: on

from random import Random

from testtools import skipIf

from twisted.internet.defer import inlineCallbacks

from foolscap.api import Referenceable

from allmydata.interfaces import IStorageServer
from .common_system import SystemTestMixin
from .common import AsyncTestCase


# Use random generator with known seed, so results are reproducible if tests
# are run in the same order.
_RANDOM = Random(0)


def _randbytes(length):
    # type: (int) -> bytes
    """Return random bytes string of given length."""
    return b"".join([bchr(_RANDOM.randrange(0, 256)) for _ in range(length)])


def new_storage_index():
    # type: () -> bytes
    """Return a new random storage index."""
    return _randbytes(16)


def new_secret():
    # type: () -> bytes
    """Return a new random secret (for lease renewal or cancellation)."""
    return _randbytes(32)


class IStorageServerSharedAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s shared APIs.

    ``self.storage_server`` is expected to provide ``IStorageServer``.
    """

    @inlineCallbacks
    def test_version(self):
        """
        ``IStorageServer`` returns a dictionary where the key is an expected
        protocol version.
        """
        result = yield self.storage_server.get_version()
        self.assertIsInstance(result, dict)
        self.assertIn(b"http://allmydata.org/tahoe/protocols/storage/v1", result)


class IStorageServerImmutableAPIsTestsMixin(object):
    """
    Tests for ``IStorageServer``'s immutable APIs.

    ``self.storage_server`` is expected to provide ``IStorageServer``.
    """

    @inlineCallbacks
    def test_allocate_buckets_new(self):
        """
        allocate_buckets() with a new storage index returns the matching
        shares.
        """
        (already_got, allocated) = yield self.storage_server.allocate_buckets(
            new_storage_index(),
            renew_secret=new_secret(),
            cancel_secret=new_secret(),
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        self.assertEqual(already_got, set())
        self.assertEqual(set(allocated.keys()), set(range(5)))
        # We validate the bucket objects' interface in a later test.

    @inlineCallbacks
    @skipIf(True, "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793")
    def test_allocate_buckets_repeat(self):
        """
        allocate_buckets() with the same storage index returns the same result,
        because the shares have not been written to.

        This fails due to https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (already_got, allocated) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        (already_got2, allocated2) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            set(range(5)),
            1024,
            Referenceable(),
        )
        self.assertEqual(already_got, already_got2)
        self.assertEqual(set(allocated.keys()), set(allocated2.keys()))

    @skipIf(True, "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793")
    @inlineCallbacks
    def test_allocate_buckets_more_sharenums(self):
        """
        allocate_buckets() with the same storage index but more sharenums
        acknowledges the extra shares don't exist.

        Fails due to https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3793
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        (already_got2, allocated2) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(7)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        self.assertEqual(already_got2, set())  # none were fully written
        self.assertEqual(set(allocated2.keys()), set(range(7)))

    @inlineCallbacks
    def test_written_shares_are_allocated(self):
        """
        Shares that are fully written to show up as allocated in result from
        ``IStorageServer.allocate_buckets()``.  Partially-written or empty
        shares don't.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (_, allocated) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )

        # Bucket 1 is fully written in one go.
        yield allocated[1].callRemote("write", 0, b"1" * 1024)
        yield allocated[1].callRemote("close")

        # Bucket 2 is fully written in two steps.
        yield allocated[2].callRemote("write", 0, b"1" * 512)
        yield allocated[2].callRemote("write", 512, b"2" * 512)
        yield allocated[2].callRemote("close")

        # Bucket 0 has partial write.
        yield allocated[0].callRemote("write", 0, b"1" * 512)

        (already_got, _) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )
        self.assertEqual(already_got, {1, 2})

    @inlineCallbacks
    def test_written_shares_are_readable(self):
        """
        Shares that are fully written to can be read.

        The result is not affected by the order in which writes
        happened, only by their offsets.
        """
        storage_index, renew_secret, cancel_secret = (
            new_storage_index(),
            new_secret(),
            new_secret(),
        )
        (_, allocated) = yield self.storage_server.allocate_buckets(
            storage_index,
            renew_secret,
            cancel_secret,
            sharenums=set(range(5)),
            allocated_size=1024,
            canary=Referenceable(),
        )

        # Bucket 1 is fully written in order
        yield allocated[1].callRemote("write", 0, b"1" * 512)
        yield allocated[1].callRemote("write", 512, b"2" * 512)
        yield allocated[1].callRemote("close")

        # Bucket 2 is fully written in reverse.
        yield allocated[2].callRemote("write", 512, b"4" * 512)
        yield allocated[2].callRemote("write", 0, b"3" * 512)
        yield allocated[2].callRemote("close")

        buckets = yield self.storage_server.get_buckets(storage_index)
        self.assertEqual(set(buckets.keys()), {1, 2})

        self.assertEqual(
            (yield buckets[1].callRemote("read", 0, 1024)), b"1" * 512 + b"2" * 512
        )
        self.assertEqual(
            (yield buckets[2].callRemote("read", 0, 1024)), b"3" * 512 + b"4" * 512
        )

    @skipIf(True, "https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3801")
    def test_overlapping_writes(self):
        """
        The policy for overlapping writes is TBD:
        https://tahoe-lafs.org/trac/tahoe-lafs/ticket/3801
        """


class _FoolscapMixin(SystemTestMixin):
    """Run tests on Foolscap version of ``IStorageServer."""

    @inlineCallbacks
    def setUp(self):
        AsyncTestCase.setUp(self)
        self.basedir = "test_istorageserver/" + self.id()
        yield SystemTestMixin.setUp(self)
        yield self.set_up_nodes(1)
        self.storage_server = next(
            iter(self.clients[0].storage_broker.get_known_servers())
        ).get_storage_server()
        self.assertTrue(IStorageServer.providedBy(self.storage_server))

    @inlineCallbacks
    def tearDown(self):
        AsyncTestCase.tearDown(self)
        yield SystemTestMixin.tearDown(self)


class FoolscapSharedAPIsTests(
    _FoolscapMixin, IStorageServerSharedAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for shared ``IStorageServer`` APIs."""


class FoolscapImmutableAPIsTests(
    _FoolscapMixin, IStorageServerImmutableAPIsTestsMixin, AsyncTestCase
):
    """Foolscap-specific tests for immutable ``IStorageServer`` APIs."""
