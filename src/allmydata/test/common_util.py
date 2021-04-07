from __future__ import print_function

from future.utils import PY2, bchr, binary_type
from future.builtins import str as future_str
from past.builtins import unicode

import os
import time
import signal
from random import randrange
from six.moves import StringIO
from io import (
    TextIOWrapper,
    BytesIO,
)

from twisted.internet import reactor, defer
from twisted.python import failure
from twisted.trial import unittest

from ..util.assertutil import precondition
from ..scripts import runner
from allmydata.util.encodingutil import unicode_platform, get_filesystem_encoding, argv_type, unicode_to_argv


def skip_if_cannot_represent_filename(u):
    precondition(isinstance(u, unicode))

    enc = get_filesystem_encoding()
    if not unicode_platform():
        try:
            u.encode(enc)
        except UnicodeEncodeError:
            raise unittest.SkipTest("A non-ASCII filename could not be encoded on this platform.")


def _getvalue(io):
    """
    Read out the complete contents of a file-like object.
    """
    io.seek(0)
    return io.read()


def maybe_unicode_to_argv(o):
    """Convert object to argv form if necessary."""
    if isinstance(o, unicode):
        return unicode_to_argv(o)
    return o


def run_cli_native(verb, *args, **kwargs):
    """
    Run a Tahoe-LAFS CLI command specified as bytes (on Python 2) or Unicode
    (on Python 3); basically, it accepts a native string.

    Most code should prefer ``run_cli_unicode`` which deals with all the
    necessary encoding considerations.

    :param native_str verb: The command to run.  For example, ``"create-node"``.

    :param [native_str] args: The arguments to pass to the command.  For example,
        ``("--hostname=localhost",)``.

    :param [native_str] nodeargs: Extra arguments to pass to the Tahoe executable
        before ``verb``.

    :param native_str stdin: Text to pass to the command via stdin.

    :param NoneType|str encoding: The name of an encoding which stdout and
        stderr will be configured to use.  ``None`` means stdout and stderr
        will accept bytes and unicode and use the default system encoding for
        translating between them.
    """
    nodeargs = kwargs.pop("nodeargs", [])
    encoding = kwargs.pop("encoding", None)
    verb = maybe_unicode_to_argv(verb)
    args = [maybe_unicode_to_argv(a) for a in args]
    nodeargs = [maybe_unicode_to_argv(a) for a in nodeargs]
    precondition(
        all(isinstance(arg, argv_type) for arg in [verb] + nodeargs + list(args)),
        "arguments to run_cli must be {argv_type} -- convert using unicode_to_argv".format(argv_type=argv_type),
        verb=verb,
        args=args,
        nodeargs=nodeargs,
    )
    argv = nodeargs + [verb] + list(args)
    stdin = kwargs.get("stdin", "")
    if encoding is None:
        # The original behavior, the Python 2 behavior, is to accept either
        # bytes or unicode and try to automatically encode or decode as
        # necessary.  This works okay for ASCII and if LANG is set
        # appropriately.  These aren't great constraints so we should move
        # away from this behavior.
        stdout = StringIO()
        stderr = StringIO()
    else:
        # The new behavior, the Python 3 behavior, is to accept unicode and
        # encode it using a specific encoding.  For older versions of Python
        # 3, the encoding is determined from LANG (bad) but for newer Python
        # 3, the encoding is always utf-8 (good).  Tests can pass in different
        # encodings to exercise different behaviors.
        stdout = TextIOWrapper(BytesIO(), encoding)
        stderr = TextIOWrapper(BytesIO(), encoding)
    d = defer.succeed(argv)
    d.addCallback(runner.parse_or_exit_with_explanation, stdout=stdout)
    d.addCallback(runner.dispatch,
                  stdin=StringIO(stdin),
                  stdout=stdout, stderr=stderr)
    def _done(rc):
        return 0, _getvalue(stdout), _getvalue(stderr)
    def _err(f):
        f.trap(SystemExit)
        return f.value.code, _getvalue(stdout), _getvalue(stderr)
    d.addCallbacks(_done, _err)
    return d


def run_cli_unicode(verb, argv, nodeargs=None, stdin=None, encoding=None):
    """
    Run a Tahoe-LAFS CLI command.

    :param unicode verb: The command to run.  For example, ``u"create-node"``.

    :param [unicode] argv: The arguments to pass to the command.  For example,
        ``[u"--hostname=localhost"]``.

    :param [unicode] nodeargs: Extra arguments to pass to the Tahoe executable
        before ``verb``.

    :param unicode stdin: Text to pass to the command via stdin.

    :param NoneType|str encoding: The name of an encoding to use for all
        bytes/unicode conversions necessary *and* the encoding to cause stdio
        to declare with its ``encoding`` attribute.  ``None`` means ASCII will
        be used and no declaration will be made at all.
    """
    if nodeargs is None:
        nodeargs = []
    precondition(
        all(isinstance(arg, future_str) for arg in [verb] + nodeargs + argv),
        "arguments to run_cli_unicode must be unicode",
        verb=verb,
        nodeargs=nodeargs,
        argv=argv,
    )
    codec = encoding or "ascii"
    if PY2:
        encode = lambda t: None if t is None else t.encode(codec)
    else:
        # On Python 3 command-line parsing expects Unicode!
        encode = lambda t: t
    d = run_cli_native(
        encode(verb),
        nodeargs=list(encode(arg) for arg in nodeargs),
        stdin=encode(stdin),
        encoding=encoding,
        *list(encode(arg) for arg in argv)
    )
    def maybe_decode(result):
        code, stdout, stderr = result
        if isinstance(stdout, bytes):
            stdout = stdout.decode(codec)
        if isinstance(stderr, bytes):
            stderr = stderr.decode(codec)
        return code, stdout, stderr
    d.addCallback(maybe_decode)
    return d


run_cli = run_cli_native


def parse_cli(*argv):
    # This parses the CLI options (synchronously), and returns the Options
    # argument, or throws usage.UsageError if something went wrong.
    return runner.parse_options(argv)

class DevNullDictionary(dict):
    def __setitem__(self, key, value):
        return

def insecurerandstr(n):
    return b''.join(map(bchr, map(randrange, [0]*n, [256]*n)))

def flip_bit(good, which):
    """Flip the low-order bit of good[which]."""
    if which == -1:
        pieces = good[:which], good[-1:], b""
    else:
        pieces = good[:which], good[which:which+1], good[which+1:]
    return pieces[0] + bchr(ord(pieces[1]) ^ 0x01) + pieces[2]

def flip_one_bit(s, offset=0, size=None):
    """ flip one random bit of the string s, in a byte greater than or equal to offset and less
    than offset+size. """
    precondition(isinstance(s, binary_type))
    if size is None:
        size=len(s)-offset
    i = randrange(offset, offset+size)
    result = s[:i] + bchr(ord(s[i:i+1])^(0x01<<randrange(0, 8))) + s[i+1:]
    assert result != s, "Internal error -- flip_one_bit() produced the same string as its input: %s == %s" % (result, s)
    return result


class ReallyEqualMixin(object):
    def failUnlessReallyEqual(self, a, b, msg=None):
        self.assertEqual(a, b, msg)
        # Make sure unicode strings are a consistent type. Specifically there's
        # Future newstr (backported Unicode type) vs. Python 2 native unicode
        # type. They're equal, and _logically_ the same type, but have
        # different types in practice.
        if a.__class__ == future_str:
            a = unicode(a)
        if b.__class__ == future_str:
            b = unicode(b)
        self.assertEqual(type(a), type(b), "a :: %r (%s), b :: %r (%s), %r" % (a, type(a), b, type(b), msg))


class SignalMixin(object):
    # This class is necessary for any code which wants to use Processes
    # outside the usual reactor.run() environment. It is copied from
    # Twisted's twisted.test.test_process . Note that Twisted-8.2.0 uses
    # something rather different.
    sigchldHandler = None

    def setUp(self):
        # make sure SIGCHLD handler is installed, as it should be on
        # reactor.run(). problem is reactor may not have been run when this
        # test runs.
        if hasattr(reactor, "_handleSigchld") and hasattr(signal, "SIGCHLD"):
            self.sigchldHandler = signal.signal(signal.SIGCHLD,
                                                reactor._handleSigchld)
        return super(SignalMixin, self).setUp()

    def tearDown(self):
        if self.sigchldHandler:
            signal.signal(signal.SIGCHLD, self.sigchldHandler)
        return super(SignalMixin, self).tearDown()


class StallMixin(object):
    def stall(self, res=None, delay=1):
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, res)
        return d


class Marker(object):
    pass

class FakeCanary(object):
    """For use in storage tests.
    """
    def __init__(self, ignore_disconnectors=False):
        self.ignore = ignore_disconnectors
        self.disconnectors = {}
    def notifyOnDisconnect(self, f, *args, **kwargs):
        if self.ignore:
            return
        m = Marker()
        self.disconnectors[m] = (f, args, kwargs)
        return m
    def dontNotifyOnDisconnect(self, marker):
        if self.ignore:
            return
        del self.disconnectors[marker]
    def getRemoteTubID(self):
        return None
    def getPeer(self):
        return "<fake>"


class ShouldFailMixin(object):

    def shouldFail(self, expected_failure, which, substring,
                   callable, *args, **kwargs):
        """Assert that a function call raises some exception. This is a
        Deferred-friendly version of TestCase.assertRaises() .

        Suppose you want to verify the following function:

         def broken(a, b, c):
             if a < 0:
                 raise TypeError('a must not be negative')
             return defer.succeed(b+c)

        You can use:
            d = self.shouldFail(TypeError, 'test name',
                                'a must not be negative',
                                broken, -4, 5, c=12)
        in your test method. The 'test name' string will be included in the
        error message, if any, because Deferred chains frequently make it
        difficult to tell which assertion was tripped.

        The substring= argument, if not None, must appear in the 'repr'
        of the message wrapped by this Failure, or the test will fail.
        """

        assert substring is None or isinstance(substring, (bytes, unicode))
        d = defer.maybeDeferred(callable, *args, **kwargs)
        def done(res):
            if isinstance(res, failure.Failure):
                res.trap(expected_failure)
                if substring:
                    self.failUnless(substring in str(res),
                                    "%s: substring '%s' not in '%s'"
                                    % (which, substring, str(res)))
                # return the Failure for further analysis, but in a form that
                # doesn't make the Deferred chain think that we failed.
                return [res]
            else:
                self.fail("%s was supposed to raise %s, not get '%s'" %
                          (which, expected_failure, res))
        d.addBoth(done)
        return d


class TestMixin(SignalMixin):
    def setUp(self):
        return super(TestMixin, self).setUp()

    def tearDown(self):
        self.clean_pending(required_to_quiesce=True)
        return super(TestMixin, self).tearDown()

    def clean_pending(self, dummy=None, required_to_quiesce=True):
        """
        This handy method cleans all pending tasks from the reactor.

        When writing a unit test, consider the following question:

            Is the code that you are testing required to release control once it
            has done its job, so that it is impossible for it to later come around
            (with a delayed reactor task) and do anything further?

        If so, then trial will usefully test that for you -- if the code under
        test leaves any pending tasks on the reactor then trial will fail it.

        On the other hand, some code is *not* required to release control -- some
        code is allowed to continuously maintain control by rescheduling reactor
        tasks in order to do ongoing work.  Trial will incorrectly require that
        code to clean up all its tasks from the reactor.

        Most people think that such code should be amended to have an optional
        "shutdown" operation that releases all control, but on the contrary it is
        good design for some code to *not* have a shutdown operation, but instead
        to have a "crash-only" design in which it recovers from crash on startup.

        If the code under test is of the "long-running" kind, which is *not*
        required to shutdown cleanly in order to pass tests, then you can simply
        call testutil.clean_pending() at the end of the unit test, and trial will
        be satisfied.
        """
        pending = reactor.getDelayedCalls()
        active = bool(pending)
        for p in pending:
            if p.active():
                p.cancel()
            else:
                print("WEIRDNESS! pending timed call not active!")
        if required_to_quiesce and active:
            self.fail("Reactor was still active when it was required to be quiescent.")


class TimezoneMixin(object):

    def setTimezone(self, timezone):
        def tzset_if_possible():
            # Windows doesn't have time.tzset().
            if hasattr(time, 'tzset'):
                time.tzset()

        unset = object()
        originalTimezone = os.environ.get('TZ', unset)
        def restoreTimezone():
            if originalTimezone is unset:
                del os.environ['TZ']
            else:
                os.environ['TZ'] = originalTimezone
            tzset_if_possible()

        os.environ['TZ'] = timezone
        self.addCleanup(restoreTimezone)
        tzset_if_possible()

    def have_working_tzset(self):
        return hasattr(time, 'tzset')


__all__ = [
    "TestMixin", "ShouldFailMixin", "StallMixin", "run_cli", "parse_cli",
    "DevNullDictionary", "insecurerandstr", "flip_bit", "flip_one_bit",
    "SignalMixin", "skip_if_cannot_represent_filename", "ReallyEqualMixin"
]
