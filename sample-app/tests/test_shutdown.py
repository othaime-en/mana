import logging
import signal
from unittest.mock import Mock, patch

import pytest

from src.utils import shutdown
from src.utils.shutdown import GracefulShutdown


@pytest.fixture
def manager():
    with patch("src.utils.shutdown.signal.signal") as install, \
         patch("src.utils.shutdown.atexit.register") as register:
        instance = GracefulShutdown()
        instance.install = install
        instance.register_exit_hook = register
        yield instance


class TestGracefulShutdown:
    """Signal handling and cleanup execution"""

    def test_registers_signal_and_exit_hooks(self, manager):
        installed = {call.args[0] for call in manager.install.call_args_list}

        assert installed == {signal.SIGTERM, signal.SIGINT}
        manager.register_exit_hook.assert_called_once_with(manager._cleanup)

    def test_sigterm_runs_handlers_and_exits_cleanly(self, manager):
        handler = Mock(__name__="handler")
        manager.register_cleanup(handler)

        with pytest.raises(SystemExit) as exit_info:
            manager._handle_shutdown(signal.SIGTERM, None)

        assert exit_info.value.code == 0
        assert manager.is_shutting_down is True
        handler.assert_called_once()

    def test_second_signal_is_ignored_while_shutting_down(self, manager):
        handler = Mock(__name__="handler")
        manager.register_cleanup(handler)
        manager.is_shutting_down = True

        manager._handle_shutdown(signal.SIGINT, None)

        handler.assert_not_called()

    def test_exit_hook_after_a_signal_does_not_run_handlers_again(self, manager):
        handler = Mock(__name__="handler")
        manager.register_cleanup(handler)
        with pytest.raises(SystemExit):
            manager._handle_shutdown(signal.SIGTERM, None)

        manager._cleanup()

        handler.assert_called_once()

    def test_failing_handler_does_not_stop_the_others(self, manager, caplog):
        failing = Mock(__name__="failing", side_effect=RuntimeError("boom"))
        later = Mock(__name__="later")
        manager.register_cleanup(failing)
        manager.register_cleanup(later)

        with caplog.at_level(logging.ERROR):
            manager._cleanup()

        later.assert_called_once()
        assert "Error in cleanup handler failing" in caplog.text

    def test_cleanup_without_handlers_is_a_no_op(self, manager, caplog):
        with caplog.at_level(logging.INFO):
            manager._cleanup()

        assert "No cleanup handlers to execute" in caplog.text

    def test_unregistered_handler_is_not_called(self, manager):
        handler = Mock(__name__="handler")
        manager.register_cleanup(handler)
        manager.unregister_cleanup(handler)

        manager._cleanup()

        handler.assert_not_called()

    def test_unregistering_an_unknown_handler_is_harmless(self, manager):
        manager.unregister_cleanup(Mock(__name__="unknown"))


class TestGlobalManager:
    """The module-level manager is created once and shared"""

    def test_get_shutdown_manager_returns_one_instance(self, monkeypatch):
        monkeypatch.setattr(shutdown, "_shutdown_manager", None)
        with patch("src.utils.shutdown.signal.signal"), \
             patch("src.utils.shutdown.atexit.register"):
            first = shutdown.get_shutdown_manager()
            second = shutdown.get_shutdown_manager()

        assert first is second

    def test_register_cleanup_handler_uses_the_global_manager(self, monkeypatch):
        monkeypatch.setattr(shutdown, "_shutdown_manager", None)
        handler = Mock(__name__="handler")
        with patch("src.utils.shutdown.signal.signal"), \
             patch("src.utils.shutdown.atexit.register"):
            shutdown.register_cleanup_handler(handler)

        assert handler in shutdown._shutdown_manager.shutdown_handlers
