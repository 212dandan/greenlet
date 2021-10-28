from __future__ import print_function
import sys
import greenlet

from . import TestCase

class SomeError(Exception):
    pass

class GreenletTracer(object):
    oldtrace = None

    def __init__(self, error_on_trace=False):
        self.actions = []
        self.error_on_trace = error_on_trace

    def __call__(self, *args):
        self.actions.append(args)
        if self.error_on_trace:
            print("Raising error on event", args, file=sys.stderr)
            raise SomeError

    def __enter__(self):
        self.oldtrace = greenlet.settrace(self)
        return self.actions

    def __exit__(self, *args):
        greenlet.settrace(self.oldtrace)


class TestGreenletTracing(TestCase):
    """
    Tests of ``greenlet.settrace()``
    """

    def test_greenlet_tracing(self):
        main = greenlet.getcurrent()
        def dummy():
            pass
        def dummyexc():
            raise SomeError()

        with GreenletTracer() as actions:
            g1 = greenlet.greenlet(dummy)
            g1.switch()
            g2 = greenlet.greenlet(dummyexc)
            self.assertRaises(SomeError, g2.switch)

        self.assertEqual(actions, [
            ('switch', (main, g1)),
            ('switch', (g1, main)),
            ('switch', (main, g2)),
            ('throw', (g2, main)),
        ])

    def test_exception_disables_tracing(self):
        main = greenlet.getcurrent()
        def dummy():
            main.switch()
        g = greenlet.greenlet(dummy)
        g.switch()
        with GreenletTracer(error_on_trace=True) as actions:
            self.assertRaises(SomeError, g.switch)
            self.assertEqual(greenlet.gettrace(), None)

        self.assertEqual(actions, [
            ('switch', (main, g)),
        ])


class PythonTracer(object):
    oldtrace = None

    def __init__(self):
        self.actions = []

    def __call__(self, frame, event, arg):
        # Record the co_name so we have an idea what function we're in.
        self.actions.append((event, frame.f_code.co_name))

    def __enter__(self):
        self.oldtrace = sys.setprofile(self)
        return self.actions

    def __exit__(self, *args):
        sys.setprofile(self.oldtrace)

def tpt_callback():
    return 42

class TestPythonTracing(TestCase):
    """
    Tests of the interaction of ``sys.settrace()``
    with greenlet facilities.

    NOTE: Most of this is probably CPython specific.
    """

    maxDiff = None

    def test_trace_events_trivial(self):
        with PythonTracer() as actions:
            tpt_callback()
        # If we use the sys.settrace instead of setprofile, we get
        # this:

        # self.assertEqual(actions, [
        #     ('call', 'tpt_callback'),
        #     ('call', '__exit__'),
        # ])

        self.assertEqual(actions, [
            ('return', '__enter__'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('call', '__exit__'),
            ('c_call', '__exit__'),
        ])

    def _trace_switch(self, glet):
        with PythonTracer() as actions:
            glet.switch()
        return actions

    def _check_trace_events_func_already_set(self, glet):
        actions = self._trace_switch(glet)
        self.assertEqual(actions, [
            ('return', '__enter__'),
            ('c_call', '_trace_switch'),
            ('call', 'run'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('return', 'run'),
            ('c_return', '_trace_switch'),
            ('call', '__exit__'),
            ('c_call', '__exit__'),
        ])

    def test_trace_events_into_greenlet_func_already_set(self):
        def run():
            return tpt_callback()

        self._check_trace_events_func_already_set(greenlet.greenlet(run))

    def test_trace_events_into_greenlet_subclass_already_set(self):
        class X(greenlet.greenlet):
            def run(self):
                return tpt_callback()
        self._check_trace_events_func_already_set(X())

    def _check_trace_events_from_greenlet_sets_profiler(self, g, tracer):
        g.switch()
        tpt_callback()
        tracer.__exit__()
        self.assertEqual(tracer.actions, [
            ('return', '__enter__'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('return', 'run'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('call', '__exit__'),
            ('c_call', '__exit__'),
        ])


    def test_trace_events_from_greenlet_func_sets_profiler(self):
        tracer = PythonTracer()
        def run():
            tracer.__enter__()
            return tpt_callback()

        self._check_trace_events_from_greenlet_sets_profiler(greenlet.greenlet(run),
                                                             tracer)

    def test_trace_events_from_greenlet_subclass_sets_profiler(self):
        tracer = PythonTracer()
        class X(greenlet.greenlet):
            def run(self):
                tracer.__enter__()
                return tpt_callback()

        self._check_trace_events_from_greenlet_sets_profiler(X(), tracer)


    def test_trace_events_multiple_greenlets_switching(self):
        tracer = PythonTracer()

        g1 = None
        g2 = None

        def g1_run():
            tracer.__enter__()
            tpt_callback()
            g2.switch()
            tpt_callback()
            return 42

        def g2_run():
            tpt_callback()
            tracer.__exit__()
            tpt_callback()
            g1.switch()

        g1 = greenlet.greenlet(g1_run)
        g2 = greenlet.greenlet(g2_run)

        x = g1.switch()
        self.assertEqual(x, 42)
        tpt_callback() # ensure not in the trace
        self.assertEqual(tracer.actions, [
            ('return', '__enter__'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('c_call', 'g1_run'),
            ('call', 'g2_run'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('call', '__exit__'),
            ('c_call', '__exit__'),
        ])

    def test_trace_events_multiple_greenlets_switching_siblings(self):
        # Like the first version, but get both greenlets running first
        # as "siblings" and then establish the tracing.
        tracer = PythonTracer()

        g1 = None
        g2 = None

        def g1_run():
            greenlet.getcurrent().parent.switch()
            tracer.__enter__()
            tpt_callback()
            g2.switch()
            tpt_callback()
            return 42

        def g2_run():
            greenlet.getcurrent().parent.switch()

            tpt_callback()
            tracer.__exit__()
            tpt_callback()
            g1.switch()

        g1 = greenlet.greenlet(g1_run)
        g2 = greenlet.greenlet(g2_run)

        # Start g1
        g1.switch()
        # And it immediately returns control to us.
        # Start g2
        g2.switch()
        # Which also returns. Now kick of the real part of the
        # test.
        x = g1.switch()
        self.assertEqual(x, 42)

        tpt_callback() # ensure not in the trace
        self.assertEqual(tracer.actions, [
            ('return', '__enter__'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('c_call', 'g1_run'),
            ('call', 'tpt_callback'),
            ('return', 'tpt_callback'),
            ('call', '__exit__'),
            ('c_call', '__exit__'),
        ])
