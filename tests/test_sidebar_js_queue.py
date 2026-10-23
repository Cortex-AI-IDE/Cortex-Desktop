"""The throttled JS batch must survive a dead renderer.

Reported symptom this guards: the Explorer stops auto-refreshing and only a
manual Refresh fixes it, with nothing in the log. The flush path used to empty
_js_pending_calls into a local, find the view dead, and return, so the refresh
was neither delivered nor re-sent on reload.
"""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from src.ui.components.sidebar_bridge import SidebarBridge


class _FakePage:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def runJavaScript(self, code):
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise RuntimeError(
                'wrapped C/C++ object of type QWebEnginePage has been deleted'
            )
        self.calls.append(code)


class _FakeView:
    def __init__(self, page):
        self._page = page

    def page(self):
        return self._page


class _FakeBridge:
    def __init__(self, alive=True, page=None):
        self._js_throttle_timer = object()
        self._js_pending_calls = []
        self._pending_js = []
        self._page_loaded = True
        self._web_view = _FakeView(page or _FakePage())
        self._alive = alive

    def _is_view_alive(self):
        return self._alive


def test_dead_renderer_keeps_the_batch_for_the_next_reload():
    bridge = _FakeBridge(alive=False)
    bridge._js_pending_calls = ['refreshDirs(["a"])', 'refreshGitStatus()']

    SidebarBridge._flush_js_queue(bridge)

    assert bridge._pending_js == ['refreshDirs(["a"])', 'refreshGitStatus()']
    assert bridge._js_pending_calls == []
    assert bridge._page_loaded is False
    assert bridge._js_throttle_timer is None


def test_live_renderer_delivers_and_clears_the_batch():
    page = _FakePage()
    bridge = _FakeBridge(page=page)
    bridge._js_pending_calls = ['refreshDirs(["a"])', 'refreshDirs(["b"])']

    SidebarBridge._flush_js_queue(bridge)

    assert page.calls == ['refreshDirs(["a"])', 'refreshDirs(["b"])']
    assert bridge._js_pending_calls == []
    assert bridge._pending_js == []
    assert bridge._page_loaded is True


def test_renderer_dying_mid_batch_requeues_only_the_undelivered_calls():
    page = _FakePage(fail_at=1)
    bridge = _FakeBridge(page=page)
    bridge._js_pending_calls = ['a', 'b', 'c']

    SidebarBridge._flush_js_queue(bridge)

    assert page.calls == ['a'], 'the failed call must not count as delivered'
    assert bridge._pending_js == ['b', 'c']
    assert bridge._page_loaded is False


def test_empty_batch_does_nothing():
    page = _FakePage()
    bridge = _FakeBridge(page=page)

    SidebarBridge._flush_js_queue(bridge)

    assert page.calls == []
    assert bridge._pending_js == []
    assert bridge._js_throttle_timer is None
